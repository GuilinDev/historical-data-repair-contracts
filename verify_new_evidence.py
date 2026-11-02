"""Independently check the v0.1.2 public evidence using only Python's stdlib.

No network, subprocess, model API, Docker, candidate execution, or file writes.
Export manifests bind publicly redacted bytes to the original audit identities;
they do not let a reader reconstruct or independently hash withheld private bytes.
This is recorded-evidence verification, not fresh execution of repair behavior.
"""
import argparse
from collections import Counter, defaultdict
import copy
import difflib
import hashlib
import json
import math
from pathlib import Path, PurePosixPath
import re


def check(ok, message):
    if not ok:
        raise ValueError(message)


def read(path):
    # Reject duplicate JSON keys rather than silently taking the last value.
    def pairs(values):
        result = {}
        for key, value in values:
            check(key not in result, 'Duplicate JSON key: ' + key)
            result[key] = value
        return result
    return json.loads(Path(path).read_text(encoding='utf-8'), object_pairs_hook=pairs)


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def payload_sha(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False).encode('utf-8')).hexdigest()


def safe(root, name):
    check(isinstance(name, str) and '\\' not in name, 'Nonportable relative path')
    parts = PurePosixPath(name)
    check(not parts.is_absolute() and '..' not in parts.parts and ':' not in name,
          'Unsafe path: ' + name)
    target = root.joinpath(*parts.parts)
    check(target.resolve().is_relative_to(root.resolve()), 'Path escapes release')
    check(not target.is_symlink(), 'Symlink in evidence: ' + name)
    return target


def valid_sha(value):
    return isinstance(value, str) and re.fullmatch('[0-9a-f]{64}', value) is not None


def by_id(rows, field='episode', expected=None):
    check(isinstance(rows, list), 'Expected a record list')
    ids = [r[field] for r in rows]
    check(len(ids) == len(set(ids)), 'Duplicate ' + field)
    if expected is not None:
        check(set(ids) == set(expected), 'Missing/unexpected ' + field)
    return {r[field]: r for r in rows}


class Export:
    def __init__(self, root):
        self.root = root
        manifest = read(root / 'export_manifest.json')
        self.files = by_id(manifest['files'], 'path')
        for name, row in self.files.items():
            check(valid_sha(row['original_sha256']) and valid_sha(row['export_sha256']),
                  'Malformed export digest: ' + name)
            check(type(row['redacted']) is bool, 'Invalid redaction indicator')
            check(row['redacted'] == (row['original_sha256'] != row['export_sha256']),
                  'Redaction/digest mismatch: ' + name)
            check(sha(safe(root, name)) == row['export_sha256'], 'Export bytes changed: ' + name)
            if 'export_payload_sha256' in row:
                check(payload_sha(read(safe(root, name))) == row['export_payload_sha256'],
                      'Export payload changed: ' + name)
        self.archives = {}
        if (root / 'archived_omissions.json').exists():
            self.archives = by_id(read(root / 'archived_omissions.json')['archives'], 'path')
            for name, row in self.archives.items():
                safe(root, name)
                check(valid_sha(row['original_sha256']) and row['bytes'] > 0,
                      'Malformed omitted archive identity: ' + name)
                check(isinstance(row['members'], dict), 'Missing archive member map')
                for member, digest in row['members'].items():
                    safe(root, member)
                    check(valid_sha(digest), 'Invalid archive member digest')

    def original(self, name):
        row = self.files.get(name, self.archives.get(name))
        check(row is not None, 'No original/export identity for ' + name)
        return row['original_sha256']

    def bind(self, name, digest):
        check(self.original(name) == digest, 'Original audit binding differs: ' + name)

    def load(self, name):
        check(name in self.files, 'Unmapped evidence: ' + name)
        return read(safe(self.root, name))

    def request_binding(self, name, digest):
        row = self.files[name]
        # Raw JSON-file hashes and serialized HTTP-payload hashes are different.
        if row['redacted']:
            check(row.get('original_payload_sha256') == digest,
                  'Missing original payload export binding: ' + name)
            check(valid_sha(row.get('export_payload_sha256')),
                  'Missing exported payload digest: ' + name)
        else:
            check(payload_sha(self.load(name)) == digest, 'Original request payload differs: ' + name)
            if 'original_payload_sha256' in row:
                check(row['original_payload_sha256'] == digest, 'Payload map mismatch')


def normalized(raw):
    return raw.decode('utf-8').replace('\r\n', '\n').replace('\r', '\n')


def inventory(root):
    result = {}
    for path in sorted(root.rglob('*')):
        check(not path.is_symlink(), 'Symlink in base tree')
        if path.is_file() and '__pycache__' not in path.parts:
            result[path.relative_to(root).as_posix()] = sha(path)
    return result


def grade(observed, reference, metadata, dtype):
    """Recompute the frozen contract's equality/admissible-widening judgments."""
    check(observed['status'] == 'observed', 'Unfinished evaluation')
    found = by_id(observed['records'], 'id', metadata)
    check(set(r['id'] for r in reference['records']) == set(metadata), 'Oracle/fixture IDs differ')

    def admissible(row, fixture):
        row = copy.deepcopy(row)
        if dtype:
            for section in ('semantics', 'restart_semantics'):
                for snapshot in row.get(section, {}).values():
                    if not isinstance(snapshot, dict) or 'dtypes' not in snapshot:
                        continue
                    for column in fixture.get('ambiguous_columns', []):
                        value = snapshot['dtypes'].get(column)
                        original = fixture['input']['numeric'][column]['dtype']
                        minimum = int(''.join(c for c in original if c.isdigit())) // 8
                        if value and value['itemsize'] >= minimum:
                            value['itemsize'] = 'permitted_widening'
        return row

    rows = []
    groups = defaultdict(lambda: {'n': 0, 'passed': 0})
    for original in reference['records']:
        ident = original['id']
        expected = admissible(original, metadata[ident])
        actual = admissible(found[ident], metadata[ident])
        check(found[ident]['group'] == original['group'], 'Observation group changed')
        initial = actual.get('status') == 'observed' and actual.get('semantics') == expected['semantics']
        restart = actual.get('restart_semantics') == expected['restart_semantics']
        row = {'id': ident, 'group': original['group'], 'initial_semantics_passed': initial,
               'restart_semantics_passed': restart, 'passed': initial and restart, 'actual': found[ident]}
        rows.append(row)
        groups[row['group']]['n'] += 1
        groups[row['group']]['passed'] += row['passed']
    return {'execution_status': 'observed', 'n': len(rows), 'passed': sum(r['passed'] for r in rows),
            'records': rows, 'groups': dict(groups)}


def verify_score(evidence, folder, oracle, fixtures, dtype):
    score = evidence.load(folder + '/score.json')
    computed = grade(evidence.load(folder + '/observations.json'), oracle, fixtures, dtype)
    for key, value in computed.items():
        check(score[key] == value, 'Recorded score differs from raw observations: ' + folder + '/' + key)
    check(score.get('submitted_tests', {}).get('present') is False, 'Unexpected regression test')
    return computed


def verify_recoveries(e, plan, summary, ledger, candidate_manifests):
    reviews = {}
    for number in (1, 2):
        prefix = 'recovery_%02d/' % number
        recovery = e.load(prefix + 'recovery_plan.json')
        review = e.load(prefix + 'independent_recovery_review.json')
        check(review['decision'] == 'PASS', 'Unapproved recovery')
        e.bind(prefix + 'recovery_plan.json', review['recovery_plan_sha256'])
        e.bind('plan.json', recovery['original_plan_sha256'])
        e.bind('plan.json', review['original_plan_sha256'])
        for name, digest in review['reviewed_recovery_code'].items():
            e.bind(prefix + 'reviewed_recovery_code/' + name, digest)
            check(digest == recovery['test_sha256' if name.startswith('test_') else 'script_sha256'],
                  'Recovery code/plan binding')
        for name, digest in recovery['pre_recovery_snapshot_hashes'].items():
            e.bind(prefix + name, digest)
        e.bind(prefix + 'budget_10usd_before.json', recovery['ledger_before_sha256'])
        old_ledger = e.load(prefix + 'budget_10usd_before.json')
        check(ledger['requests'][:len(old_ledger['requests'])] == old_ledger['requests'],
              'Pre-recovery ledger events changed')
        completed = recovery['frozen_first_40' if number == 1 else 'completed_53']
        check(len(completed) == (40 if number == 1 else 53), 'Recovery coverage')
        for eid, records in completed.items():
            for name, digest in records.items():
                e.bind('episodes/' + eid + '/' + name, digest)
        if number == 1:
            check(summary['engineering_recovery'] == prefix + 'recovery_plan.json', 'Recovery01 summary')
            check(recovery['remaining_schedule'] == plan['episodes'][40:], 'Recovery01 schedule')
            e.bind(prefix + 'reconstructed_first_40.json', recovery['reconstructed_sha256'])
            check(e.load(prefix + 'reconstructed_first_40.json') == summary['episodes'][:40],
                  'Reconstructed first 40 changed')
            for eid, digest in recovery['candidate_archive_hashes'].items():
                e.bind('episodes/' + eid + '/hidden_evaluation/candidate.tar', digest)
        else:
            check(summary['engineering_recovery_02'] == prefix + 'recovery_plan.json', 'Recovery02 summary')
            e.bind('recovery_01/recovery_plan.json', recovery['parent_recovery_plan_sha256'])
            e.bind('recovery_01/independent_recovery_review.json', recovery['parent_recovery_review_sha256'])
            check(recovery['remaining_schedule'] == plan['episodes'][54:], 'Recovery02 schedule')
            eid = recovery['interrupted_episode']
            ep = 'episodes/' + eid + '/'
            snapshot = prefix + 'interrupted_before/'
            # Removed duplicate source is represented by the snapshot's full tree.
            # The partial candidate is not incorrectly equated to its later final tree.
            for name, digest in recovery['interrupted_before_hashes'].items():
                if name.startswith('candidate/'):
                    check(valid_sha(digest), 'Partial candidate digest')
                    continue
                e.bind(snapshot + name, digest)
                if name not in ('tool_outputs.json', 'applied_edits.json'):
                    e.bind(ep + name, digest)
            old = e.load(snapshot + 'tool_outputs.json')
            current = e.load(ep + 'tool_outputs.json')
            check(current[:len(old)] == old, 'Repeated/changed prior tool actions')
            check(all(15 <= row['round'] <= 23 for row in current[len(old):]), 'Extra resumed rounds')
            old_edits = e.load(snapshot + 'applied_edits.json')
            check(e.load(ep + 'applied_edits.json')[:len(old_edits)] == old_edits, 'Prior edits changed')
            e.bind(prefix + 'reconstructed_request.json', recovery['reconstructed_file_sha256'])
            check(e.load(prefix + 'reconstructed_request.json') == e.load(ep + 'explore_15.request.json') ==
                  e.load(ep + 'explore_15_ledger_recovery.request.json'), 'Resumed context differs')
            for name in ('explore_15.request.json', 'explore_15_ledger_recovery.request.json'):
                e.request_binding(ep + name, recovery['reconstructed_request_sha256'])
            unsent = e.load(ep + 'explore_15.not_sent.json')
            check(unsent['event_id'] == 1146 and unsent['sent'] is False and
                  unsent['conservative_reservation_retained'] is True and
                  unsent['evidence'] == recovery['reconstructed_request_sha256'], 'Unsent annotation')
            check(ep + 'explore_15.response.json' not in e.files, 'Unsent request has a response')
            current_rows = by_id(summary['episodes'])
            for row in e.load(prefix + 'results_before.json'):
                if row['episode'] != eid:
                    check(current_rows[row['episode']] == row, 'Completed attempt changed in recovery02')
        reviews[prefix[:-1]] = e.original(prefix + 'independent_recovery_review.json')
    return reviews


def verify_cost(e, ledger, schedule):
    events = ledger['requests']
    check(len(events) == 1381 and [r['id'] for r in events] == list(range(1381)), 'Ledger events')
    destinations = [r['destination'] for r in events]
    check(len(set(destinations)) == len(destinations), 'Repeated ledger destination')
    files = {p.relative_to(e.root).as_posix() for p in (e.root / 'episodes').glob('*/*.request.json')}
    check(files == {d + '.request.json' for d in destinations}, 'Unreconciled request archive')
    expected_responses = set()
    reported = reserved = 0.0
    final_ids = []
    for event in events:
        destination = event['destination']
        eid = PurePosixPath(destination).parts[1]
        check(eid in schedule, 'Request for unplanned episode')
        request = e.load(destination + '.request.json')
        e.request_binding(destination + '.request.json', event['request_sha256'])
        check(request['model'] == schedule[eid]['model'] == 'google/gemma-4-31b-it', 'Model alias changed')
        check(request['provider']['only'] == ['coreweave/fp4'] and request['provider']['allow_fallbacks'] is False,
              'Provider pinning changed')
        final = destination.endswith('/finalize')
        check(type(event['is_final']) is bool and event['is_final'] == final, 'Final reservation identity')
        check(request['temperature'] == (0 if final else 0.2) and request['max_tokens'] == (2048 if final else 4096),
              'Token/temperature protocol changed')
        check(('tools' not in request) if final else bool(request.get('tools')), 'Final/exploration tools changed')
        for key in ('reserved_usd', 'accounted_usd'):
            check(type(event[key]) in (int, float) and math.isfinite(event[key]) and event[key] >= 0, 'Invalid cost')
        response_name = destination + '.response.json'
        if event['reported_usd'] is None:
            check(event['id'] == 1146 and event['status'] == 'reserved_before_network', 'Unexpected unresolved request')
            check(response_name not in e.files and event['accounted_usd'] == event['reserved_usd'], 'Unsent cost treatment')
            reserved += event['reserved_usd']
        else:
            response = e.load(response_name)
            expected_responses.add(response_name)
            cost = response['usage']['cost']
            check(type(cost) in (int, float) and math.isfinite(cost) and cost >= 0, 'Response cost')
            check(abs(cost - event['reported_usd']) < 1e-10 and abs(cost - event['accounted_usd']) < 1e-10,
                  'Response/ledger cost mismatch')
            check(cost <= event['reserved_usd'] + 1e-9 and event['status'] == 'response_received', 'Reservation/response')
            check(response['provider'] == 'CoreWeave' and response['model'] == request['model'], 'Response deployment')
            check(bool(response.get('choices')), 'No model response')
            reported += cost
            if final:
                final_ids.append(eid)
    actual_responses = {p.relative_to(e.root).as_posix() for p in (e.root / 'episodes').glob('*/*.response.json')}
    check(actual_responses == expected_responses and len(actual_responses) == 1380, 'Unreconciled response archive')
    check(len(final_ids) == 64 and set(final_ids) == set(schedule), 'Final request coverage')
    check(len(ledger['finalized_episodes']) == 64 and set(ledger['finalized_episodes']) == set(schedule), 'Final ledger coverage')
    check(abs(reported - ledger['reported_usd']) < 1e-8 and
          abs(reported + reserved - ledger['accounted_usd']) < 1e-8, 'Cost totals')
    check(abs(sum(r['accounted_usd'] for r in events) - ledger['accounted_usd']) < 1e-8, 'Event cost total')
    check(ledger['cap_usd'] == 10 and ledger['accounted_usd'] <= 10, 'Authorized cost cap exceeded')
    return {'ledger_events': len(events), 'actual_responses': len(actual_responses),
            'reported_usd': round(reported, 8), 'retained_unsent_reservation_usd': round(reserved, 8),
            'accounted_usd': round(reported + reserved, 8)}


def verify_editing(root):
    e = Export(root / 'evidence/editing_ablation')
    plan = e.load('plan.json')
    summary = e.load('summary.json')
    replay = e.load('replay/summary.json')
    policy = e.load('source_review/independent_source_policy.json')
    index = e.load('source_review/outcome_masked_patch_index.json')
    analysis = e.load('analysis.json')
    audit = e.load('final_independent_audit.json')
    protocol = e.load('independent_protocol_review.json')
    ledger = e.load('budget_10usd.json')
    check(protocol['decision'] == audit['decision'] == 'PASS', 'Independent review failed')
    check(not audit['must_fix_remaining'], 'Unresolved independent finding')
    for document in (protocol, replay, policy, analysis):
        e.bind('plan.json', document['plan_sha256'])
    for name, digest in audit['artifact_sha256'].items():
        e.bind(name, digest)
    e.bind('source_review/outcome_masked_patch_index.json', policy['review_index_sha256'])
    e.bind('source_review/independent_source_policy.json', analysis['source_policy_sha256'])
    for name, digest in plan['source_hashes'].items():
        e.bind('frozen_runner/' + name, digest)
    check(sha(root / 'tasks.json') == plan['task_metadata_sha256'], 'Task definitions changed')
    for name, digest in plan['oracle_hashes'].items():
        check(sha(safe(root / 'oracle', name)) == digest, 'Frozen oracle changed')
    e.bind('frozen_bundle_manifest.json', plan['bundle_manifest_sha256'])
    e.bind('remote_assets_before.json', plan['remote_snapshot_sha256'])
    e.bind('runtime_preflight/summary.json', plan['runtime_preflight_sha256'])
    check(e.load('runtime_preflight/summary.json')['passed'] is True, 'Runtime controls failed')
    baseline_assets = e.load('remote_assets_before.json')
    public_runtime = read(root / 'plan.json')
    check(baseline_assets['image'] == public_runtime['expected_image_id'] and baseline_assets['machine'] == 'aarch64',
          'Public runtime identity differs from the observed runtime')
    for name in ['remote_assets_start.json', 'remote_assets_after.json', 'remote_assets_after_recovery.json',
                 'remote_assets_after_recovery02.json', 'remote_assets_before_replay.json', 'remote_assets_after_replay.json']:
        check(e.load(name) == baseline_assets, 'Recorded scientific asset drift: ' + name)
    frozen_bundle = e.load('frozen_bundle_manifest.json')
    for name, digest in frozen_bundle.items():
        p = safe(root / 'bundle', name)
        check(p.is_file() and sha(p) == digest, 'Frozen bundle file missing or changed: ' + name)
    check(inventory(root / 'bundle') == frozen_bundle, 'Unexpected frozen bundle file')
    schedule = by_id(plan['episodes'])
    check(plan['planned'] == summary['planned'] == summary['recorded'] == len(schedule) == 64, 'Campaign size')
    expected_ids = list(schedule)
    sources = [summary['episodes'], replay['episodes'], policy['decisions'], index, analysis['episodes']]
    mapped = [by_id(records, expected=expected_ids) for records in sources]
    sm, rm, pm, im, am = mapped
    check([r['episode'] for r in summary['episodes']] == expected_ids, 'Final schedule order')
    check(summary['protocol_and_evaluation_complete'] and summary['immutable_assets_unchanged'], 'Incomplete campaign')
    check(replay['complete'] and replay['all_full_acceptances_consistent'] and replay['api_ledger_unchanged'] and
          replay['immutable_assets_unchanged'] and replay['additional_api_calls'] == 0, 'Incomplete full replay')
    check(policy['review_complete'] is True and all(type(r['source_policy_compliant']) is bool for r in pm.values()),
          'Incomplete source policy review')
    check(analysis['complete'], 'Incomplete analysis')
    tasks = read(root / 'tasks.json')
    check(len({r['family'] for r in schedule.values()}) == 8, 'Lineage count')
    check(Counter((r['family'], r['arm']) for r in schedule.values()) ==
          Counter({(f, a): 4 for f in tasks for a in ('exact', 'span')}), 'Unbalanced lineage/arm counts')
    check({(r['family'], r['arm'], r['replicate']) for r in schedule.values()} ==
          {(f, a, rep) for f in tasks for a in ('exact', 'span') for rep in range(4)}, 'Replicate identity')
    for block in range(32):
        pair = [r for r in schedule.values() if r['block'] == block]
        check(len(pair) == 2 and {r['arm'] for r in pair} == {'exact', 'span'} and
              len({(r['family'], r['replicate']) for r in pair}) == 1, 'Paired block differs')
    costs = verify_cost(e, ledger, schedule)
    base_maps = {}
    for family in tasks:
        base_maps[family] = inventory(root / 'bundle/packages' / family / 'base')
    outputs = []
    candidate_maps = {}
    drift = []
    new_files = []
    newline_only = []
    observation_count = 0
    for eid, scheduled in schedule.items():
        row, replay_row, policy_row, indexed = sm[eid], rm[eid], pm[eid], im[eid]
        family = scheduled['family']
        for key, value in scheduled.items():
            check(row[key] == value, 'Schedule metadata changed: ' + eid)
        check(replay_row['family'] == indexed['family'] == family and replay_row['arm'] == scheduled['arm'],
              'Replay/review task identity differs')
        check(indexed['allowed'] == tasks[family]['allowed'] and indexed['contract'] == tasks[family]['request'] and
              indexed['frozen_system'] == plan['system'], 'Source-policy scope changed')
        ep = 'episodes/' + eid
        result = e.load(ep + '/result.json')
        final = e.load(ep + '/final.json')
        response = e.load(ep + '/finalize.response.json')
        request = e.load(ep + '/finalize.request.json')
        initial_prompt = e.load(ep + '/initial.json')
        check(initial_prompt[0]['content'] == plan['system'] and
              json.loads(initial_prompt[1]['content'])['rules'] == indexed['frozen_task_rules'],
              'Frozen task/system prompt differs')
        choice = response['choices'][0]
        check(row['protocol_complete'] and result['protocol_complete'] and not row['budget_truncated'], 'Incomplete attempt')
        check(final['protocol_error'] is None and final['last_tool_consumed'] is True and final['rounds'] <= 24,
              'Invalid final protocol: ' + eid)
        check(choice['finish_reason'] == 'stop' and not choice['message'].get('tool_calls') and
              final['raw'] == choice['message']['content'] and final['response_id'] == response['id'], 'Final response binding')
        check(final['answer'] == result['answer'] == row['answer'] and bool(final['answer']['report'].strip()), 'Final report binding')
        patch_dir = e.root / 'patches' / eid
        patch = read(patch_dir / 'patch_manifest.json')
        base = root / 'bundle/packages' / family / 'base'
        before = base_maps[family]
        changed = patch['changed_paths']
        check(len(changed) == len(set(changed)), 'Repeated overlay path')
        check(set(changed) <= set(tasks[family]['allowed']) | {'tests/test_regression.py'}, 'Unauthorized overlay')
        check(set(p.relative_to(patch_dir).as_posix() for p in patch_dir.rglob('*') if p.is_file()) ==
              set(changed) | {'patch_manifest.json'}, 'Unexpected/missing overlay file')
        after = dict(before)
        effective = []
        diff = []
        for name in changed:
            p = safe(patch_dir, name)
            after[name] = sha(p)
            check(before.get(name) != after[name], 'Unchanged bytes included as patch')
            original = (base / name).read_bytes() if (base / name).exists() else None
            amended = p.read_bytes()
            if original is None:
                new_files.append([eid, name])
            original_text = normalized(original) if original is not None else None
            amended_text = normalized(amended)
            if name in tasks[family]['allowed'] and original_text != amended_text:
                effective.append(name)
            diff.extend(difflib.unified_diff((original_text or '').splitlines(keepends=True),
                        amended_text.splitlines(keepends=True), fromfile='base/' + name, tofile='candidate/' + name))
        check(after == patch['full_candidate_manifest'] == row['candidate_manifest'], 'Reconstructed candidate differs: ' + eid)
        check(sorted(changed) == row['changed_paths'] and sorted(effective) == row['normalized_source_changed_paths'], 'Changed paths differ')
        changed_source = bool(effective)
        check(row['actual_source_change'] is changed_source and indexed['actual_source_change'] is changed_source and
              row['only_authorized_paths'] is True and indexed['only_authorized_paths'] is True, 'Source-change status')
        e.bind('source_review/' + indexed['diff'], indexed['diff_sha256'])
        check(normalized((e.root / 'source_review' / indexed['diff']).read_bytes()) == ''.join(diff), 'Source-review diff differs')
        if changed and not effective:
            newline_only.append(eid)
        for archive_name in (ep + '/hidden_evaluation/candidate.tar', 'replay/' + eid + '/candidate.tar'):
            check(archive_name in e.archives and e.archives[archive_name]['members'] == after, 'Candidate/archive member identity')
        candidate_maps[eid] = after
        fixture = by_id(read(root / 'bundle/fixtures' / family / 'all.json'), 'id')
        oracle = read(root / 'oracle' / (family + '.json'))
        initial = verify_score(e, ep + '/hidden_evaluation', oracle, fixture, family == 'jsonpickle_dtype_634')
        repeated = verify_score(e, 'replay/' + eid, oracle, fixture, family == 'jsonpickle_dtype_634')
        observation_count += initial['n']
        check(initial['n'] == repeated['n'] == tasks[family]['cases'], 'Observation denominator')
        for key in ('n', 'passed', 'groups', 'execution_status'):
            check(row[key] == result[key] == initial[key], 'Result/raw score mismatch')
        full = initial['passed'] == initial['n']
        check(full == row['all_checks_passed'] == (repeated['passed'] == repeated['n']), 'Full replay verdict changed')
        check(replay_row['full_semantic_acceptance'] == full and replay_row['passed'] == repeated['passed'] and
              replay_row['n'] == repeated['n'] and replay_row['same_full_acceptance'] is True, 'Replay summary differs')
        e.bind('replay/' + eid + '/score.json', replay_row['score_sha256'])
        bits = lambda score: [(r['id'], r['initial_semantics_passed'], r['restart_semantics_passed'], r['passed']) for r in score['records']]
        same = bits(initial) == bits(repeated)
        check(replay_row['same_record_decisions'] == same, 'Replay drift metadata')
        if not same:
            check(family == 'jsonpickle_dtype_634' and not full, 'Undisclosed full/other-family drift')
            drift.append(eid)
        journal = e.load(ep + '/tool_outputs.json')
        check(len(journal) == final['tool_actions'] == row['tool_actions'], 'Tool journal count')
        check(all(type(event['round']) is int and 0 <= event['round'] < 24 for event in journal), 'Tool round bound')
        check(len({event['call']['id'] for event in journal}) == len(journal), 'Duplicated tool event')
        if journal:
            last = [m for m in request['messages'] if m.get('tool_call_id') == journal[-1]['call']['id'] and m['role'] == 'tool']
            check(len(last) == 1 and json.loads(last[0]['content']) == journal[-1]['shown_result'],
                  'Last shown tool result absent/different in final request')
        # Match each executed call to the archived response; at most six calls per round.
        tool_calls = []
        for response_file in sorted((e.root / ep).glob('explore_*.response.json')):
            raw = read(response_file)
            round_ = int(re.match(r'explore_(\d+)', response_file.name).group(1))
            for call in raw['choices'][0]['message'].get('tool_calls', [])[:6]:
                tool_calls.append((round_, call))
        check(tool_calls == [(event['round'], event['call']) for event in journal], 'Response/tool journal mismatch')
        errors = Counter()
        edit_calls = successful = 0
        for event in journal:
            if event['call']['function']['name'] != 'apply_edits':
                continue
            edit_calls += 1
            if 'tool_error' not in event['result']:
                successful += bool(event['result'].get('applied_files'))
                continue
            message = event['result']['tool_error']
            if 'exactly once' in message:
                kind = 'exact_match_rejection'
            elif 'Stale source_version' in message:
                kind = 'stale_version_rejection'
            elif any(text in message for text in ('Line span', 'Overlapping', 'coordinates')):
                kind = 'span_rejection'
            elif 'SyntaxError' in message or 'IndentationError' in message:
                kind = 'syntax_rejection'
            else:
                kind = 'other_edit_rejection'
            errors[kind] += 1
        accepted = full and changed_source and policy_row['source_policy_compliant']
        derived = {key: row[key] for key in ['episode', 'family', 'arm', 'replicate', 'block', 'passed', 'n', 'actual_source_change', 'all_checks_passed']}
        derived.update(primary_accepted=accepted, category='PASS' if accepted else ('E' if changed_source else 'U'),
                       source_policy_compliant=policy_row['source_policy_compliant'],
                       context_stop=(e.root / ep / 'exploration_context_stop.json').exists(),
                       edit_calls=edit_calls, successful_edit_calls=successful, edit_rejections=dict(errors))
        check(derived == am[eid], 'Derived episode analysis differs: ' + eid)
        outputs.append(derived)
    reviews = verify_recoveries(e, plan, summary, ledger, candidate_maps)
    check(reviews == replay['engineering_recovery_reviews'] == analysis['engineering_recovery_reviews'], 'Recovery review summary')
    check(observation_count == 2016, 'Observation total')
    check(drift == replay['record_level_decision_drift'] == analysis['record_level_replay_drift'] and len(drift) == 4, 'Drift coverage')
    check(len(new_files) == 4 and all(n == 'packaging/_structures.py' for _, n in new_files), 'New-file coverage')
    check(set(newline_only) == {'4e90e3581a3f', 'd38d3f36c802'}, 'Newline-only coverage')
    arms = {}
    for arm in ('exact', 'span'):
        rows = [r for r in outputs if r['arm'] == arm]
        categories = Counter(r['category'] for r in rows)
        errors = Counter()
        for row in rows:
            errors.update(row['edit_rejections'])
        arms[arm] = {'attempts': len(rows), 'complete_acceptances': categories['PASS'], 'unchanged': categories['U'],
                     'edited_incomplete': categories['E'], 'context_stops': sum(r['context_stop'] for r in rows),
                     'edit_rejections': dict(errors), 'edit_calls': sum(r['edit_calls'] for r in rows),
                     'successful_edit_calls': sum(r['successful_edit_calls'] for r in rows),
                     'attempts_with_edit_rejection': sum(bool(r['edit_rejections']) for r in rows)}
        independent = audit['primary_results'][arm]
        check(independent['accepted'] == categories['PASS'] and independent['unchanged'] == categories['U'] and
              independent['edited_incomplete'] == categories['E'], 'Independent result binding')
    by_lineage = {f: {**{a: sum(r['primary_accepted'] for r in outputs if r['family'] == f and r['arm'] == a)
                        for a in arms}, 'attempts_per_arm': 4} for f in sorted(tasks)}
    check(arms == analysis['arms'] and by_lineage == analysis['by_lineage'], 'Aggregate analysis differs')
    check(arms['exact']['complete_acceptances'] == 10 and arms['span']['complete_acceptances'] == 4, 'Expected completed result differs')
    difference = arms['span']['complete_acceptances'] - arms['exact']['complete_acceptances']
    check(analysis['acceptance_difference_span_minus_exact'] == difference and
          analysis['percentage_point_difference'] == 100 * difference / 32, 'Effect difference differs')
    check(sum(r['source_policy_compliant'] for r in pm.values()) == 64, 'Policy exclusions changed')
    check(policy['coverage']['episodes_reviewed'] == 64 and policy['coverage']['policy_exclusions'] == 0 and
          policy['coverage']['normalized_source_changed'] == sum(r['actual_source_change'] for r in outputs), 'Source-policy coverage')
    check(abs(costs['accounted_usd'] - analysis['accounted_usd']) < 1e-8 and
          abs(costs['reported_usd'] - audit['cost']['reported_usd']) < 1e-8, 'Published cost summary')
    return {'planned_completed': 64, 'initial_observation_judgments': observation_count,
            'replay_observation_judgments': observation_count, 'full_acceptance_replay_changes': 0,
            'partial_dtype_drift_episode_ids': drift, 'arms': arms, 'by_lineage': by_lineage, 'cost': costs,
            'source_policy_compliant': 64, 'new_files_verified': len(new_files), 'newline_only_episodes': newline_only,
            'original_plan_sha256': e.original('plan.json'), 'export_manifest_sha256': sha(e.root / 'export_manifest.json'),
            'frozen_bundle_files_verified': len(frozen_bundle),
            'exported_files_verified': len(e.files), 'omitted_archive_member_manifests': len(e.archives)}


def verify_signature(root):
    e = Export(root / 'extensions/approval_signature')
    plan = e.load('plan.json')
    summary = e.load('summary.json')
    review = e.load('independent_execution_review.json')
    fixtures = e.load('fixtures.json')
    signed = e.load('persisted_original_approvals.json')
    check(review['decision'] == 'PASS_WITH_EXPLICIT_SCOPE_LIMITS', 'Calibration review')
    e.bind('plan.json', review['plan_sha256'])
    e.bind('plan.json', summary['plan_sha256'])
    for name, field in [('fixtures.json', 'fixture_sha256'), ('merged_source_manifest.json', 'source_manifest_sha256'),
                        ('interpretation_clarification.json', 'interpretation_clarification_sha256')]:
        e.bind(name, review[field])
    e.bind('fixtures.json', plan['fixture_sha256'])
    e.bind('merged_source_manifest.json', plan['source_manifest_sha256'])
    e.bind('frozen_runner/approval_signature_calibration.mjs', plan['runner_sha256'])
    check(plan['runner_sha256'] == review['runner_sha256'], 'Calibration runner binding')
    e.bind('independent_replay/audit.py', review['independent_audit_script_sha256'])
    for row in e.load('merged_source_manifest.json'):
        name = 'merged_upstream/' + row['variant'] + '/' + row['path']
        e.bind(name, row['sha256'])
        check(row['commit'] == plan['upstream_parent' if row['variant'] == 'base' else 'upstream_fixed'], 'Upstream source commit')
        # Actual algorithm/source/license bytes, unlike local-path receipts, stay exact.
        check(sha(safe(e.root, name)) == row['sha256'], 'Upstream source altered on export')
    for variant, files in plan['executable_hashes'].items():
        for name, digest in files.items():
            path = 'executable/' + variant + '/' + name
            e.bind(path, digest)
            check(sha(safe(e.root, path)) == digest, 'Adapted executable altered')
    fixture_map = by_id(fixtures, 'id')
    signed_map = by_id(signed, 'id', fixture_map)
    check(len(fixtures) == plan['case_count'] == 54 and len(plan['variants']) == 5, 'Calibration dimensions')
    check(Counter(r['group'] for r in fixtures) == {'historical': 27, 'fresh': 27}, 'Calibration groups')
    for fixture in fixtures:
        check(type(fixture['expected']) is bool and fixture['eligible_continuation'] is fixture['expected'], 'Calibration expected type')
        check(all(signed_map[fixture['id']][k] == v for k, v in fixture.items()), 'Persisted fixture changed')
    eligible = {r['id'] for r in fixtures if r['eligible_continuation']}
    check(len(eligible) == 37 and len({r['signature'] for r in signed}) == 42, 'Calibration populations')
    summary_rows = by_id(summary['results'], 'variant', plan['variants'])
    independent_rows = by_id(review['results'], 'variant', plan['variants'])
    results = []
    for variant in plan['variants']:
        prefix = 'observations/' + variant + '/'
        initial = by_id(e.load(prefix + 'initial.json'), 'id', fixture_map)
        restarted = by_id(e.load(prefix + 'fresh_process.json'), 'id', fixture_map)
        reissued = by_id(e.load(prefix + 'reissued_fresh_process.json'), 'id', eligible)
        persisted = by_id(e.load(prefix + 'persisted_reissued_approvals.json'), 'id', eligible)
        scores = by_id(e.load(prefix + 'score.json')['records'], 'id', fixture_map)
        decisions = []
        for fixture in fixtures:
            eid = fixture['id']
            stages = [initial[eid], restarted[eid]] + ([reissued[eid]] if eid in eligible else [])
            check(all(s['execution_complete'] is True and type(s['accepted']) is bool for s in stages), 'Incomplete calibration observation')
            if eid in eligible:
                saved = persisted[eid]
                check(saved['verify'] == fixture['verify'] and saved['signed'] == fixture['verify'] and
                      saved['expected'] is True and saved['wrongSecret'] is False, 'Reissued fixture population/binding')
            derived = {'id': eid, 'group': fixture['group'], 'expected': fixture['expected'],
                       'initial': initial[eid]['accepted'], 'restarted': restarted[eid]['accepted'],
                       'continuation_required': eid in eligible, 'continued': reissued[eid]['accepted'] if eid in eligible else None,
                       'execution_complete': True,
                       'passed': initial[eid]['accepted'] == fixture['expected'] and restarted[eid]['accepted'] == fixture['expected'] and
                                 (eid not in eligible or reissued[eid]['accepted'])}
            check(scores[eid] == derived, 'Calibration raw/score mismatch')
            decisions.append(derived)
        count = sum(r['passed'] for r in decisions)
        derived = {'variant': variant, 'total': 54, 'passed': count, 'completed': 54,
                   'groups': {g: {'total': 27, 'passed': sum(r['passed'] for r in decisions if r['group'] == g)}
                              for g in ('historical', 'fresh')}, 'failed_ids': [r['id'] for r in decisions if not r['passed']]}
        check(derived == summary_rows[variant], 'Calibration summary differs')
        check(independent_rows[variant]['complete'] == 54 and independent_rows[variant]['contract_passed'] == count,
              'Independent calibration totals differ')
        results.append({'variant': variant, 'contract_passed': count, 'total': 54,
                        'legacy_passed': derived['groups']['historical']['passed'], 'new_format_passed': derived['groups']['fresh']['passed']})
    check([r['contract_passed'] for r in results] == [29, 54, 37, 49, 33], 'Frozen calibration table differs')
    check(summary['completed'] and summary['paid_calls'] == 0, 'Calibration completion')
    check(review['counts']['total_verification_observations_across_five_variants'] == 725, 'Calibration observation count')
    return {'known_defects': 1, 'scenarios': 54, 'variants': 5, 'raw_verification_observations': 725,
            'reissue_population_per_variant': 37, 'model_attempts': 0, 'results': results,
            'original_plan_sha256': e.original('plan.json'), 'export_manifest_sha256': sha(e.root / 'export_manifest.json'),
            'exported_files_verified': len(e.files)}


def verify(root):
    root = Path(root).resolve()
    return {'passed': True, 'editing_ablation': verify_editing(root), 'signature_calibration': verify_signature(root),
            'api_calls': 0, 'docker_calls': 0, 'subprocess_calls': 0,
            'scope': 'Recorded evidence and exact public candidate bytes; no fresh repair execution. Original private-byte identities are export attestations. Full semantic replay has a separate Docker entry point.'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parent)
    args = parser.parse_args()
    print(json.dumps(verify(args.root), indent=2))


if __name__ == '__main__':
    main()
