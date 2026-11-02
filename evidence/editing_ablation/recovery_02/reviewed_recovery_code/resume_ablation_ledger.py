"""Recovery02: exact continuation after a pre-network Windows ledger write failure."""
import argparse
import copy
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import time

import ablation_api_client as api
from editing_ablation_tools import AblationGateway
from run_reader_repairs import run_episode, make_payload, fit_context
from resume_editing_ablation import read, verify_frozen, safe_source_difference, archive_manifest
from run_editing_ablation import ROOT, digest, hashes, remote_snapshot

EID='a0fadab880c7'
SEED='explore_15'
CONTINUATION='explore_15_ledger_recovery'
ORIGINAL_ATOMIC=api.atomic_json
RETRY_LOG=None


def resilient_json(path,value):
    # Retry only the local atomic replacement, never an HTTP request.
    for retry in range(9):
        try:return ORIGINAL_ATOMIC(path,value)
        except PermissionError:
            if RETRY_LOG is not None:
                with RETRY_LOG.open('a',encoding='utf-8') as stream:
                    stream.write(json.dumps({'utc':datetime.now(timezone.utc).isoformat(),
                        'path':str(path),'retry':retry,'error':'PermissionError','network_repeated':False})+'\n')
            if retry==8:raise
            time.sleep(min(.05*2**retry,1))


def reconstruct_request(folder,row,tools):
    payload=read(folder/'explore_14.request.json')
    messages=copy.deepcopy(payload['messages'])
    msg=read(folder/'explore_14.response.json')['choices'][0]['message']
    calls=msg.get('tool_calls') or [];assert calls
    messages.append(copy.deepcopy({k:msg[k] for k in ('role','content','tool_calls') if k in msg}))
    outputs=read(folder/'tool_outputs.json')
    last=[r for r in outputs if r['round']==14]
    assert [r['call'] for r in last]==calls
    for event in last:
        messages.append({'role':'tool','tool_call_id':event['call']['id'],
                         'content':json.dumps(event['shown_result'],ensure_ascii=False)})
    fit_context(messages,row,tools,[])
    return make_payload(messages,model=row['model'],provider=row['provider'],tools=tools)


def restore_gateway(data,row,folder):
    gateway=object.__new__(AblationGateway)
    gateway.arm=row['arm'];gateway.dataset=data;gateway.family=row['family'];gateway.task=data.tasks[row['family']]
    gateway.out=folder.resolve();gateway.source=gateway.out/'candidate'
    gateway.edits=read(folder/'applied_edits.json') if (folder/'applied_edits.json').exists() else []
    checks=sorted(int(p.name.split('_')[1]) for p in folder.glob('check_*') if p.is_dir())
    assert checks==list(range(1,len(checks)+1))
    gateway.checks=len(checks)
    return gateway


class ContinuationClient:
    def __init__(self,client,expected):self.client=client;self.expected=expected;self.used=False
    def request(self,payload,destination):
        if not self.used:
            assert destination.name==SEED and destination.parent.name==EID
            assert hashlib.sha256(json.dumps(payload,ensure_ascii=False).encode()).hexdigest()==self.expected
            self.used=True
            return self.client.request(payload,destination.with_name(CONTINUATION))
        return self.client.request(payload,destination)


def prepare(out):
    plan,data=verify_frozen(out);records=read(out/'results.json');assert len(records)==54
    incomplete=[r for r in records if not r['protocol_complete'] or r['execution_status']!='observed']
    assert len(incomplete)==1 and incomplete[0]['episode']==EID
    row=next(r for r in plan['episodes'] if r['episode']==EID);episode=out/'episodes'/EID
    assert not (episode/'result.json').exists() and not (episode/(SEED+'.request.json')).exists()
    ledger=read(out/'budget_10usd.json');events=[r for r in ledger['requests'] if r['destination']==f'episodes/{EID}/{SEED}']
    assert len(events)==1;event=events[0]
    assert event['status']=='reserved_before_network' and event['reported_usd'] is None
    assert abs(ledger['accounted_usd']-ledger['reported_usd']-event['reserved_usd'])<1e-8
    payload=reconstruct_request(episode,row,plan['tools'][row['arm']])
    assert hashlib.sha256(json.dumps(payload,ensure_ascii=False).encode()).hexdigest()==event['request_sha256']
    for r in plan['episodes'][54:]:assert not (out/'episodes'/r['episode']).exists()
    folder=out/'recovery_02';folder.mkdir(exist_ok=False)
    for name in ['results.json','summary.json','state.json','budget_10usd.json']:
        shutil.copy2(out/name,folder/(name.removesuffix('.json')+'_before.json'))
    shutil.copytree(episode,folder/'interrupted_before')
    resilient_json(folder/'reconstructed_request.json',payload)
    completed={}
    for record in records:
        if record['episode']==EID:continue
        target=out/'episodes'/record['episode']
        assert hashes(target/'candidate')==record['candidate_manifest']==archive_manifest(target/'hidden_evaluation/candidate.tar')
        completed[record['episode']]={name:digest(target/name) for name in
            ['result.json','final.json','tool_outputs.json','hidden_evaluation/score.json','hidden_evaluation/candidate.tar']}
    tests=subprocess.run([str(ROOT/'.venv/Scripts/python.exe'),'-m','unittest','discover','-s','tests','-p','test_ablation_ledger_recovery.py','-v'],
        cwd=ROOT,capture_output=True,text=True,encoding='utf-8')
    (folder/'engineering_tests.txt').write_text(tests.stdout+tests.stderr,encoding='utf-8');assert tests.returncode==0
    recovery={'created_utc':datetime.now(timezone.utc).isoformat(),'original_plan_sha256':digest(out/'plan.json'),
        'parent_recovery_plan_sha256':digest(out/'recovery_01/recovery_plan.json'),
        'parent_recovery_review_sha256':digest(out/'recovery_01/independent_recovery_review.json'),
        'cause':'Pre-network ledger atomic replacement PermissionError. Reservation was later persisted by other worker; no request archive or network call for interrupted round.',
        'script_sha256':digest(Path(__file__)),'test_sha256':digest(ROOT/'tests/test_ablation_ledger_recovery.py'),
        'ledger_before_sha256':digest(out/'budget_10usd.json'),'interrupted_episode':EID,
        'interrupted_before_hashes':hashes(episode),'completed_53':completed,
        'reconstructed_request_sha256':event['request_sha256'],'reconstructed_file_sha256':digest(folder/'reconstructed_request.json'),
        'pre_recovery_snapshot_hashes':{name:digest(folder/name) for name in ['results_before.json','summary_before.json','state_before.json','budget_10usd_before.json']},
        'remaining_schedule':plan['episodes'][54:],
        'continuation':'Resume original run_episode at round15 with identical hashed request and current source/edits/check counter. No previous action is applied twice. Completed paired partner stays unchanged.',
        'io_change':'Bounded PermissionError retries for JSON atomic writes only; HTTP retry behavior unchanged for both arms.',
        'cost_treatment':'Keep original event1146 conservative reservation without refund. Archive reconstructed request under its original destination; new actual network call uses unique explore_15_ledger_recovery destination.',
        'scientific_change':'None: same model/provider/messages/tools/caps/frozen contracts; interrupted attempt continues without reset or extra rounds.'}
    resilient_json(folder/'recovery_plan.json',recovery)
    print(json.dumps({'prepared':str(folder),'request_exactly_matches_preexisting_ledger_hash':True,'completed_preserved':53,'partial_to_continue':1,'unstarted':10,'paid_calls':0}),flush=True)


def run(out):
    global RETRY_LOG
    plan,data=verify_frozen(out);folder=out/'recovery_02';recovery=read(folder/'recovery_plan.json')
    approval=read(folder/'independent_recovery_review.json')
    assert approval['decision']=='PASS' and approval['recovery_plan_sha256']==digest(folder/'recovery_plan.json')
    assert digest(Path(__file__))==recovery['script_sha256']
    assert digest(ROOT/'tests/test_ablation_ledger_recovery.py')==recovery['test_sha256']
    assert digest(out/'budget_10usd.json')==recovery['ledger_before_sha256']
    assert digest(out/'recovery_01/recovery_plan.json')==recovery['parent_recovery_plan_sha256']
    assert digest(out/'recovery_01/independent_recovery_review.json')==recovery['parent_recovery_review_sha256']
    for name,sha in recovery['pre_recovery_snapshot_hashes'].items():assert digest(folder/name)==sha
    for eid,files in recovery['completed_53'].items():
        for name,sha in files.items():assert digest(out/'episodes'/eid/name)==sha
    episode=out/'episodes'/EID
    assert hashes(episode)==recovery['interrupted_before_hashes']==hashes(folder/'interrupted_before')
    assert recovery['remaining_schedule']==plan['episodes'][54:]
    for row in recovery['remaining_schedule']:assert not (out/'episodes'/row['episode']).exists()
    assert remote_snapshot(data,folder/'remote_assets_before_resume.json')==read(out/'remote_assets_before.json')
    api.atomic_json=resilient_json
    RETRY_LOG=folder/'atomic_write_retries.jsonl'
    seed=read(folder/'reconstructed_request.json');assert digest(folder/'reconstructed_request.json')==recovery['reconstructed_file_sha256']
    resilient_json(episode/(SEED+'.request.json'),seed)
    resilient_json(episode/(SEED+'.not_sent.json'),{'event_id':1146,'sent':False,
        'reconstructed_after_pre_network_failure':True,'evidence':recovery['reconstructed_request_sha256'],
        'conservative_reservation_retained':True})
    client=api.AblationClient(out)
    adapter=ContinuationClient(client,recovery['reconstructed_request_sha256'])
    records=read(folder/'results_before.json')
    resilient_json(out/'state.json',{'status':'RESUMING_IDENTICAL_INTERRUPTED_REQUEST_THEN_FINAL_10'})
    def worker(row,resume=False):
        target=out/'episodes'/row['episode']
        def factory(task,path,**kwargs):
            return restore_gateway(data,row,path) if resume else AblationGateway(data,task,path,arm=row['arm'])
        try:
            result=run_episode(adapter if resume else client,row,target,resume=resume,gateway_factory=factory,
                               tools=plan['tools'][row['arm']],system=plan['system'])
            result.update(safe_source_difference(data,row,target))
            result.update(budget_truncated=(target/'exploration_budget_stop.json').exists(),source_policy_verdict='pending_independent_review')
        except Exception as exc:
            result={**row,'protocol_complete':False,'execution_status':'engineering_error','budget_truncated':isinstance(exc,api.BudgetLimit),
                    'error':type(exc).__name__+': '+str(exc)}
            resilient_json(target/('engineering_error_recovery02.json' if resume else 'engineering_error.json'),result)
        resilient_json(target/('campaign_result_after_ledger_recovery.json' if resume else 'campaign_result.json'),result)
        return result
    row=next(r for r in plan['episodes'] if r['episode']==EID)
    replacement=worker(row,True);records=[replacement if r['episode']==EID else r for r in records]
    resilient_json(out/'results.json',records)
    healthy=lambda:all(r['protocol_complete'] and r['execution_status']=='observed' and not r['budget_truncated'] for r in records)
    if healthy():
        with ThreadPoolExecutor(max_workers=2) as pool:
            for start in range(54,64,2):
                futures=[pool.submit(worker,r) for r in plan['episodes'][start:start+2]]
                records.extend(f.result() for f in futures);resilient_json(out/'results.json',records)
                print(json.dumps({'recorded':len(records),'planned':64,'finalized':sum(r['protocol_complete'] for r in records),
                                  'observed':sum(r['execution_status']=='observed' for r in records),'accounted_usd':client.account['accounted_usd']}),flush=True)
                if not healthy():break
    complete=len(records)==64 and healthy()
    stable=remote_snapshot(data,out/'remote_assets_after_recovery02.json')==read(out/'remote_assets_before.json')
    verify_frozen(out)
    resilient_json(out/'summary.json',{'planned':64,'recorded':len(records),'protocol_and_evaluation_complete':complete,
        'immutable_assets_unchanged':stable,'full_replay_complete':False,'independent_review_complete':False,
        'engineering_recovery':'recovery_01/recovery_plan.json','engineering_recovery_02':'recovery_02/recovery_plan.json',
        'cost':{k:v for k,v in client.account.items() if k!='requests'},'episodes':records})
    resilient_json(out/'state.json',{'status':'AWAITING_FULL_REPLAY_AND_REVIEW' if complete and stable else 'INCOMPLETE_NO_EFFECTIVENESS_CONCLUSION'})
    print(json.dumps({'recorded':len(records),'complete':complete,'immutable_assets_unchanged':stable}),flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('action',choices=['prepare','run']);parser.add_argument('out',type=Path)
    args=parser.parse_args();{'prepare':prepare,'run':run}[args.action](args.out.resolve())
