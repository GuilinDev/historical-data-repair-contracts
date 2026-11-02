"""Independent audit: source adaptation, Python HMAC oracle, isolated Node replay."""
from datetime import datetime, timezone
import base64
from collections import Counter
import hashlib
import hmac
import json
from pathlib import Path
import shutil
import subprocess

HERE = Path(__file__).resolve().parent
SOURCE = HERE.parent
PROJECT = SOURCE.parents[2]


def load(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def b64url(raw):
    return base64.urlsafe_b64encode(raw).decode().rstrip('=')


def input_hash(value):
    return b64url(hashlib.sha256(canonical(value).encode()).digest())


SECRET = 'public-synthetic-calibration-key-not-a-production-credential'
FIELDS = ['approvalId', 'toolCallId', 'toolName']


def payload_bytes(tuple_, format_):
    values = [tuple_[field] for field in FIELDS] + [input_hash(tuple_['input'])]
    text = '\n'.join(values) if format_ == 'base' else json.dumps(['ai-sdk-tool-approval-v1'] + values, ensure_ascii=False, separators=(',', ':'))
    return text.encode()


def sign(tuple_, format_, secret=SECRET):
    return b64url(hmac.digest(secret.encode(), payload_bytes(tuple_, format_), 'sha256'))


def expected_decision(row):
    """Derive labels from stated compatibility/binding obligations, not JS output."""
    matching_tuple = all(row['signed'][f] == row['verify'][f] for f in FIELDS) and canonical(row['signed']['input']) == canonical(row['verify']['input'])
    safe_legacy = row['producer'] != 'base' or all('\n' not in row['verify'][f] for f in FIELDS)
    return matching_tuple and safe_legacy and not row['wrongSecret']


def predict(row, signature, variant):
    """Independent implementation of each explicitly specified verifier policy."""
    secret = SECRET + '-wrong' if row['wrongSecret'] else SECRET
    tuple_ = row['verify']
    if variant == 'upstream_base':
        return hmac.compare_digest(signature, sign(tuple_, 'base', secret))
    if variant == 'reject_control_chars' and any(ord(c) < 32 for f in FIELDS for c in tuple_[f]):
        return False
    if hmac.compare_digest(signature, sign(tuple_, 'fixed', secret)):
        return True
    if variant == 'reject_all_legacy':
        return False
    legacy_allowed = variant == 'unguarded_legacy' or all('\n' not in tuple_[f] for f in FIELDS)
    return legacy_allowed and hmac.compare_digest(signature, sign(tuple_, 'base', secret))


def main():
    plan = load(SOURCE / 'plan.json')
    fixtures = load(SOURCE / 'fixtures.json')
    summary = load(SOURCE / 'summary.json')
    persisted = load(SOURCE / 'persisted_original_approvals.json')
    runner = PROJECT / 'scripts/approval_signature_calibration.mjs'
    assert digest(runner) == plan['runner_sha256']
    assert digest(SOURCE / 'fixtures.json') == plan['fixture_sha256']
    assert digest(SOURCE / 'plan.json') == summary['plan_sha256']
    assert digest(SOURCE / 'merged_source_manifest.json') == plan['source_manifest_sha256']
    manifest = load(SOURCE / 'merged_source_manifest.json')
    for entry in manifest:
        assert digest(SOURCE / 'merged_upstream' / entry['variant'] / entry['path']) == entry['sha256']
        expected_commit = plan['upstream_parent' if entry['variant'] == 'base' else 'upstream_fixed']
        assert entry['commit'] == expected_commit
    independently_downloaded = PROJECT / 'results/predeadline_assessment_20261031/approval_source_screen'
    for version in ['base', 'fixed']:
        for rel in ['packages/ai/src/generate-text/tool-approval-signature.ts',
                    'packages/ai/src/generate-text/tool-approval-signature.test.ts',
                    'packages/ai/src/util/canonical-hash.ts']:
            other = independently_downloaded / (('before' if version == 'base' else 'after') + '-' + Path(rel).name)
            assert other.read_bytes() == (SOURCE / 'merged_upstream' / version / rel).read_bytes()
    guard = "!approvalId.includes('\\n') &&\n    !toolCallId.includes('\\n') &&\n    !toolName.includes('\\n')"
    adaptation_checks = []
    for variant, files in plan['executable_hashes'].items():
        for name, sha in files.items():
            assert digest(SOURCE / 'executable' / variant / name) == sha
        upstream = 'base' if variant == 'upstream_base' else 'fixed'
        tree = SOURCE / 'merged_upstream' / upstream
        signer = (tree / 'packages/ai/src/generate-text/tool-approval-signature.ts').read_text(encoding='utf-8')
        signer = signer.replace("from '@ai-sdk/provider-utils'", "from './uint8-utils.ts'")
        signer = signer.replace("from '../util/canonical-hash'", "from './canonical-hash.ts'")
        if variant == 'reject_all_legacy':
            assert signer.count(guard) == 1
            signer = signer.replace(guard, 'false')
        elif variant == 'unguarded_legacy':
            assert signer.count(guard) == 1
            signer = signer.replace(guard, 'true')
        elif variant == 'reject_control_chars':
            marker = 'export async function verifyToolApprovalSignature'
            pre, post = signer.split(marker)
            first = '  const key = await importKey(secret);'
            assert post.count(first) == 1
            post = post.replace(first, "  if ([approvalId, toolCallId, toolName].some(value => /[\\u0000-\\u001f]/.test(value))) return false;\n" + first)
            signer = pre + marker + post
        assert signer == (SOURCE / 'executable' / variant / 'tool-approval-signature.ts').read_text(encoding='utf-8')
        canonical_source = (tree / 'packages/ai/src/util/canonical-hash.ts').read_text(encoding='utf-8')
        canonical_source = canonical_source.replace("from '@ai-sdk/provider-utils'", "from './uint8-utils.ts'")
        assert canonical_source == (SOURCE / 'executable' / variant / 'canonical-hash.ts').read_text(encoding='utf-8')
        assert (tree / 'packages/provider-utils/src/uint8-utils.ts').read_bytes() == (SOURCE / 'executable' / variant / 'uint8-utils.ts').read_bytes()
        adaptation_checks.append({'variant': variant, 'only_declared_changes': True})
    assert len(fixtures) == len({r['id'] for r in fixtures}) == 54
    assert len(persisted) == 54
    for row, artifact in zip(fixtures, persisted):
        assert row['expected'] is expected_decision(row)
        assert row['eligible_continuation'] == row['expected']
        assert {k:v for k,v in artifact.items() if k != 'signature'} == row
        assert artifact['signature'] == sign(row['signed'], row['producer'])
    collisions = []
    for a, b in plan['collision_pairs']:
        assert a != b
        assert payload_bytes(a, 'base') == payload_bytes(b, 'base')
        assert payload_bytes(a, 'fixed') != payload_bytes(b, 'fixed')
        collisions.append({'different_tuples': True, 'old_payload_bytes_equal': True,
                           'new_payload_bytes_equal': False,
                           'old_payload_sha256': hashlib.sha256(payload_bytes(a, 'base')).hexdigest()})

    workspace = HERE / ('workspace_' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S'))
    assert not workspace.exists(), 'Do not overwrite a prior independent replay'
    (workspace / 'scripts').mkdir(parents=True)
    shutil.copy2(runner, workspace / 'scripts' / runner.name)
    isolated = workspace / 'results/second_calibration/approval_signature_17493'
    isolated.mkdir(parents=True)
    for name in ['plan.json', 'fixtures.json', 'merged_source_manifest.json']:
        shutil.copy2(SOURCE / name, isolated / name)
    shutil.copytree(SOURCE / 'executable', isolated / 'executable')
    result = subprocess.run(['node', str(workspace / 'scripts' / runner.name), 'run'], capture_output=True,
                            text=True, encoding='utf-8', timeout=120)
    (HERE / 'node_stdout.txt').write_text(result.stdout, encoding='utf-8')
    (HERE / 'node_stderr.txt').write_text(result.stderr, encoding='utf-8')
    assert result.returncode == 0, result.stderr
    independent_summary = load(isolated / 'summary.json')
    assert {k:v for k,v in independent_summary.items() if k != 'node_version'} == {k:v for k,v in summary.items() if k != 'node_version'}
    assert load(isolated / 'collision_observations.json') == load(SOURCE / 'collision_observations.json')
    assert load(isolated / 'persisted_original_approvals.json') == persisted
    independent_records = []
    count_comparisons = 0
    for variant in plan['variants']:
        original_folder = SOURCE / 'observations' / variant
        replay_folder = isolated / 'observations' / variant
        for p in original_folder.glob('*.json'):
            assert load(p) == load(replay_folder / p.name), (variant, p.name)
        initial = load(replay_folder / 'initial.json')
        restart = load(replay_folder / 'fresh_process.json')
        reissued = load(replay_folder / 'persisted_reissued_approvals.json')
        reissued_results = load(replay_folder / 'reissued_fresh_process.json')
        assert len(reissued) == len(reissued_results) == 37
        assert [r['id'] for r in reissued] == [r['id'] for r in fixtures if r['expected']]
        for stage in [initial, restart]:
            for row, observation in zip(persisted, stage):
                assert observation['id'] == row['id'] and observation['execution_complete']
                assert observation['accepted'] == predict(row, row['signature'], variant)
                count_comparisons += 1
        for row, observation in zip(reissued, reissued_results):
            assert row['signature'] == sign(row['verify'], 'base' if variant == 'upstream_base' else 'fixed')
            assert observation['id'] == row['id'] and observation['execution_complete']
            assert observation['accepted'] == predict(row, row['signature'], variant)
            count_comparisons += 1
        expected_rows = []
        for row in fixtures:
            original_sig = sign(row['signed'], row['producer'])
            first = predict(row, original_sig, variant)
            continuation = None
            if row['expected']:
                replacement = {**row, 'signed': row['verify'], 'wrongSecret': False}
                new_sig = sign(row['verify'], 'base' if variant == 'upstream_base' else 'fixed')
                continuation = predict(replacement, new_sig, variant)
            passed = first == row['expected'] and (not row['expected'] or continuation)
            expected_rows.append({'id': row['id'], 'passed': bool(passed)})
        observed_rows = load(replay_folder / 'score.json')['records']
        assert expected_rows == [{'id': r['id'], 'passed': r['passed']} for r in observed_rows]
        independent_records.append({'variant': variant, 'passed': sum(r['passed'] for r in expected_rows),
                                    'total': 54, 'execution_complete': True})
    report = {'reviewed_utc': datetime.now(timezone.utc).isoformat(), 'passed': True,
        'plan_sha256': digest(SOURCE / 'plan.json'), 'runner_sha256': digest(runner),
        'fixtures_sha256': digest(SOURCE / 'fixtures.json'), 'independent_audit_script_sha256': digest(Path(__file__)),
        'independent_node_replay_exit': result.returncode, 'node_summary_identical_except_runtime_version': True,
        'original_node': summary['node_version'], 'replay_node': independent_summary['node_version'],
        'replay_workspace': workspace.relative_to(HERE).as_posix(),
        'independent_python_hmac_observation_comparisons': count_comparisons,
        'fixture_labels_independently_derived': 54, 'source_adaptation_checks': adaptation_checks,
        'collision_proofs': collisions, 'results': independent_records,
        'counts': {'cases': 54, 'historical': 27, 'fresh': 27, 'expected_accept': 37,
                   'expected_reject': 17, 'unique_original_signatures': len({r['signature'] for r in persisted}),
                   'verification_observations_per_variant': 145, 'variants': 5,
                   'semantic_defect_lineages': 1, 'model_runs': 0},
        'interpretation': ['All 54 labels are independent of JavaScript candidate outputs.',
            'The 37 reissued cases are expected-accept cases, even when a negative control initially rejected them.',
            '54 conditions and 725 repeated verification observations are not independent defects or independent statistical samples.',
            'Restart checks reproduced the initial decisions; no added rejection attributable to restart was observed.']}
    (HERE / 'verification.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps({k:report[k] for k in ['passed','node_summary_identical_except_runtime_version','original_node','replay_node','independent_python_hmac_observation_comparisons','results','counts']}, indent=2))


if __name__ == '__main__':
    main()
