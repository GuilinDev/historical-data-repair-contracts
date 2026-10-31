"""Re-evaluate every published Gemma patch without a model API call."""
import argparse
import json
from pathlib import Path
import shutil
import time
import uuid

from engine.common import write_json
from engine.dataset import ResourceDataset


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--episode',action='append',help='Optional subset of episode IDs')
    parser.add_argument('--output',type=Path)
    parser.add_argument('--records-only',action='store_true',help='Check recorded denominators, without Docker or API')
    args=parser.parse_args();root=Path(__file__).resolve().parent;data=ResourceDataset(root)
    study=root/'evidence/main';record=json.loads((study/'summary.json').read_text(encoding='utf-8'))
    audit=json.loads((root/'evidence/independent_review/main_audit.json').read_text(encoding='utf-8'))
    assert record['planned']==record['recorded']==record['finalized']==record['observed_evaluations']==32
    assert audit['decision']=='PASS' and not audit['failures']
    episodes=record['episodes'];assert len({r['episode'] for r in episodes})==32
    accepted=set(audit['source_policy_accepted_episode_ids'])
    assert accepted=={r['episode'] for r in episodes if r['all_checks_passed']}
    if args.records_only:
        print(json.dumps({'planned':32,'finalized':32,'observed':32,'semantic_and_source_accepted':len(accepted),
                          'api_calls':0,'docker_calls':0,'initial_dispositions':'24 completed,8 rate-limited; same-context recoveries retained'},indent=2));return 0
    data.verify_integrity();data.installation()
    if args.episode and set(args.episode)-{r['episode'] for r in episodes}:parser.error('Unknown episode ID')
    out=(args.output or root/'results'/('main_replay_'+time.strftime('%Y%m%dT%H%M%SZ',time.gmtime())+'_'+uuid.uuid4().hex[:6])).resolve()
    out.mkdir(parents=True,exist_ok=False);rows=[]
    for original in episodes:
        identity=original['episode'];task=original['family']
        if args.episode and identity not in args.episode:continue
        case=out/identity;case.mkdir();candidate=case/'candidate'
        shutil.copytree(data.bundle/'packages'/task/'base',candidate)
        patch=study/'patches'/identity
        manifest=json.loads((patch/'patch_manifest.json').read_text(encoding='utf-8'))
        for name in manifest['changed_paths']:
            if name not in data.tasks[task]['allowed']+['tests/test_regression.py']:raise ValueError('Unauthorized model overlay path')
            target=candidate/name;target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(patch/name,target)
        score=data.evaluate(task,candidate,'all.json',case/'evaluation')
        observed=json.loads((case/'evaluation/observations.json').read_text(encoding='utf-8'))
        complete=score['execution_status']=='observed' and len({r['id'] for r in observed['records']})==data.tasks[task]['cases']
        passed=complete and score['passed']==score['n']
        row={'episode':identity,'task':task,'model':original['model'],'provider':original['provider'],
             'complete':complete,'passed':score['passed'],'n':score['n'],'full_semantic_accept':passed,
             'recorded_full_semantic_accept':original['all_checks_passed'],'decision_matches':complete and passed==original['all_checks_passed']}
        rows.append(row);write_json(out/'progress.json',rows);print(json.dumps(row),flush=True)
    summary={'passed':all(r['decision_matches'] for r in rows),'episodes':len(rows),'observations':sum(r['n'] for r in rows),
             'full_semantic_acceptances':sum(r['full_semantic_accept'] for r in rows),'rows':rows,'api_calls':0,'gpu':False,
             'interpretation':'Fresh execution of published patch bytes. Compare complete-patch decisions. Broken numeric-code readers have process-dependent partial-case counts. This replay does not generate new model responses or replace independent source-policy review.'}
    write_json(out/'summary.json',summary);print(json.dumps({k:v for k,v in summary.items() if k!='rows'},indent=2))
    return 0 if summary['passed'] else 1


if __name__=='__main__':raise SystemExit(main())
