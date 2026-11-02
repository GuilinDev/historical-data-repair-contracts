"""Close the independent audit only after full replay and separate source review."""
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

OUT=Path(__file__).resolve().parent
ROOT=OUT.parents[2]
sys.path.insert(0,str(ROOT/'scripts'))
from resource_task_tools import ResourceDataset
from run_editing_ablation import DATA


def read(path):return json.loads(path.read_text(encoding='utf-8'))
def digest(path):return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    evidence=read(OUT/'independent_evidence_audit.json');summary=read(OUT/'summary.json')
    replay=read(OUT/'replay/summary.json');policy=read(OUT/'source_review/independent_source_policy.json')
    mechanics=read(OUT/'independent_edit_mechanics.json');analysis=read(OUT/'analysis.json')
    index=read(OUT/'source_review/outcome_masked_patch_index.json')
    assert digest(OUT/'summary.json')==evidence['summary_sha256']
    assert digest(OUT/'budget_10usd.json')==evidence['ledger_sha256']
    assert replay['complete'] and replay['all_full_acceptances_consistent'] and replay['immutable_assets_unchanged'] and replay['api_ledger_unchanged']
    assert policy['review_complete'] and policy['plan_sha256']==evidence['plan_sha256']==replay['plan_sha256']
    assert policy['review_index_sha256']==digest(OUT/'source_review/outcome_masked_patch_index.json')
    assert len(policy['decisions'])==len({r['episode'] for r in policy['decisions']})==64
    decisions={r['episode']:r for r in policy['decisions']};rows={r['episode']:r for r in summary['episodes']}
    replay_rows={r['episode']:r for r in replay['episodes']}
    assert set(rows)==set(decisions)==set(replay_rows)=={r['episode'] for r in evidence['episodes']}
    for entry in index:assert digest(OUT/'source_review'/entry['diff'])==entry['diff_sha256']
    data=ResourceDataset(DATA);counts=defaultdict(Counter);lineages=defaultdict(Counter);drift=[];syntax=defaultdict(Counter)
    checked=[];replayed_observations=0
    for proof in evidence['episodes']:
        eid=proof['episode'];row=rows[eid];folder=OUT/'episodes'/eid
        assert digest(folder/'final.json')==proof['final_sha256']
        assert digest(folder/'tool_outputs.json')==proof['tool_journal_sha256']
        original=read(folder/'hidden_evaluation/score.json')
        assert digest(folder/'hidden_evaluation/score.json')==proof['score_sha256']
        score=read(OUT/'replay'/eid/'score.json');observed=read(OUT/'replay'/eid/'observations.json')
        recomputed=data.judge(row['family'],observed,'all.json')
        assert all(score[k]==v for k,v in recomputed.items())
        assert read(OUT/'replay'/eid/'execution.json')['exit']==0
        assert score['execution_status']=='observed' and score['n']==original['n']==row['n']
        assert score['passed']==sum(r['passed'] for r in score['records'])
        assert all(r['passed']==(r['initial_semantics_passed'] and r['restart_semantics_passed']) for r in score['records'])
        full=score['passed']==score['n'];assert full==proof['semantic_all_pass']==row['all_checks_passed']
        assert digest(OUT/'replay'/eid/'score.json')==replay_rows[eid]['score_sha256']
        assert type(decisions[eid]['source_policy_compliant']) is bool
        accepted=full and proof['actual_source_change'] and proof['only_authorized_paths'] and decisions[eid]['source_policy_compliant']
        category='PASS' if accepted else ('E' if proof['actual_source_change'] else 'U')
        arm=row['arm'];counts[arm][category]+=1;lineages[row['family']][arm]+=accepted
        bits=lambda s:[(r['id'],r['initial_semantics_passed'],r['restart_semantics_passed'],r['passed']) for r in s['records']]
        if bits(original)!=bits(score):
            drift.append({'episode':eid,'family':row['family'],'arm':arm,'original_passed':original['passed'],
                          'replay_passed':score['passed'],'n':score['n'],'original_complete_acceptance':False,'replay_complete_acceptance':False})
            assert row['family']=='jsonpickle_dtype_634' and not full
        journal=read(folder/'tool_outputs.json')
        syntax_errors=sum(event['call']['function']['name']=='apply_edits' and
            event['result'].get('tool_error','').split(':',1)[0] in ('SyntaxError','IndentationError') for event in journal)
        syntax[arm]['rejections']+=syntax_errors
        if syntax_errors:
            syntax[arm]['attempts']+=1;syntax[arm][category]+=1
        checked.append({'episode':eid,'arm':arm,'family':row['family'],'primary_accepted':accepted,'category':category,
                        'initial_score_sha256':proof['score_sha256'],'replay_score_sha256':digest(OUT/'replay'/eid/'score.json')})
        replayed_observations+=score['n']
    assert replayed_observations==2016 and len(drift)==4
    assert [r['episode'] for r in drift]==replay['record_level_decision_drift']
    assert mechanics['edit_calls_reproduced']==166 and mechanics['span_compile_failures_independently_reconstructed_by_forward_character_offsets']==49
    for arm,values in counts.items():
        assert sum(values.values())==32
        assert analysis['arms'][arm]['complete_acceptances']==values['PASS']
        assert analysis['arms'][arm]['unchanged']==values['U'] and analysis['arms'][arm]['edited_incomplete']==values['E']
        assert analysis['arms'][arm]['edit_rejections']['syntax_rejection']==syntax[arm]['rejections']
    for family,values in lineages.items():
        for arm in ['exact','span']:assert analysis['by_lineage'][family][arm]==values[arm]
        assert all(sum(r['family']==family and r['arm']==arm for r in checked)==4 for arm in ['exact','span'])
    delta=counts['span']['PASS']-counts['exact']['PASS']
    assert analysis['acceptance_difference_span_minus_exact']==delta and analysis['percentage_point_difference']==100*delta/32
    plan=read(OUT/'plan.json')
    assert digest(ROOT/'.cache/gemma_api_budget.json')==plan['prior_budget_sha256']
    assert digest(ROOT/'output/pdf/historical-data-compatibility.pdf')==plan['submitted_pdf_sha256']
    assert digest(ROOT/'publication/historical-data-repair-contracts/paper/historical-data-compatibility.pdf')==plan['submitted_pdf_sha256']
    artifacts=['plan.json','summary.json','budget_10usd.json','replay/summary.json','analysis.json',
        'source_review/independent_source_policy.json','source_review/independent_source_integrity.json',
        'source_review/outcome_masked_patch_index.json','independent_evidence_audit.json','independent_edit_mechanics.json',
        'recovery_01/recovery_plan.json','recovery_01/independent_recovery_review.json',
        'recovery_02/recovery_plan.json','recovery_02/independent_recovery_review.json',
        'independent_audit_evidence.py','independent_edit_mechanics.py','finalize_independent_audit.py']
    report={'decision':'PASS','scientific_result':'VALID_COMPLETED_DESCRIPTIVE_NEGATIVE_RESULT_FOR_THE_TESTED_SPAN_INTERFACE',
        'reviewed_utc':datetime.now(timezone.utc).isoformat(),'must_fix_remaining':[],
        'coverage':{'planned_and_completed_attempts':64,'final_reports_and_full_tool_transcripts_verified':64,
            'candidate_sources_reconstructed_from_edit_logs':64,'initial_observation_judgments_recomputed':2016,
            'replay_observation_judgments_recomputed':replayed_observations,'full_acceptance_replay_changes':0,
            'source_policy_decisions':64,'source_policy_exclusions':sum(not d['source_policy_compliant'] for d in decisions.values())},
        'primary_results':{arm:{'attempts':32,'accepted':v['PASS'],'accepted_percent':100*v['PASS']/32,
            'unchanged':v['U'],'edited_incomplete':v['E']} for arm,v in counts.items()},
        'difference_span_minus_exact':{'accepted_attempts':delta,'percentage_points':100*delta/32},
        'by_lineage':{f:{'attempts_per_arm':4,'exact':v['exact'],'span':v['span']} for f,v in sorted(lineages.items())},
        'edit_mechanics':{'all_166_calls_reproduced':True,'implementation_mismatch_identified':False,
            'all_49_span_compile_errors_reproduced_with_independent_character_offset_rendering':True,
            'syntax_rejections_and_attempt_categories':{a:dict(v) for a,v in syntax.items()},
            'interpretation':'Compilation/format errors are observed secondary outcomes, not an estimate of their causal contribution to the acceptance gap. The tested bundle requires explicit replacement newlines, original inclusive line spans and source-version hashes.'},
        'partial_replay_drift':drift,
        'cost':{'ledger_events':evidence['requests'],'actual_archived_responses':evidence['responses'],
            'reported_usd':evidence['reported_cost_usd'],'conservative_unsent_reservation_usd':evidence['conservative_unissued_reservation_usd'],
            'accounted_usd':evidence['accounted_usd'],'authorized_cap_usd':10.0,'independently_reconciled_to_archived_responses':True},
        'deployment':{'all_actual_responses_provider':'CoreWeave','all_actual_responses_model_alias':'google/gemma-4-31b-it',
            'provider_and_model_consistency_checked':True,'exact_weight_revision_verified':False},
        'engineering_recoveries':{'recovery_01':'Metadata-only correction for an allowed newly created file; completed model actions and scores were preserved.',
            'recovery_02':'Exact hash-matched continuation after a pre-network filesystem failure; no prior model action replayed; original conservative reservation retained.',
            'wall_clock_pause_disclosure_required':True},
        'submission_preservation':{'original_budget_unchanged':True,'submitted_pdf_unchanged':True,
            'publication_checkout_pdf_unchanged':True,'publication_checkout_head':'ff9bfc04987436945107ab74909fc3334470c259',
            'publication_checkout_worktree_clean_when_checked':True,'publication_or_kaggle_modified_by_reviewer':False},
        'limitations':['The sample contains eight selected, public, previously exposed lineages with four attempts per arm; attempts and individual observations are not independent defects.',
            'The whole version-checked span interface was changed, including formatting requirements; this is not an isolated causal effect of line numbers, version hashes, or one failure mode.',
            'The observed 4/32 versus 10/32 result does not establish a universal editor ranking or statistical significance.',
            'Four jsonpickle dtype attempts had partial per-record decision drift; all four remained complete-contract failures. Do not treat their partial pass totals as exactly reproducible continuous measurements.',
            'Provider pinning and consistent response aliases do not cryptographically identify a weight revision.',
            'The two reviewed engineering pauses must remain disclosed; no outcomes were discarded, restarted or chosen for success.',
            'Source review was outcome-masked, not strictly blinded, and assessed delivered-source policy separately from semantic correctness.'],
        'recommended_use':'A concise, bounded resource-diagnostic ablation may be considered. Do not claim the span intervention improved repair, replace the old baseline selectively, or turn the secondary syntax counts into a universal/mediating causal claim. Keep the submitted version untouched pending the separate revision decision.',
        'artifact_sha256':{name:digest(OUT/name) for name in artifacts},'new_api_calls_by_reviewer':0}
    (OUT/'final_independent_audit.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({k:report[k] for k in ['decision','coverage','primary_results','difference_span_minus_exact','cost','must_fix_remaining']}))


if __name__=='__main__':main()
