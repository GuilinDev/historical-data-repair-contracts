"""Final descriptive analysis gated on complete replay and independent source review."""
import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path

from ablation_api_client import atomic_json
from run_editing_ablation import hashes, digest
from replay_editing_ablation import verify_recovery_chain


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def main(out):
    summary = read(out/'summary.json'); replay = read(out/'replay/summary.json')
    policy = read(out/'source_review/independent_source_policy.json')
    plan = read(out/'plan.json'); protocol = read(out/'independent_protocol_review.json')
    plan_sha = digest(out/'plan.json')
    index = read(out/'source_review/outcome_masked_patch_index.json')
    assert protocol['decision']=='PASS' and protocol['plan_sha256']==plan_sha
    assert policy['plan_sha256']==plan_sha and replay['plan_sha256']==plan_sha
    recovery_reviews = verify_recovery_chain(out, summary)
    assert replay.get('engineering_recovery_reviews',{})==recovery_reviews
    assert policy['review_index_sha256']==digest(out/'source_review/outcome_masked_patch_index.json')
    expected = {r['episode']:r for r in plan['episodes']}
    assert len(expected)==len(plan['episodes'])==64
    for records in [summary['episodes'],replay['episodes'],policy['decisions'],index]:
        assert len(records)==64 and len({r['episode'] for r in records})==64
        assert {r['episode'] for r in records}==set(expected)
    for record in summary['episodes']:
        assert all(record[k]==v for k,v in expected[record['episode']].items())
        assert hashes(out/'episodes'/record['episode']/'candidate')==record['candidate_manifest']
    for record in index:
        assert digest(out/'source_review'/record['diff'])==record['diff_sha256']
    assert summary['protocol_and_evaluation_complete'] and summary['immutable_assets_unchanged']
    assert replay['complete'] and replay['all_full_acceptances_consistent'] and replay['immutable_assets_unchanged'] and replay['api_ledger_unchanged']
    assert policy['review_complete'] is True
    assert all(type(r['source_policy_compliant']) is bool for r in policy['decisions'])
    decisions = {r['episode']:r for r in policy['decisions']}
    assert len(decisions)==64 and set(decisions)=={r['episode'] for r in summary['episodes']}
    ledger = read(out/'budget_10usd.json')
    destinations = [r['destination'] for r in ledger['requests']]
    assert len(destinations)==len(set(destinations))
    assert {r['id'] for r in ledger['requests']}==set(range(len(destinations)))
    assert {str(p.relative_to(out)).replace('\\','/') for p in out.glob('episodes/*/*.request.json')}=={d+'.request.json' for d in destinations}
    response_costs = 0.0; unknown_reserved = 0.0
    for event in ledger['requests']:
        target=out/event['destination']; request=read(target.with_suffix('.request.json'))
        encoded=json.dumps(request,ensure_ascii=False).encode('utf-8')
        assert hashlib.sha256(encoded).hexdigest()==event['request_sha256']
        assert request['model']=='google/gemma-4-31b-it' and request['provider']['only']==['coreweave/fp4'] and request['provider']['allow_fallbacks'] is False
        response=target.with_suffix('.response.json')
        cost=read(response).get('usage',{}).get('cost') if response.exists() else None
        if cost is None:
            assert event['reported_usd'] is None and event['accounted_usd']==event['reserved_usd']
            unknown_reserved+=event['reserved_usd']
        else:
            assert abs(float(cost)-event['reported_usd'])<1e-10 and abs(float(cost)-event['accounted_usd'])<1e-10
            response_costs+=float(cost)
    assert abs(response_costs-ledger['reported_usd'])<1e-8
    assert abs(response_costs+unknown_reserved-ledger['accounted_usd'])<1e-8
    assert abs(sum(r['accounted_usd'] for r in ledger['requests'])-ledger['accounted_usd']) < 1e-8
    assert abs(sum(r['reported_usd'] or 0 for r in ledger['requests'])-ledger['reported_usd']) < 1e-8
    assert ledger['accounted_usd']<=10
    rows = []
    for record in summary['episodes']:
        eid=record['episode'];folder=out/'episodes'/eid
        journal=read(folder/'tool_outputs.json') if (folder/'tool_outputs.json').exists() else []
        errors=Counter(); edit_calls=0; successful_edits=0
        for event in journal:
            if event['call']['function']['name']!='apply_edits':continue
            edit_calls+=1
            result=event['result']
            if 'tool_error' not in result:
                successful_edits+=bool(result.get('applied_files'));continue
            message=result['tool_error']
            if 'exactly once' in message:kind='exact_match_rejection'
            elif 'Stale source_version' in message:kind='stale_version_rejection'
            elif 'Line span' in message or 'Overlapping' in message or 'coordinates' in message:kind='span_rejection'
            elif 'SyntaxError' in message or 'IndentationError' in message:kind='syntax_rejection'
            else:kind='other_edit_rejection'
            errors[kind]+=1
        primary=record['all_checks_passed'] and record['actual_source_change'] and record['only_authorized_paths'] and decisions[eid]['source_policy_compliant']
        category='PASS' if primary else ('E' if record['actual_source_change'] else 'U')
        rows.append({k:record[k] for k in ['episode','family','arm','replicate','block','passed','n','actual_source_change','all_checks_passed']} |
            {'primary_accepted':bool(primary),'category':category,'source_policy_compliant':decisions[eid]['source_policy_compliant'],
             'context_stop':(folder/'exploration_context_stop.json').exists(), 'edit_calls':edit_calls,
             'successful_edit_calls':successful_edits,'edit_rejections':dict(errors)})
    arms={};families={}
    for arm in ['exact','span']:
        selected=[r for r in rows if r['arm']==arm];assert len(selected)==32
        counts=Counter(r['category'] for r in selected);errors=Counter()
        for row in selected:errors.update(row['edit_rejections'])
        arms[arm]={'attempts':32,'complete_acceptances':counts['PASS'],'unchanged':counts['U'],'edited_incomplete':counts['E'],
            'context_stops':sum(r['context_stop'] for r in selected),'edit_rejections':dict(errors),
            'edit_calls':sum(r['edit_calls'] for r in selected),'successful_edit_calls':sum(r['successful_edit_calls'] for r in selected),
            'attempts_with_edit_rejection':sum(bool(r['edit_rejections']) for r in selected)}
    for family in sorted({r['family'] for r in rows}):
        assert all(sum(r['family']==family and r['arm']==arm for r in rows)==4 for arm in arms)
        families[family]={arm:sum(r['primary_accepted'] for r in rows if r['family']==family and r['arm']==arm) for arm in arms}
        families[family]['attempts_per_arm']=4
    result={'complete':True,'plan_sha256':hashlib.sha256((out/'plan.json').read_bytes()).hexdigest(),
        'source_policy_sha256':hashlib.sha256((out/'source_review/independent_source_policy.json').read_bytes()).hexdigest(),
        'engineering_recovery_reviews':recovery_reviews,
        'arms':arms,'by_lineage':families,'acceptance_difference_span_minus_exact':arms['span']['complete_acceptances']-arms['exact']['complete_acceptances'],
        'percentage_point_difference':100*(arms['span']['complete_acceptances']-arms['exact']['complete_acceptances'])/32,
        'accounted_usd':ledger['accounted_usd'],'reported_usd':ledger['reported_usd'],'requests':len(ledger['requests']),
        'archived_response_cost_reconciliation':True,'unknown_cost_reservations_usd':unknown_reserved,
        'record_level_replay_drift':replay['record_level_decision_drift'],'episodes':rows,
        'interpretation_limits':['Single provider/model alias; exact weight provenance not established.',
            'The intervention is the entire version-checked span interface, not line numbers alone.',
            'Eight known selected lineages, four conversations per arm per lineage; not 64 independent defects.',
            'No significance claim or extrapolated general model/editor ranking.',
            'Failures in editing, request-context guards and unchanged source are descriptive secondary outcomes; fewer errors do not imply more complete repairs.']}
    atomic_json(out/'analysis.json',result)
    print(json.dumps({k:v for k,v in result.items() if k not in ['episodes','interpretation_limits']}),flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('out',type=Path)
    main(parser.parse_args().out.resolve())
