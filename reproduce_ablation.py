"""Replay all64 published editor-study patches. Never imports an API client."""
import argparse,json,shutil,time,uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from engine.common import inventory,write_json,read_json
from engine.dataset import ResourceDataset
from verify_new_evidence import verify

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--records-only',action='store_true',help='Independent static evidence audit; no Docker/network/API')
    parser.add_argument('--episode',action='append');parser.add_argument('--output',type=Path)
    parser.add_argument('--jobs',type=int,choices=[1,2],default=2)
    args=parser.parse_args();root=Path(__file__).resolve().parent
    checked=verify(root)
    if args.records_only:print(json.dumps(checked,indent=2));return 0
    data=ResourceDataset(root);data.verify_integrity();data.installation()
    study=root/'evidence/editing_ablation';original=read_json(study/'summary.json')['episodes']
    ids={r['episode'] for r in original}
    if args.episode and not set(args.episode)<=ids:parser.error('Unknown episode')
    rows=[r for r in original if not args.episode or r['episode'] in args.episode]
    out=(args.output or root/'results'/('ablation_'+time.strftime('%Y%m%dT%H%M%SZ',time.gmtime())+'_'+uuid.uuid4().hex[:6])).resolve()
    out.mkdir(parents=True,exist_ok=False)
    def replay(row):
        eid=row['episode'];family=row['family'];folder=out/eid;candidate=folder/'candidate'
        try:
            shutil.copytree(data.bundle/'packages'/family/'base',candidate)
            patch=study/'patches'/eid;manifest=read_json(patch/'patch_manifest.json')
            for name in manifest['changed_paths']:
                assert name in data.tasks[family]['allowed']+['tests/test_regression.py']
                target=candidate/name;target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(patch/name,target)
            assert inventory(candidate)==manifest['full_candidate_manifest']==row['candidate_manifest']
            score=data.evaluate(family,candidate,'all.json',folder/'evaluation')
            complete=score['execution_status']=='observed' and score['n']==data.tasks[family]['cases']
            accepted=complete and score['passed']==score['n'] and row['actual_source_change']
            return {'episode':eid,'family':family,'arm':row['arm'],'complete':complete,'passed':score['passed'],'n':score['n'],
                'complete_acceptance':accepted,'recorded_complete_acceptance':row['all_checks_passed'] and row['actual_source_change'],
                'decision_matches':complete and accepted==(row['all_checks_passed'] and row['actual_source_change'])}
        except Exception as exc:
            return {'episode':eid,'family':family,'arm':row['arm'],'complete':False,'decision_matches':False,'error':type(exc).__name__+': '+str(exc)}
    results=[]
    with ThreadPoolExecutor(max_workers=args.jobs) as pool:
        for result in pool.map(replay,rows):
            results.append(result);write_json(out/'progress.json',results)
            if len(results)%8==0:print(json.dumps({'replayed':len(results),'planned':len(rows)}),flush=True)
    summary={'passed':len(results)==len(rows) and all(r['decision_matches'] for r in results),'episodes':len(results),
        'observations':sum(r.get('n',0) for r in results),'complete_acceptances':sum(r.get('complete_acceptance',False) for r in results),
        'by_arm':{a:sum(r.get('complete_acceptance',False) for r in results if r['arm']==a) for a in ['exact','span']},
        'api_calls':0,'gpu':False,'rows':results,
        'interpretation':'Re-executes published source patches; does not create model attempts. Source-policy review is recorded separately. Incorrect dtype readers can change partial observations across processes; complete acceptances are the replay comparison.'}
    write_json(out/'summary.json',summary);print(json.dumps({k:v for k,v in summary.items() if k!='rows'},indent=2))
    return 0 if summary['passed'] else 1

if __name__=='__main__':raise SystemExit(main())
