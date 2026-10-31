"""Statically verify published trajectory diagnoses. No API or code execution.

Run: python evidence/independent_review/clarity_revision_20261031/verify_trajectory.py
Uses only this release; reads JSON and source bytes without importing candidates.
Original archive request sizes are labeled provenance, not recomputed claims
about privacy-rewritten requests. Current exported sizes are reported separately.
"""
import collections
import hashlib
import json
from pathlib import Path


def main():
    root = Path(__file__).resolve().parents[3]
    here = Path(__file__).resolve().parent.relative_to(root)
    inputs = {}
    manifest_path = Path('evidence/export_manifest.json')
    export = json.loads((root / manifest_path).read_text(encoding='utf-8'))
    identities = {x['path']: x for x in export['files']}

    def read(path):
        path = Path(path)
        assert not path.is_absolute() and '..' not in path.parts
        content = (root / path).read_bytes()
        digest = hashlib.sha256(content).hexdigest()
        record = {'sha256': digest}
        if path.parts[0] == 'evidence':
            identity = identities.get(path.relative_to('evidence').as_posix())
            if identity:
                assert digest == identity['export_sha256'], path
                record['export_manifest_identity'] = identity
        inputs[path.as_posix()] = record
        return content

    def load(path):
        return json.loads(read(path).decode('utf-8'))

    def serialized(value):
        return json.dumps(value, ensure_ascii=False).encode('utf-8')

    expected = load(here / 'trajectory_diagnostics.json')
    tasks = load('tasks.json')
    summary = load('evidence/main/summary.json')
    rows = []
    journals = {}
    for original in summary['episodes']:
        eid = original['episode']
        task = original['family']
        ep = Path('evidence/main/episodes') / eid
        patch = Path('evidence/main/patches') / eid
        patch_manifest = load(patch / 'patch_manifest.json')
        changed = []
        candidate_paths = {}
        for path in tasks[task]['allowed']:
            base = Path('bundle/packages') / task / 'base' / path
            overlay = patch / path
            a = read(base) if (root / base).exists() else None
            source = overlay if (root / overlay).exists() else base
            b = read(source) if (root / source).exists() else None
            candidate_paths[path] = source.as_posix()
            if a != b:
                changed.append(path)
        assert sorted(changed) == sorted(patch_manifest['changed_paths'])
        journal = load(ep / 'tool_outputs.json')
        journals[eid] = journal
        errors = [(x['call']['function']['name'], x['result']['tool_error'])
                  for x in journal if isinstance(x['result'], dict) and 'tool_error' in x['result']]
        exact = sum(err == 'ValueError: Old text must match exactly once' for _, err in errors)
        utf8 = sum(name == 'search_source' and err.startswith("UnicodeDecodeError: 'utf-8'") for name, err in errors)
        stop_path = ep / 'exploration_context_stop.json'
        stop = load(stop_path) if (root / stop_path).exists() else None
        result = load(ep / 'result.json')
        score = load(ep / 'hidden_evaluation/score.json')
        assert result['protocol_complete'] and score['execution_status'] == 'observed'
        assert result['passed'] == score['passed'] == original['passed']
        accepted = score['passed'] == score['n']
        assert accepted == original['all_checks_passed']
        reads = [x for x in journal if x['call']['function']['name'] == 'read_source']
        read_counts = collections.Counter(json.dumps(json.loads(x['call']['function']['arguments']), sort_keys=True) for x in reads)
        rows.append({'episode': eid, 'task': task, 'unchanged_source': not changed,
            'edited_incomplete': bool(changed) and not accepted, 'accepted': accepted,
            'context_bound': stop is not None, 'exact_match_error_calls': exact,
            'utf8_search_error_calls': utf8, 'passed': score['passed'], 'n': score['n'],
            'candidate_sources': candidate_paths, 'read_source_calls': len(reads),
            'repeated_read_requests_beyond_first': sum(n - 1 for n in read_counts.values()),
            'has_repeated_read_request': any(n > 1 for n in read_counts.values())})
    assert len(rows) == len({x['episode'] for x in rows}) == 32
    counts = {key: sum(x[key] for x in rows) for key in ['unchanged_source', 'edited_incomplete', 'accepted', 'context_bound']}
    counts.update(finalized_and_observed=len(rows),
        exact_match_error_episodes=sum(x['exact_match_error_calls'] > 0 for x in rows),
        exact_match_error_calls=sum(x['exact_match_error_calls'] for x in rows),
        utf8_search_error_episodes=sum(x['utf8_search_error_calls'] > 0 for x in rows),
        utf8_search_error_calls=sum(x['utf8_search_error_calls'] for x in rows))
    assert counts == expected['counts']
    context_partition = {k: sum(x[k] and x['context_bound'] for x in rows)
                         for k in ['unchanged_source', 'edited_incomplete', 'accepted']}
    assert context_partition == expected['context_partition']

    # SQLAlchemy: target text, rather than replacement text, is repeated.
    sql_id = '6beed2ca1c08'
    sql_source_path = next(x for x in rows if x['episode'] == sql_id)['candidate_sources']['sqlalchemy/ext/mutable.py']
    sql_source = read(sql_source_path).decode('utf-8')
    sql_events = []
    for entry in journals[sql_id]:
        result = entry['result']
        if not isinstance(result, dict) or result.get('tool_error') != 'ValueError: Old text must match exactly once':
            continue
        args = json.loads(entry['call']['function']['arguments'])
        assert len(args['edits']) == 1
        edit = args['edits'][0]
        sql_events.append({'round_zero_based': entry['round'], 'target': edit['old'],
            'target_lines': len(edit['old'].splitlines()), 'occurrences': sql_source.count(edit['old']),
            'replacement_is_noop': edit['new'] == edit['old']})
    sql = {'episode': sql_id, 'rejected_edits': len(sql_events),
        'distinct_targets': len({x['target'] for x in sql_events}),
        'target_lines': sorted({x['target_lines'] for x in sql_events}),
        'target_occurrences': sorted({x['occurrences'] for x in sql_events}),
        'noop_replacements': sum(x['replacement_is_noop'] for x in sql_events),
        'rounds_zero_based': [x['round_zero_based'] for x in sql_events]}
    assert sql == expected['sqlalchemy_case']

    # Packaging: reconstruct the current export separately from the archival
    # pre-privacy-rewrite number. Never assert byte-count invariance of redaction.
    packaging_id = 'bfe5bd58e4c2'
    ep = Path('evidence/main/episodes') / packaging_id
    repeats = [x for x in journals[packaging_id] if x['call']['function']['name'] == 'read_source'
        and json.loads(x['call']['function']['arguments']) == {'path': 'packaging/version.py', 'start_line': 320}]
    assert all([line['line'] for line in x['result']['lines']] == list(range(320, 420)) for x in repeats)
    request = load(ep / 'explore_20.request.json')
    response = load(ep / 'explore_20.response.json')['choices'][0]['message']
    final = load(ep / 'finalize.request.json')
    next_request = dict(request)
    next_request['messages'] = request['messages'] + [
        {k: response[k] for k in ('role', 'content', 'tool_calls') if k in response}
    ] + [{'role': 'tool', 'tool_call_id': x['call']['id'],
          'content': json.dumps(x['shown_result'], ensure_ascii=False)}
         for x in journals[packaging_id] if x['round'] == 20]
    assert next_request['messages'] == final['messages'][:-1]
    stop = load(ep / 'exploration_context_stop.json')
    packaging = {'episode': packaging_id, 'same_window_requests': len(repeats),
        'line_range_inclusive': [320, 419], 'rounds_zero_based': [x['round'] for x in repeats],
        'stopped_before_round_zero_based': stop['round'],
        'round21_request_exists': (root / ep / 'explore_21.request.json').exists()}
    assert packaging == expected['packaging_case']
    for identity in expected['original_archive_request_measurement']['source_identities']:
        public_path = Path(identity['public_path'])
        read(public_path)
        actual = identities[public_path.relative_to('evidence').as_posix()]
        assert all(identity[k] == actual[k] for k in ['original_sha256', 'export_sha256', 'redacted'])

    # CSV: original archived observations/grades, not a new execution.
    csv_id = '0a3c0db3ab2e'
    ep = Path('evidence/main/episodes') / csv_id
    score = load(ep / 'hidden_evaluation/score.json')
    observed = load(ep / 'hidden_evaluation/observations.json')
    records = {x['id']: x for x in observed['records']}
    groups = {}
    for group in ['historical', 'fresh_api', 'current_roundtrip']:
        selected = [x for x in score['records'] if x['group'] == group]
        groups[group] = {'n': len(selected),
            'initial_semantics_passed': sum(x['initial_semantics_passed'] for x in selected),
            'restart_semantics_passed': sum(x['restart_semantics_passed'] for x in selected),
            'full_passed': sum(x['passed'] for x in selected)}
        for row in selected:
            obs = records[row['id']]
            error = obs.get('restart_error') if group != 'current_roundtrip' else obs.get('error')
            assert 'Length mismatch: Expected axis has 0 elements' in error
            # Child-process restart errors retain tracebacks; initial failures
            # retain only the exception type/message. Do not invent a traceback.
            if group != 'current_roundtrip':
                assert 'restore_v3_3' in error
    assert groups == expected['csv_case']['groups']
    csv_path = next(x for x in rows if x['episode'] == csv_id)['candidate_sources']['jsonpickle/ext/pandas.py']
    source_lines = read(csv_path).decode('utf-8').splitlines()
    assert "obj.get('py/object') == 'pandas.core.frame.DataFrame'" in source_lines[210]
    assert 'return self.restore_v3_3(obj)' in source_lines[211]
    assert 'df.index = pd.Index(idx)' in source_lines[321]

    result = {'passed': True, 'scope': 'Static public-release logs, source bytes and export identities; no candidate execution or API.',
        'counts': counts, 'context_partition': context_partition,
        'sqlalchemy_case': sql, 'packaging_case': packaging, 'csv_case': {'episode': csv_id, 'groups': groups},
        'original_archive_request_measurement': expected['original_archive_request_measurement'],
        'current_exported_request_measurements': {
            'last_sent_round20_bytes': len(serialized(request)),
            'reconstructed_unsent_round21_bytes': len(serialized(next_request)),
            'interpretation': 'Calculated from this public export, after privacy rewriting. Equality to original measurements is not required and is not a general invariant.'},
        'context_repeated_reads': {'read_source_calls': sum(x['read_source_calls'] for x in rows if x['context_bound']),
            'episodes_with_repeated_requests': sum(x['has_repeated_read_request'] for x in rows if x['context_bound']),
            'repeats_beyond_first': sum(x['repeated_read_requests_beyond_first'] for x in rows if x['context_bound'])},
        'episodes': rows, 'verified_public_inputs': inputs,
        'interpretation': 'Events overlap. Context and tool errors also occur in accepted attempts. No intervention or causal performance effect is estimated.'}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return result


if __name__ == '__main__':
    main()
