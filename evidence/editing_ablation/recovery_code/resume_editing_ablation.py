"""Reviewed recovery of post-evaluation metadata only; never repeat an episode."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
import subprocess
import tarfile
import hashlib

from ablation_api_client import AblationClient, BudgetLimit, atomic_json
from editing_ablation_tools import AblationGateway
from resource_task_tools import ResourceDataset
from run_reader_repairs import run_episode
from run_editing_ablation import ROOT, DATA, digest, hashes, remote_snapshot


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def archive_manifest(archive):
    with tarfile.open(archive,'r') as bundle:
        return {member.name:hashlib.sha256(bundle.extractfile(member).read()).hexdigest()
                for member in bundle.getmembers() if member.isfile()}


def safe_source_difference(data, row, folder):
    base=data.bundle/'packages'/row['family']/'base'; candidate=folder/'candidate'
    before=hashes(base);after=hashes(candidate)
    changed=sorted(p for p in set(before)|set(after) if before.get(p)!=after.get(p))
    allowed=data.tasks[row['family']]['allowed']
    def normalized(path):
        return path.read_text(encoding='utf-8') if path.exists() else None
    effective=[p for p in changed if p in allowed and normalized(base/p)!=normalized(candidate/p)]
    return {'changed_paths':changed,'normalized_source_changed_paths':effective,'actual_source_change':bool(effective),
            'only_authorized_paths':all(p in allowed+['tests/test_regression.py'] for p in changed),
            'candidate_manifest':after}


def verify_frozen(out):
    plan=read(out/'plan.json'); review=read(out/'independent_protocol_review.json')
    assert review['decision']=='PASS' and review['plan_sha256']==digest(out/'plan.json')
    for name,sha in plan['source_hashes'].items():assert digest(ROOT/'scripts'/name)==sha
    for name,sha in plan['test_hashes'].items():assert digest(ROOT/'tests'/name)==sha
    assert digest(DATA/'plan.json')==plan['data_plan_sha256']
    assert digest(DATA/'tasks.json')==plan['task_metadata_sha256']
    assert hashes(DATA/'oracle')==plan['oracle_hashes']
    data=ResourceDataset(DATA)
    assert hashes(data.bundle)==read(out/'frozen_bundle_manifest.json')
    assert digest(out/'remote_assets_before.json')==plan['remote_snapshot_sha256']
    assert digest(ROOT/'.cache/gemma_api_budget.json')==plan['prior_budget_sha256']
    assert digest(ROOT/'output/pdf/historical-data-compatibility.pdf')==plan['submitted_pdf_sha256']
    return plan,data


def prepare(out):
    plan,data=verify_frozen(out)
    folder=out/'recovery_01';folder.mkdir(exist_ok=False)
    original=read(out/'results.json');assert len(original)==40
    assert len({r['episode'] for r in original})==40
    for name in ['results.json','summary.json','state.json','budget_10usd.json']:
        shutil.copy2(out/name,folder/(name.removesuffix('.json')+'_before.json'))
    reconstructed=[];identities={}
    for expected,record in zip(plan['episodes'][:40],original):
        assert all(record[k]==v for k,v in expected.items())
        target=out/'episodes'/record['episode']; result=read(target/'result.json');final=read(target/'final.json')
        assert result['protocol_complete'] and result['execution_status']=='observed'
        assert final['protocol_error'] is None and final['last_tool_consumed']
        assert not (target/'exploration_budget_stop.json').exists()
        result.update(safe_source_difference(data,record,target))
        result.update(budget_truncated=False,source_policy_verdict='pending_independent_review')
        if record['execution_status']!='engineering_error':assert result==record
        else:
            assert record['episode']=='8ed676066380' and 'FileNotFoundError' in record['error']
        reconstructed.append(result)
        identities[record['episode']]={name:digest(target/name) for name in ['result.json','final.json','hidden_evaluation/score.json','tool_outputs.json']}
    for row in plan['episodes'][40:]:assert not (out/'episodes'/row['episode']).exists()
    atomic_json(folder/'reconstructed_first_40.json',reconstructed)
    tests=subprocess.run([str(ROOT/'.venv/Scripts/python.exe'),'-m','unittest','discover','-s','tests','-p','test_ablation_recovery.py','-v'],
                         cwd=ROOT,capture_output=True,text=True,encoding='utf-8')
    (folder/'engineering_tests.txt').write_text(tests.stdout+tests.stderr,encoding='utf-8');assert tests.returncode==0
    recovery={'created_utc':datetime.now(timezone.utc).isoformat(),'original_plan_sha256':digest(out/'plan.json'),
        'cause':'Post-evaluation metadata compared an allowed newly created file to an assumed pre-existing base file; model run and evaluation already finished.',
        'script_sha256':digest(Path(__file__)),'test_sha256':digest(ROOT/'tests/test_ablation_recovery.py'),
        'reconstructed_sha256':digest(folder/'reconstructed_first_40.json'),'ledger_before_sha256':digest(out/'budget_10usd.json'),
        'frozen_first_40':identities,'completed_episode_count':40,'remaining_schedule':plan['episodes'][40:],
        'scientific_change':'None: same tools, prompts, model, provider, caps, schedule, fixtures and acceptance definition. Absence is handled as None only in derived normalized source difference.',
        'preservation':'Original engineering error, raw result, all requests and observations remain. No completed or partial model episode is rerun.',
        'requires_independent_review_before_resuming':True}
    atomic_json(folder/'recovery_plan.json',recovery)
    print(json.dumps({'prepared_recovery':str(folder),'completed_model_attempts_preserved':40,'unstarted':24,'paid_calls':0}),flush=True)


def run(out):
    plan,data=verify_frozen(out);folder=out/'recovery_01'
    recovery=read(folder/'recovery_plan.json');review=read(folder/'independent_recovery_review.json')
    assert review['decision']=='PASS' and review['recovery_plan_sha256']==digest(folder/'recovery_plan.json')
    assert digest(Path(__file__))==recovery['script_sha256']
    assert digest(ROOT/'tests/test_ablation_recovery.py')==recovery['test_sha256']
    assert digest(out/'budget_10usd.json')==recovery['ledger_before_sha256']
    assert digest(folder/'reconstructed_first_40.json')==recovery['reconstructed_sha256']
    for name,sha in recovery['pre_recovery_snapshot_hashes'].items():assert digest(folder/name)==sha
    assert recovery['remaining_schedule']==plan['episodes'][40:]
    for eid,files in recovery['frozen_first_40'].items():
        for name,sha in files.items():assert digest(out/'episodes'/eid/name)==sha
        assert digest(out/'episodes'/eid/'hidden_evaluation/candidate.tar')==recovery['candidate_archive_hashes'][eid]
        assert archive_manifest(out/'episodes'/eid/'hidden_evaluation/candidate.tar')==hashes(out/'episodes'/eid/'candidate')
    for row in recovery['remaining_schedule']:assert not (out/'episodes'/row['episode']).exists()
    assert remote_snapshot(data,folder/'remote_assets_before_resume.json')==read(out/'remote_assets_before.json')
    results=read(folder/'reconstructed_first_40.json')
    atomic_json(out/'results.json',results)
    for record in results:
        target=out/'episodes'/record['episode']
        assert hashes(target/'candidate')==record['candidate_manifest']
        atomic_json(target/'campaign_result_after_metadata_recovery.json',record)
    client=AblationClient(out)
    atomic_json(out/'state.json',{'status':'RUNNING_REMAINING_24_AFTER_REVIEWED_METADATA_RECOVERY','started_utc':datetime.now(timezone.utc).isoformat()})
    def worker(row):
        target=out/'episodes'/row['episode']
        def factory(task,path,**kwargs):return AblationGateway(data,task,path,arm=row['arm'],**kwargs)
        try:
            result=run_episode(client,row,target,gateway_factory=factory,tools=plan['tools'][row['arm']],system=plan['system'])
            result.update(safe_source_difference(data,row,target))
            result.update(budget_truncated=(target/'exploration_budget_stop.json').exists(),source_policy_verdict='pending_independent_review')
        except Exception as exc:
            result={**row,'protocol_complete':False,'execution_status':'engineering_error','budget_truncated':isinstance(exc,BudgetLimit),
                    'error':type(exc).__name__+': '+str(exc)}
            atomic_json(target/'engineering_error.json',result)
        atomic_json(target/'campaign_result.json',result);return result
    with ThreadPoolExecutor(max_workers=2) as pool:
        for start in range(40,64,2):
            futures=[pool.submit(worker,row) for row in plan['episodes'][start:start+2]]
            results.extend(f.result() for f in futures);atomic_json(out/'results.json',results)
            print(json.dumps({'recorded':len(results),'planned':64,'finalized':sum(r['protocol_complete'] for r in results),
                              'observed':sum(r['execution_status']=='observed' for r in results),'accounted_usd':client.account['accounted_usd']}),flush=True)
            if any(r.get('budget_truncated') or r['execution_status']=='engineering_error' for r in results):break
    complete=len(results)==64 and all(r['protocol_complete'] and r['execution_status']=='observed' and not r['budget_truncated'] for r in results)
    stable=remote_snapshot(data,out/'remote_assets_after_recovery.json')==read(out/'remote_assets_before.json')
    assert digest(ROOT/'.cache/gemma_api_budget.json')==plan['prior_budget_sha256']
    assert digest(ROOT/'output/pdf/historical-data-compatibility.pdf')==plan['submitted_pdf_sha256']
    atomic_json(out/'summary.json',{'planned':64,'recorded':len(results),'protocol_and_evaluation_complete':complete,
        'immutable_assets_unchanged':stable,'full_replay_complete':False,'independent_review_complete':False,
        'engineering_recovery':'recovery_01/recovery_plan.json','cost':{k:v for k,v in client.account.items() if k!='requests'},'episodes':results})
    atomic_json(out/'state.json',{'status':'AWAITING_FULL_REPLAY_AND_REVIEW' if complete and stable else 'INCOMPLETE_NO_EFFECTIVENESS_CONCLUSION'})
    print(json.dumps({'recorded':len(results),'complete':complete,'immutable_assets_unchanged':stable}),flush=True)


def seal(out):
    folder=out/'recovery_01';recovery=read(folder/'recovery_plan.json')
    assert digest(out/'budget_10usd.json')==recovery['ledger_before_sha256']
    if not (folder/'initial_recovery_plan.json').exists():atomic_json(folder/'initial_recovery_plan.json',recovery)
    recovery['pre_recovery_snapshot_hashes']={name:digest(folder/name) for name in
        ['results_before.json','summary_before.json','state_before.json','budget_10usd_before.json']}
    recovery['candidate_archive_hashes']={}
    for eid in recovery['frozen_first_40']:
        target=out/'episodes'/eid
        assert archive_manifest(target/'hidden_evaluation/candidate.tar')==hashes(target/'candidate')
        recovery['candidate_archive_hashes'][eid]=digest(target/'hidden_evaluation/candidate.tar')
    error=out/'episodes/8ed676066380/engineering_error.json'
    shutil.copy2(error,folder/'original_engineering_error.json')
    recovery['pre_recovery_snapshot_hashes']['original_engineering_error.json']=digest(folder/'original_engineering_error.json')
    recovery['script_sha256']=digest(Path(__file__))
    atomic_json(folder/'recovery_plan.json',recovery)
    print(json.dumps({'recovery_plan_sha256':digest(folder/'recovery_plan.json'),'all_40_match_original_evaluation_archive':True,'paid_calls':0}),flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('action',choices=['prepare','seal','run']);parser.add_argument('out',type=Path)
    args=parser.parse_args();{'prepare':prepare,'seal':seal,'run':run}[args.action](args.out.resolve())
