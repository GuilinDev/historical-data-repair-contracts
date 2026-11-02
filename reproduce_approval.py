"""Re-execute the unchanged frozen Node calibration in a fresh local workspace."""
import argparse,hashlib,json,runpy,shutil,subprocess,time,uuid
from pathlib import Path
read=lambda p:json.loads(p.read_text(encoding='utf-8'))
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()

def python_check(case):
    # Reuse the independent implementation of HMAC and expected-label rules,
    # without running its historical author-machine orchestration.
    code=runpy.run_path(str(case/'independent_replay/audit.py'))
    fixtures=read(case/'fixtures.json');persisted=read(case/'persisted_original_approvals.json');plan=read(case/'plan.json')
    for f,row in zip(fixtures,persisted):
        assert f['expected']==code['expected_decision'](f)
        assert row['signature']==code['sign'](row['signed'],row['producer'])
    count=0
    for variant in plan['variants']:
        folder=case/'observations'/variant
        for name in ['initial.json','fresh_process.json']:
            for row,obs in zip(persisted,read(folder/name)):
                assert obs['id']==row['id'] and obs['execution_complete']
                assert obs['accepted']==code['predict'](row,row['signature'],variant);count+=1
        reissued=read(folder/'persisted_reissued_approvals.json')
        assert len(reissued)==37
        for row,obs in zip(reissued,read(folder/'reissued_fresh_process.json')):
            assert row['signature']==code['sign'](row['verify'],'base' if variant=='upstream_base' else 'fixed')
            assert obs['id']==row['id'] and obs['execution_complete']
            assert obs['accepted']==code['predict'](row,row['signature'],variant);count+=1
    assert count==725;return count

def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',type=Path)
    parser.add_argument('--records-only',action='store_true',help='Python HMAC/label cross-check of stored observations only')
    args=parser.parse_args();root=Path(__file__).resolve().parent;case=root/'extensions/approval_signature'
    count=python_check(case)
    if args.records_only:print(json.dumps({'passed':True,'python_verified_observations':count,'api_calls':0,'node_calls':0}));return 0
    plan=read(case/'plan.json');runner=case/'frozen_runner/approval_signature_calibration.mjs'
    assert sha(runner)==plan['runner_sha256']
    out=(args.output or root/'results'/('approval_'+time.strftime('%Y%m%dT%H%M%SZ',time.gmtime())+'_'+uuid.uuid4().hex[:6])).resolve()
    out.mkdir(parents=True,exist_ok=False);workspace=out/'workspace';(workspace/'scripts').mkdir(parents=True)
    shutil.copy2(runner,workspace/'scripts'/runner.name)
    trial=workspace/'results/second_calibration/approval_signature_17493';trial.mkdir(parents=True)
    for name in ['plan.json','fixtures.json','merged_source_manifest.json']:shutil.copy2(case/name,trial/name)
    shutil.copytree(case/'executable',trial/'executable')
    result=subprocess.run(['node',str(workspace/'scripts'/runner.name),'run'],capture_output=True,text=True,encoding='utf-8',timeout=180)
    (out/'stdout.txt').write_text(result.stdout,encoding='utf-8');(out/'stderr.txt').write_text(result.stderr,encoding='utf-8')
    assert result.returncode==0,result.stderr
    # Metadata runtime version/plan SHA may differ after declared path redaction;
    # every semantic artifact and decision must be identical.
    for name in ['persisted_original_approvals.json','collision_observations.json']:assert read(trial/name)==read(case/name)
    for variant in plan['variants']:
        for path in (case/'observations'/variant).glob('*.json'):assert read(path)==read(trial/'observations'/variant/path.name)
    assert read(trial/'summary.json')['results']==read(case/'summary.json')['results']
    report={'passed':True,'scenarios':54,'variants':5,'verification_observations':725,'python_verified_observations':count,
        'node_version':read(trial/'summary.json')['node_version'],'all_semantic_artifacts_identical':True,'api_calls':0,
        'results':read(trial/'summary.json')['results'],'scope':'Synthetic signature-layer calibration of one known upstream defect; no SDK tool execution or live credentials.'}
    (out/'summary.json').write_text(json.dumps(report,indent=2),encoding='utf-8');print(json.dumps(report,indent=2));return 0

if __name__=='__main__':raise SystemExit(main())
