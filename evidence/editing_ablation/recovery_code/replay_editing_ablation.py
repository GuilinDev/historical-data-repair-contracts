"""API-free replay and source-review export for the entire frozen campaign."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import difflib
import hashlib
import json
from pathlib import Path

from ablation_api_client import atomic_json
from resource_task_tools import ResourceDataset, ResourceGateway
from run_editing_ablation import DATA, ROOT, digest, hashes, remote_snapshot


def verify_recovery_chain(out, summary):
    """Bind reviewed recoveries without mistaking legitimate continuation for drift."""
    read = lambda path: json.loads(path.read_text(encoding='utf-8'))
    approved_hashes = {}
    plan_sha = digest(out/'plan.json')
    if (out/'recovery_01/recovery_plan.json').exists():
        assert summary.get('engineering_recovery') == 'recovery_01/recovery_plan.json'
    if summary.get('engineering_recovery'):
        assert summary['engineering_recovery'] == 'recovery_01/recovery_plan.json'
        folder = out/'recovery_01'; recovery_plan = folder/'recovery_plan.json'
        recovery = read(recovery_plan); approval = folder/'independent_recovery_review.json'
        approved = read(approval)
        assert approved['decision']=='PASS' and approved['recovery_plan_sha256']==digest(recovery_plan)
        assert recovery['original_plan_sha256']==plan_sha
        assert recovery['script_sha256']==digest(ROOT/'scripts/resume_editing_ablation.py')
        assert recovery['test_sha256']==digest(ROOT/'tests/test_ablation_recovery.py')
        for name,sha in recovery['pre_recovery_snapshot_hashes'].items(): assert digest(folder/name)==sha
        for eid,files in recovery['frozen_first_40'].items():
            for name,sha in files.items(): assert digest(out/'episodes'/eid/name)==sha
            assert digest(out/'episodes'/eid/'hidden_evaluation/candidate.tar')==recovery['candidate_archive_hashes'][eid]
        approved_hashes['recovery_01'] = digest(approval)
    if (out/'recovery_02/recovery_plan.json').exists():
        assert summary.get('engineering_recovery_02') == 'recovery_02/recovery_plan.json'
    if summary.get('engineering_recovery_02'):
        assert summary['engineering_recovery_02'] == 'recovery_02/recovery_plan.json'
        assert 'recovery_01' in approved_hashes
        folder = out/'recovery_02'; recovery_plan = folder/'recovery_plan.json'
        recovery = read(recovery_plan); approval = folder/'independent_recovery_review.json'
        approved = read(approval)
        assert approved['decision']=='PASS' and approved['recovery_plan_sha256']==digest(recovery_plan)
        assert recovery['original_plan_sha256']==plan_sha
        assert recovery['parent_recovery_plan_sha256']==digest(out/'recovery_01/recovery_plan.json')
        assert recovery['parent_recovery_review_sha256']==approved_hashes['recovery_01']
        assert recovery['script_sha256']==digest(ROOT/'scripts/resume_ablation_ledger.py')
        assert recovery['test_sha256']==digest(ROOT/'tests/test_ablation_ledger_recovery.py')
        for name,sha in recovery['pre_recovery_snapshot_hashes'].items(): assert digest(folder/name)==sha
        for eid,files in recovery['completed_53'].items():
            for name,sha in files.items(): assert digest(out/'episodes'/eid/name)==sha
        eid = recovery['interrupted_episode']; episode = out/'episodes'/eid
        snapshot = folder/'interrupted_before'
        assert hashes(snapshot)==recovery['interrupted_before_hashes']
        # Source and append-only journals legitimately evolve after the interruption.
        # Earlier requests/responses, observations, errors and initial prompt do not.
        evolving = {'tool_outputs.json', 'applied_edits.json'}
        for name,sha in recovery['interrupted_before_hashes'].items():
            if not name.startswith('candidate/') and name not in evolving:
                assert digest(episode/name)==sha
        old_outputs = read(snapshot/'tool_outputs.json')
        current_outputs = read(episode/'tool_outputs.json')
        assert current_outputs[:len(old_outputs)]==old_outputs
        assert all(15 <= row['round'] <= 23 for row in current_outputs[len(old_outputs):])
        old_edits = read(snapshot/'applied_edits.json') if (snapshot/'applied_edits.json').exists() else []
        current_edits = read(episode/'applied_edits.json') if (episode/'applied_edits.json').exists() else []
        assert current_edits[:len(old_edits)]==old_edits
        assert read(episode/'final.json')['rounds'] <= 24
        assert digest(folder/'reconstructed_request.json')==recovery['reconstructed_file_sha256']
        payload_hash = lambda path: hashlib.sha256(json.dumps(read(path),ensure_ascii=False).encode('utf-8')).hexdigest()
        for path in [folder/'reconstructed_request.json', episode/'explore_15.request.json',
                     episode/'explore_15_ledger_recovery.request.json']:
            assert payload_hash(path)==recovery['reconstructed_request_sha256']
        unsent = read(episode/'explore_15.not_sent.json')
        assert unsent['event_id']==1146 and unsent['sent'] is False and unsent['conservative_reservation_retained'] is True
        assert unsent['evidence']==recovery['reconstructed_request_sha256']
        assert not (episode/'explore_15.response.json').exists()
        before_ledger = read(folder/'budget_10usd_before.json'); ledger = read(out/'budget_10usd.json')
        assert ledger['requests'][:len(before_ledger['requests'])]==before_ledger['requests']
        old_event = ledger['requests'][1146]
        assert old_event['destination']==f'episodes/{eid}/explore_15'
        assert old_event['reported_usd'] is None and old_event['accounted_usd']==old_event['reserved_usd']
        prior_rows = read(folder/'results_before.json'); final_rows = {r['episode']:r for r in summary['episodes']}
        for row in prior_rows:
            if row['episode'] != eid: assert final_rows[row['episode']]==row
        approved_hashes['recovery_02'] = digest(approval)
    return approved_hashes


def main(out):
    summary = json.loads((out / 'summary.json').read_text(encoding='utf-8'))
    assert summary['protocol_and_evaluation_complete'] and len(summary['episodes']) == 64
    plan = json.loads((out / 'plan.json').read_text(encoding='utf-8'))
    review = json.loads((out / 'independent_protocol_review.json').read_text(encoding='utf-8'))
    assert review['decision']=='PASS' and review['plan_sha256']==digest(out/'plan.json')
    recovery_reviews = verify_recovery_chain(out, summary)
    schedule = plan['episodes']; recorded = summary['episodes']
    assert len({r['episode'] for r in schedule})==len({r['episode'] for r in recorded})==64
    assert [r['episode'] for r in schedule]==[r['episode'] for r in recorded]
    for expected, actual in zip(schedule,recorded):
        assert all(actual[k]==v for k,v in expected.items())
        final = json.loads((out/'episodes'/actual['episode']/'final.json').read_text(encoding='utf-8'))
        assert final['protocol_error'] is None and final['last_tool_consumed']
        assert actual['protocol_complete'] and not actual['budget_truncated']
    for name,digest_expected in plan['source_hashes'].items():
        assert digest(ROOT/'scripts'/name)==digest_expected
    for name,digest_expected in plan['test_hashes'].items():
        assert digest(ROOT/'tests'/name)==digest_expected
    assert digest(DATA/'plan.json')==plan['data_plan_sha256']
    assert digest(DATA/'tasks.json')==plan['task_metadata_sha256']
    assert hashes(DATA/'oracle')==plan['oracle_hashes']
    data = ResourceDataset(DATA)
    assert hashes(data.bundle)==json.loads((out/'frozen_bundle_manifest.json').read_text(encoding='utf-8'))
    assert digest(out/'frozen_bundle_manifest.json')==plan['bundle_manifest_sha256']
    assert digest(out/'remote_assets_before.json')==plan['remote_snapshot_sha256']
    assert remote_snapshot(data,out/'remote_assets_before_replay.json')==json.loads((out/'remote_assets_before.json').read_text(encoding='utf-8'))
    before_ledger = digest(out / 'budget_10usd.json')
    reviewdir = out / 'source_review'; reviewdir.mkdir(exist_ok=True)
    patch_records = []
    for row in summary['episodes']:
        eid = row['episode']; candidate = out / 'episodes' / eid / 'candidate'
        assert hashes(candidate) == row['candidate_manifest']
        base = data.bundle / 'packages' / row['family'] / 'base'
        differences = []
        for rel in row['changed_paths']:
            original = (base / rel).read_text(encoding='utf-8').splitlines(keepends=True) if (base / rel).exists() else []
            patched = (candidate / rel).read_text(encoding='utf-8').splitlines(keepends=True) if (candidate / rel).exists() else []
            differences.extend(difflib.unified_diff(original, patched, fromfile='base/' + rel, tofile='candidate/' + rel))
        patch = reviewdir / (eid + '.diff'); patch.write_text(''.join(differences), encoding='utf-8')
        # Outcome-masked, not a claim of strict blinding: IDs remain linkable to the plan.
        initial = json.loads((out/'episodes'/eid/'initial.json').read_text(encoding='utf-8'))
        task_input = json.loads(initial[1]['content'])
        patch_records.append({'episode': eid, 'family': row['family'], 'contract': data.tasks[row['family']]['request'],
            'allowed': data.tasks[row['family']]['allowed'], 'diff': patch.name, 'diff_sha256': digest(patch),
            'actual_source_change': row['actual_source_change'], 'only_authorized_paths': row['only_authorized_paths'],
            'frozen_system':plan['system'],'frozen_task_rules':task_input['rules']})
    atomic_json(reviewdir / 'outcome_masked_patch_index.json', patch_records)
    def replay(row):
        eid = row['episode']; folder = out / 'replay' / eid
        assert not (folder / 'score.json').exists(), 'A replay result already exists; do not overwrite evidence'
        gateway = object.__new__(ResourceGateway)
        gateway.dataset = data; gateway.family = row['family']; gateway.task = data.tasks[row['family']]
        gateway.source = out / 'episodes' / eid / 'candidate'; gateway.out = folder
        actual = gateway.evaluate('all.json', folder)
        original = json.loads((out / 'episodes' / eid / 'hidden_evaluation/score.json').read_text(encoding='utf-8'))
        def bits(score):
            return [(r['id'],r['initial_semantics_passed'],r['restart_semantics_passed'],r['passed']) for r in score['records']]
        full = actual['execution_status']=='observed' and actual['passed']==actual['n']
        return {'episode':eid,'family':row['family'],'arm':row['arm'],'observed':actual['execution_status']=='observed',
            'passed':actual['passed'],'n':actual['n'],'full_semantic_acceptance':full,
            'same_full_acceptance':full==row['all_checks_passed'],'same_record_decisions':bits(actual)==bits(original),
            'same_observation_count':actual['n']==original['n'],'candidate_unchanged':hashes(gateway.source)==row['candidate_manifest'],
            'score_sha256':digest(folder/'score.json')}
    results = []
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(replay,row) for row in summary['episodes']]
        for row,future in zip(summary['episodes'],futures):
            try:
                result = future.result()
            except Exception as exc:
                result = {'episode':row['episode'],'family':row['family'],'arm':row['arm'],
                    'observed':False,'same_full_acceptance':False,'same_record_decisions':False,
                    'error':type(exc).__name__+': '+str(exc)}
                atomic_json(out/'replay'/row['episode']/'replay_engineering_error.json',result)
            results.append(result)
            atomic_json(out / 'replay/progress.json', results)
            if len(results)%8==0:
                print(json.dumps({'replayed':len(results),'planned':64}),flush=True)
    stable = remote_snapshot(data, out/'remote_assets_after_replay.json') == json.loads((out/'remote_assets_before.json').read_text(encoding='utf-8'))
    complete = len(results)==64 and all(r['observed'] and r['same_observation_count'] and r['candidate_unchanged'] for r in results)
    consistent = all(r['same_full_acceptance'] for r in results)
    ledger_stable = digest(out/'budget_10usd.json')==before_ledger
    atomic_json(out/'replay/summary.json',{'complete':complete,'all_full_acceptances_consistent':consistent,
        'plan_sha256':digest(out/'plan.json'),'replay_script_sha256':digest(Path(__file__)),
        'engineering_recovery_review_sha256':recovery_reviews.get('recovery_01'),
        'engineering_recovery_02_review_sha256':recovery_reviews.get('recovery_02'),
        'engineering_recovery_reviews':recovery_reviews,
        'record_level_decision_drift':[r['episode'] for r in results if not r['same_record_decisions']],
        'immutable_assets_unchanged':stable,'api_ledger_unchanged':ledger_stable,
        'additional_api_calls':0,'episodes':results})
    status = 'AWAITING_INDEPENDENT_FINAL_REVIEW' if complete and stable and consistent and ledger_stable else 'REPLAY_DISCREPANCY_OR_INCOMPLETE_REQUIRES_REVIEW'
    atomic_json(out/'state.json',{'status':status})
    print(json.dumps({'replay_complete':complete,'full_acceptance_consistent':consistent,'paid_calls':0}),flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('out',type=Path)
    main(parser.parse_args().out.resolve())
