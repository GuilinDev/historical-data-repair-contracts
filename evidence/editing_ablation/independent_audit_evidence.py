"""Independent, API-free audit of the completed campaign's actual evidence.

Only Python syntax is parsed; candidate source is never executed on this host.
This audit does not infer success from tool errors or from a final self-report.
"""
from collections import Counter, defaultdict
import copy
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import sys

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[2]
sys.path.insert(0, str(ROOT/'scripts'))
from resource_task_tools import ResourceDataset
from run_editing_ablation import DATA, hashes
from run_reader_repairs import fit_context, make_payload
from resume_editing_ablation import archive_manifest, verify_frozen
from replay_editing_ablation import verify_recovery_chain


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def encoded(payload):
    return json.dumps(payload, ensure_ascii=False).encode('utf-8')


def text_after_windows_write(text):
    # The acquisition client wrote UTF-8 text on Windows with newline=None.
    raw = text.replace('\n', '\r\n')
    return raw.replace('\r\n', '\n').replace('\r', '\n')


def reconstruct_source(data, row, folder, journal):
    base = data.bundle/'packages'/row['family']/'base'
    allowed = data.tasks[row['family']]['allowed'] + ['tests/test_regression.py']
    state = {name: (base/name).read_text(encoding='utf-8') if (base/name).exists() else None for name in allowed}
    calls = 0
    for event in journal:
        if event['call']['function']['name']!='apply_edits' or 'tool_error' in event['result']:
            continue
        edits = json.loads(event['call']['function']['arguments'])['edits']
        pending = {}
        if row['arm']=='exact':
            for edit in edits:
                name = edit['path']; assert name in allowed
                old, new = edit['old'], edit['new']
                text = pending.get(name, state[name])
                if old=='':
                    assert text is None
                    changed = new
                else:
                    assert text is not None and text.count(old)==1
                    changed = text.replace(old, new, 1)
                compile(changed, name, 'exec')  # Parse only; do not execute.
                pending[name] = changed
        else:
            assert row['arm']=='span'
            grouped = defaultdict(list)
            for edit in edits:
                assert edit['path'] in allowed
                grouped[edit['path']].append(edit)
            for name, group in grouped.items():
                text = state[name]
                version = 'new' if text is None else hashlib.sha256(text.encode('utf-8')).hexdigest()
                assert all(e['source_version']==version for e in group)
                assert all(type(e['start_line']) is int and type(e['end_line']) is int for e in group)
                if text is None:
                    assert len(group)==1 and group[0]['start_line']==group[0]['end_line']==0
                    changed = group[0]['new']
                else:
                    lines = text.splitlines(keepends=True)
                    group = sorted(group, key=lambda e:e['start_line'])
                    last = 0
                    for edit in group:
                        assert 1 <= edit['start_line'] <= edit['end_line'] <= len(lines)
                        assert edit['start_line'] > last
                        last = edit['end_line']
                    for edit in reversed(group):
                        lines[edit['start_line']-1:edit['end_line']] = [edit['new']]
                    changed = ''.join(lines)
                compile(changed, name, 'exec')
                pending[name] = changed
        assert event['result']['applied_files']==list(pending)
        for name, text in pending.items():
            state[name] = text_after_windows_write(text)
        calls += 1
    for name, expected in state.items():
        path = folder/'candidate'/name
        actual = path.read_text(encoding='utf-8') if path.exists() else None
        assert actual==expected, (row['episode'], name, 'source reconstruction mismatch')
    before = hashes(base); after = hashes(folder/'candidate')
    assert after==row['candidate_manifest']==archive_manifest(folder/'hidden_evaluation/candidate.tar')
    changed = sorted(name for name in set(before)|set(after) if before.get(name)!=after.get(name))
    effective = [name for name in data.tasks[row['family']]['allowed']
                 if ((base/name).read_text(encoding='utf-8') if (base/name).exists() else None)!=state[name]]
    assert changed==row['changed_paths']
    assert sorted(effective)==row['normalized_source_changed_paths']
    assert bool(effective)==row['actual_source_change']
    assert all(name in allowed for name in changed)==row['only_authorized_paths']
    return {'successful_edit_calls_reconstructed':calls, 'actual_source_change':bool(effective),
            'only_authorized_paths':all(name in allowed for name in changed)}


def main():
    plan, data = verify_frozen(OUT)
    summary = read(OUT/'summary.json')
    assert summary['protocol_and_evaluation_complete'] and summary['immutable_assets_unchanged']
    assert len(summary['episodes'])==64
    assert [r['episode'] for r in summary['episodes']]==[r['episode'] for r in plan['episodes']]
    recoveries = verify_recovery_chain(OUT, summary)
    ledger = read(OUT/'budget_10usd.json')
    ledger_sha = digest(OUT/'budget_10usd.json')
    assert ledger['cap_usd']==10 and ledger['accounted_usd'] <= 10
    assert set(ledger['finalized_episodes'])=={r['episode'] for r in plan['episodes']}
    assert len(ledger['finalized_episodes'])==64
    events = ledger['requests']
    assert [e['id'] for e in events]==list(range(len(events)))
    assert len({e['destination'] for e in events})==len(events)
    assert {p.relative_to(OUT).as_posix() for p in OUT.glob('episodes/*/*.request.json')}=={
        e['destination']+'.request.json' for e in events}
    response_map = {}; known_cost = 0.0; unknown_cost = 0.0
    providers = Counter(); models = Counter(); no_response = []
    requests_per_episode = defaultdict(list)
    for event in events:
        target = OUT/event['destination']; request = read(target.with_suffix('.request.json'))
        assert hashlib.sha256(encoded(request)).hexdigest()==event['request_sha256']
        assert request['model']=='google/gemma-4-31b-it'
        assert request['provider']=={'only':['coreweave/fp4'],'allow_fallbacks':False,
            'require_parameters':True,'max_price':{'prompt':0.10,'completion':0.40,'request':0}}
        is_final = 'tools' not in request
        assert event['is_final']==is_final
        assert request['max_tokens']==(2048 if is_final else 4096)
        assert request['temperature']==(0 if is_final else 0.2)
        assert len(encoded(request)) <= (160000 if is_final else 120000)
        response_path = target.with_suffix('.response.json')
        raw = read(response_path) if response_path.exists() else None
        cost = raw.get('usage',{}).get('cost') if raw else None
        if raw:
            providers[raw['provider']]+=1; models[raw['model']]+=1
            assert raw['provider']=='CoreWeave' and raw['model']=='google/gemma-4-31b-it'
            assert not raw.get('error') and len(raw['choices'])==1
            response_map[event['destination']] = raw
        else:
            no_response.append(event['id'])
        if cost is None:
            assert event['reported_usd'] is None and event['accounted_usd']==event['reserved_usd']
            unknown_cost += event['reserved_usd']
        else:
            cost = float(cost); assert math.isfinite(cost) and cost >= 0
            assert abs(event['reported_usd']-cost)<1e-10 and abs(event['accounted_usd']-cost)<1e-10
            known_cost += cost
        requests_per_episode[target.parent.name].append((target,request,raw))
    assert no_response==[1146]
    assert abs(known_cost-ledger['reported_usd'])<1e-8
    assert abs(known_cost+unknown_cost-ledger['accounted_usd'])<1e-8
    episodes = []
    final_instruction = ('Exploration has ended; all executed tool outputs above are available. Submit a brief final report: '
        'what you changed, which checks you actually ran, and what remains unresolved. This final report completes your '
        'source-code delivery. Honest inability is allowed. Independent hidden checks will judge the patch. Do not request further tools.')
    for expected, row in zip(plan['episodes'], summary['episodes']):
        assert all(row[k]==v for k,v in expected.items())
        folder = OUT/'episodes'/row['episode']
        assert row['protocol_complete'] and row['execution_status']=='observed' and not row['budget_truncated']
        assert not (folder/'exploration_budget_stop.json').exists()
        messages = read(folder/'initial.json')
        assert messages[0]=={'role':'system','content':plan['system']}
        initial = json.loads(messages[1]['content'])
        assert initial['request']==data.tasks[row['family']]['request']
        assert initial['allowed_source_paths']==data.tasks[row['family']]['allowed']
        journal = read(folder/'tool_outputs.json') if (folder/'tool_outputs.json').exists() else []
        final = read(folder/'final.json')
        assert final['protocol_error'] is None and final['last_tool_consumed']
        explorations = {}; finals = []
        for target,request,raw in requests_per_episode[row['episode']]:
            if raw is None: continue
            if 'tools' not in request:
                finals.append((request,raw))
            else:
                number = int(re.match(r'explore_(\d+)',target.name).group(1))
                assert number not in explorations
                explorations[number] = (request,raw)
        assert len(finals)==1 and sorted(explorations)==list(range(len(explorations)))
        assert len(explorations)<=24
        assert all(0<=e['round']<len(explorations) for e in journal)
        revisions = []
        visible = {r['id'] for r in read(data.bundle/'fixtures'/row['family']/'visible.json')}
        for number,(request,raw) in sorted(explorations.items()):
            assert request==make_payload(messages,model=row['model'],provider=row['provider'],tools=plan['tools'][row['arm']])
            message = raw['choices'][0]['message']; calls = message.get('tool_calls') or []
            messages.append(copy.deepcopy({k:message[k] for k in ('role','content','tool_calls') if k in message}))
            outputs = [event for event in journal if event['round']==number]
            assert [e['call'] for e in outputs]==calls
            for index,event in enumerate(outputs):
                result = event['result']; serialized = encoded(result)
                if len(serialized)>24000:
                    shown = {'truncated':True,'raw_output_bytes':len(serialized),'prefix':serialized[:24000].decode('utf-8',errors='ignore')}
                else: shown=result
                assert event['shown_result']==shown and event['truncated']==(len(serialized)>24000)
                if index>=6: assert result.get('tool_error')=='ValueError: Six actions per round maximum'
                if event['call']['function']['name']=='run_checks' and 'tool_error' not in result:
                    assert all(failure['id'] in visible for failure in result['failures'])
                messages.append({'role':'tool','tool_call_id':event['call']['id'],'content':json.dumps(shown,ensure_ascii=False)})
            if not calls:
                assert number==max(explorations)
            else:
                fit_context(messages,row,plan['tools'][row['arm']],revisions)
        messages.append({'role':'user','content':final_instruction})
        request,raw = finals[0]
        assert request==make_payload(messages,True,model=row['model'],provider=row['provider'],tools=plan['tools'][row['arm']])
        choice = raw['choices'][0]; content = choice['message'].get('content')
        assert choice['finish_reason']=='stop' and isinstance(content,str) and content.strip()
        assert not choice['message'].get('tool_calls')
        assert final['response_id']==raw['id'] and final['raw']==content and final['answer']=={'report':content}
        assert not journal or any(m.get('tool_call_id')==journal[-1]['call']['id'] for m in request['messages'])
        assert final['tool_actions']==row['tool_actions']==len(journal)
        source = reconstruct_source(data,row,folder,journal)
        observations = read(folder/'hidden_evaluation/observations.json')
        score = read(folder/'hidden_evaluation/score.json')
        recomputed = data.judge(row['family'],observations,'all.json')
        assert all(score[k]==v for k,v in recomputed.items())
        assert score['execution_status']=='observed' and score['n']>0
        assert score['passed']==sum(r['passed'] for r in score['records'])
        assert all(r['passed']==(r['initial_semantics_passed'] and r['restart_semantics_passed']) for r in score['records'])
        assert (score['passed']==score['n'])==row['all_checks_passed']
        assert all(row[k]==score[k] for k in ['passed','n','groups'])
        assert read(folder/'hidden_evaluation/execution.json')['exit']==0
        episodes.append({'episode':row['episode'],'family':row['family'],'arm':row['arm'],
            'semantic_all_pass':score['passed']==score['n'],'n':score['n'],**source,
            'request_trace_verified':True,'score_sha256':digest(folder/'hidden_evaluation/score.json'),
            'final_sha256':digest(folder/'final.json'),'tool_journal_sha256':digest(folder/'tool_outputs.json')})
    assert sum(row['n'] for row in episodes)==2016
    assert digest(OUT/'budget_10usd.json')==ledger_sha
    report = {'decision':'EVIDENCE_PASS_PENDING_REPLAY_AND_SOURCE_POLICY_FINALIZATION',
        'reviewed_utc':datetime.now(timezone.utc).isoformat(),'plan_sha256':digest(OUT/'plan.json'),
        'audit_script_sha256':digest(Path(__file__)),'summary_sha256':digest(OUT/'summary.json'),
        'ledger_sha256':ledger_sha,'episodes_audited':64,'observation_judgments_recomputed':2016,
        'complete_request_and_tool_transcripts_verified':64,'final_reports_verified':64,
        'candidate_source_reconstructions_verified':64,'providers':dict(providers),'models':dict(models),
        'requests':len(events),'responses':len(response_map),'reported_cost_usd':known_cost,
        'conservative_unissued_reservation_usd':unknown_cost,'accounted_usd':ledger['accounted_usd'],
        'engineering_recovery_reviews':recoveries,'old_budget_and_submitted_pdf_unchanged':True,
        'new_api_calls_by_reviewer':0,'episodes':episodes,
        'not_final_acceptance':'Semantic all-pass is not final acceptance until independent source-policy review and full replay are checked.'}
    (OUT/'independent_evidence_audit.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({k:v for k,v in report.items() if k not in ['episodes','not_final_acceptance']}))


if __name__=='__main__':
    main()
