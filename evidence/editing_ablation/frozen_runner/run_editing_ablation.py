"""Frozen, separately authorized 64-attempt experiment; no publication writes."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import random
import shlex
import shutil
import subprocess
import time

from ablation_api_client import AblationClient, BudgetLimit, atomic_json
from editing_ablation_tools import AblationGateway, tools_for
from freeze_and_build import ROOT
from prepare_reader_repairs import HOST
from resource_task_tools import ResourceDataset
from run_reader_repairs import run_episode
from run_resource_baselines import SYSTEM

DATA = ROOT / 'results/resource/20261030T212309'
RELEASE = ROOT / 'release/historical-data-compatibility-v0.1.1'
SOURCES = ['run_editing_ablation.py', 'ablation_api_client.py', 'editing_ablation_tools.py',
    'resource_task_tools.py', 'resource_eval_worker.py', 'resource_pytest_runner.py',
    'transfer_task_tools.py', 'run_reader_repairs.py', 'run_resource_baselines.py',
    'agent_runner_v2.py', 'reader_repair_tools.py', 'model_pilot.py', 'freeze_and_build.py',
    'prepare_reader_repairs.py', 'historical_artifacts.py', 'transfer_eval_worker.py', 'repair_eval_worker.py']
TESTS = ['test_ablation_api_client.py', 'test_editing_ablation_tools.py']
SYSTEM_FIXED = SYSTEM + ' The editing interface supplied in this conversation is the one to use. read_source includes a source_version hash; follow the apply_edits tool schema exactly.'


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def hashes(root):
    return {p.relative_to(root).as_posix(): digest(p) for p in sorted(root.rglob('*'))
            if p.is_file() and '__pycache__' not in p.parts}


def remote_snapshot(data, out):
    script = '''import hashlib,json,pathlib,subprocess,sys,platform
root=pathlib.Path(sys.argv[1]);runtime=pathlib.Path(sys.argv[2]);result={}
for label,path in [('harness',root/'harness'),('packages',root/'packages'),('producers',root/'producers'),('fixtures',root/'fixtures'),('fixtures_public',root/'fixtures_public'),('runtime',runtime)]:
 assert path.is_dir(),str(path)
 result[label]={p.relative_to(path).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(path.rglob('*')) if p.is_file() and '__pycache__' not in p.parts}
result['machine']=platform.machine()
result['image']=json.loads(subprocess.check_output(['docker','image','inspect',sys.argv[3]],text=True))[0]['Id']
print(json.dumps(result))
'''
    command = 'python3 - ' + ' '.join(shlex.quote(v) for v in [data.remote, data.plan['runtime_remote'], data.plan['image']])
    result = subprocess.run(['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=10', HOST, command],
                            input=script, capture_output=True, text=True, encoding='utf-8', timeout=120)
    if result.returncode:
        raise RuntimeError('Remote immutable-asset inspection failed: ' + result.stderr[-500:])
    snapshot = json.loads(result.stdout)
    assert snapshot['machine'] == 'aarch64' and snapshot['image'] == data.plan['image']
    for part in ['harness', 'packages', 'producers', 'fixtures']:
        assert snapshot[part] == hashes(data.bundle / part), ('Remote input mismatch', part)
    atomic_json(out, snapshot)
    return snapshot


def prepare(out):
    out.mkdir(parents=True, exist_ok=False)
    data = ResourceDataset(DATA)
    # Bind the scientific inputs to both the previously completed campaign and release.
    for part in ['tasks.json']:
        assert digest(DATA / part) == digest(RELEASE / part)
    original = json.loads((DATA / 'main_20261030T215327/frozen_bundle_manifest.json').read_text(encoding='utf-8'))
    manifest = hashes(data.bundle)
    assert original == manifest, 'Original scientific inputs changed'
    atomic_json(out / 'frozen_bundle_manifest.json', manifest)
    frozen = out / 'frozen_runner'; frozen.mkdir()
    for name in SOURCES:
        shutil.copy2(ROOT / 'scripts' / name, frozen / name)
    source_hashes = hashes(frozen)
    tests = subprocess.run([str(ROOT / '.venv/Scripts/python.exe'), '-m', 'unittest', 'discover',
        '-s', 'tests', '-p', 'test_*ablation*.py', '-v'], cwd=ROOT, capture_output=True, text=True, encoding='utf-8')
    (out / 'engineering_tests.txt').write_text(tests.stdout + tests.stderr, encoding='utf-8')
    assert tests.returncode == 0, 'Deterministic engineering gate failed'
    remote_snapshot(data, out / 'remote_assets_before.json')
    controls = []
    for task in data.tasks:
        for variant in ['base', 'fixed']:
            folder = out / 'runtime_preflight' / task / variant
            observed = data.observe(task, data.remote + '/packages/' + task + '/' + variant, 'all.json', folder)
            score = data.judge(task, observed, 'all.json')
            atomic_json(folder / 'score.json', score)
            assert score['execution_status'] == 'observed'
            assert (score['passed'] == score['n']) == (variant == 'fixed')
            controls.append({'task': task, 'variant': variant, 'passed': score['passed'], 'n': score['n']})
        print(json.dumps({'runtime_preflight': task, 'valid': True}), flush=True)
    atomic_json(out / 'runtime_preflight/summary.json', {'passed': True, 'controls': controls})
    blocks = [(task, rep) for task in data.tasks for rep in range(4)]
    rng = random.Random(2026110101); rng.shuffle(blocks)
    rows = []
    for block, (task, replicate) in enumerate(blocks):
        arms = ['exact', 'span']; rng.shuffle(arms)
        for arm in arms:
            identity = hashlib.sha256(f'editor-ablation-v1|{task}|{replicate}|{arm}'.encode()).hexdigest()[:12]
            rows.append({'episode': identity, 'family': task, 'project': data.tasks[task]['project'],
                         'model': 'google/gemma-4-31b-it', 'provider': 'coreweave/fp4',
                         'replicate': replicate, 'block': block, 'arm': arm, 'condition': 'ordinary'})
    plan = {'created_utc': datetime.now(timezone.utc).isoformat(), 'planned': 64, 'episodes': rows,
        'cap_usd': 10.0, 'authorization': 'User approved new paid experiments up to USD 10; existing submission must remain unchanged',
        'budget_ledger': 'budget_10usd.json', 'system': SYSTEM_FIXED,
        'source_hashes': source_hashes, 'task_metadata_sha256': digest(DATA / 'tasks.json'),
        'bundle_manifest_sha256': digest(out / 'frozen_bundle_manifest.json'),
        'oracle_hashes': hashes(DATA / 'oracle'), 'remote_snapshot_sha256': digest(out / 'remote_assets_before.json'),
        'prior_budget_sha256': digest(ROOT / '.cache/gemma_api_budget.json'),
        'submitted_pdf_sha256': digest(ROOT / 'output/pdf/historical-data-compatibility.pdf'),
        'tools': {arm: tools_for(arm) for arm in ['exact', 'span']},
        'intervention': 'Interface package: exact unique-text replacement versus version-checked line-span replacement. Not an isolated line-number or version-check effect.',
        'identical_information': 'Same initial tasks and read/search/source tools; read_source returns the identical version hash in both arms. Only apply_edits differs.',
        'limits': {'exploration_rounds': 24, 'exploration_max_tokens': 4096, 'exploration_temperature': 0.2,
                   'final_max_tokens': 2048, 'final_temperature': 0, 'context_stop_request_bytes': 120000,
                   'tool_result_bytes': 24000, 'max_tools_per_round': 6, 'concurrent_episodes': 2},
        'primary': 'Actual authorized source change AND all frozen contract observations pass AND independent source-policy acceptance. Optional regression tests are not sufficient.',
        'secondary': ['per-lineage acceptance', 'unchanged source', 'edited incomplete', 'edit rejections', 'usage/cost', 'context stops'],
        'completion_gate': '64 final reports, 64 recorded evaluations, no budget-truncated episodes, every candidate replayed unchanged; protocol/infrastructure incompleteness prevents effectiveness conclusion.',
        'transport_retries': 'Up to three identical requests for transient transport/429/5xx/no-choice failures; all charged conservatively and retained. No outcome-driven restarts.',
        'statistics': '32 attempts per arm nested in eight known lineages; report counts and each lineage. Repetitions are not independent defects; no general superiority/significance claim based on 64 independent samples.',
        'publication': 'No manuscript, repository release, or Kaggle update during the experiment. Full outcomes and independent review first.'}
    atomic_json(out / 'plan.json', plan)
    atomic_json(out / 'state.json', {'status': 'PREPARED_AWAITING_INDEPENDENT_PROTOCOL_REVIEW', 'paid_calls': 0})
    print(json.dumps({'prepared': str(out), 'planned': 64, 'paid_calls': 0}), flush=True)


def source_difference(data, row, folder):
    base = data.bundle / 'packages' / row['family'] / 'base'
    before = hashes(base)
    after = hashes(folder / 'candidate')
    changed = sorted(p for p in set(before) | set(after) if before.get(p) != after.get(p))
    allowed = data.tasks[row['family']]['allowed']
    effective = [p for p in changed if p in allowed and
                 (base / p).read_text(encoding='utf-8') != (folder / 'candidate' / p).read_text(encoding='utf-8')]
    return {'changed_paths': changed, 'normalized_source_changed_paths': effective, 'actual_source_change': bool(effective),
            'only_authorized_paths': all(p in allowed + ['tests/test_regression.py'] for p in changed),
            'candidate_manifest': after}


def seal(out):
    """Pre-spend engineering revision only; retains completed CPU checks."""
    assert not (out / 'budget_10usd.json').exists(), 'Cannot revise after paid execution'
    plan = json.loads((out / 'plan.json').read_text(encoding='utf-8'))
    if not (out / 'initial_pre_spend_plan.json').exists():
        atomic_json(out / 'initial_pre_spend_plan.json', plan)
    assert json.loads((out / 'runtime_preflight/summary.json').read_text(encoding='utf-8'))['passed']
    for name in SOURCES:
        shutil.copy2(ROOT / 'scripts' / name, out / 'frozen_runner' / name)
    plan['source_hashes'] = hashes(out / 'frozen_runner')
    plan['test_hashes'] = {name: digest(ROOT / 'tests' / name) for name in TESTS}
    plan['data_plan_sha256'] = digest(DATA / 'plan.json')
    plan['runtime_preflight_sha256'] = digest(out / 'runtime_preflight/summary.json')
    plan['recovery_policy'] = 'No automatic episode restart after a crash or non-transient engineering failure. Retain partial artifacts and stop new blocks. Any engineering-only recovery must undergo independent review; never discard model actions or select by semantic outcome.'
    plan['engineering_revision_utc'] = datetime.now(timezone.utc).isoformat()
    tests = subprocess.run([str(ROOT / '.venv/Scripts/python.exe'), '-m', 'unittest', 'discover',
        '-s', 'tests', '-p', 'test_*ablation*.py', '-v'], cwd=ROOT, capture_output=True, text=True, encoding='utf-8')
    (out / 'engineering_tests.txt').write_text(tests.stdout + tests.stderr, encoding='utf-8')
    assert tests.returncode == 0
    atomic_json(out / 'plan.json', plan)
    print(json.dumps({'sealed_plan_sha256': digest(out / 'plan.json'), 'paid_calls': 0}), flush=True)


def run(out):
    plan = json.loads((out / 'plan.json').read_text(encoding='utf-8'))
    review = json.loads((out / 'independent_protocol_review.json').read_text(encoding='utf-8'))
    assert review['decision'] == 'PASS' and review['plan_sha256'] == digest(out / 'plan.json')
    for name, sha in plan['source_hashes'].items():
        assert digest(ROOT / 'scripts' / name) == sha, ('Executor changed after freeze', name)
    for name, sha in plan['test_hashes'].items():
        assert digest(ROOT / 'tests' / name) == sha
    assert digest(DATA / 'plan.json') == plan['data_plan_sha256']
    assert digest(DATA / 'tasks.json') == plan['task_metadata_sha256']
    assert digest(out / 'runtime_preflight/summary.json') == plan['runtime_preflight_sha256']
    data = ResourceDataset(DATA)
    assert hashes(data.bundle) == json.loads((out / 'frozen_bundle_manifest.json').read_text(encoding='utf-8'))
    assert hashes(DATA / 'oracle') == plan['oracle_hashes']
    assert remote_snapshot(data, out / 'remote_assets_start.json') == json.loads((out / 'remote_assets_before.json').read_text(encoding='utf-8'))
    assert not (out / 'results.json').exists(), 'No automatic outcome-driven restart'
    client = AblationClient(out)
    atomic_json(out / 'state.json', {'status': 'RUNNING', 'started_utc': datetime.now(timezone.utc).isoformat()})
    results = []
    def worker(row):
        target = out / 'episodes' / row['episode']
        def factory(task, folder, **kwargs):
            return AblationGateway(data, task, folder, arm=row['arm'], **kwargs)
        try:
            result = run_episode(client, row, target, gateway_factory=factory, tools=plan['tools'][row['arm']], system=plan['system'])
            result.update(source_difference(data, row, target))
            result['budget_truncated'] = (target / 'exploration_budget_stop.json').exists()
            result['source_policy_verdict'] = 'pending_independent_review'
        except Exception as exc:
            result = {**row, 'protocol_complete': False, 'execution_status': 'engineering_error',
                      'budget_truncated': isinstance(exc, BudgetLimit), 'error': type(exc).__name__ + ': ' + str(exc)}
            atomic_json(target / 'engineering_error.json', result)
        atomic_json(target / 'campaign_result.json', result)
        return result
    # Each randomized block contains both arms of one task/replicate; two CPU containers maximum.
    with ThreadPoolExecutor(max_workers=2) as pool:
        for start in range(0, 64, 2):
            futures = [pool.submit(worker, row) for row in plan['episodes'][start:start+2]]
            results.extend(f.result() for f in futures)
            atomic_json(out / 'results.json', results)
            print(json.dumps({'recorded': len(results), 'planned': 64,
                'finalized': sum(bool(r['protocol_complete']) for r in results),
                'observed': sum(r['execution_status'] == 'observed' for r in results),
                'accounted_usd': client.account['accounted_usd']}), flush=True)
            if any(r.get('budget_truncated') or r['execution_status'] == 'engineering_error' for r in results):
                break
    complete = len(results) == 64 and all(r['protocol_complete'] and r['execution_status'] == 'observed' and not r['budget_truncated'] for r in results)
    assert digest(ROOT / '.cache/gemma_api_budget.json') == plan['prior_budget_sha256']
    assert digest(ROOT / 'output/pdf/historical-data-compatibility.pdf') == plan['submitted_pdf_sha256']
    stable = remote_snapshot(data, out / 'remote_assets_after.json') == json.loads((out / 'remote_assets_before.json').read_text(encoding='utf-8'))
    summary = {'planned': 64, 'recorded': len(results), 'protocol_and_evaluation_complete': complete,
               'immutable_assets_unchanged': stable, 'full_replay_complete': False, 'independent_review_complete': False,
               'cost': {k:v for k,v in client.account.items() if k != 'requests'}, 'episodes': results}
    atomic_json(out / 'summary.json', summary)
    atomic_json(out / 'state.json', {'status': 'AWAITING_FULL_REPLAY_AND_REVIEW' if complete and stable else 'INCOMPLETE_NO_EFFECTIVENESS_CONCLUSION'})
    print(json.dumps({k:v for k,v in summary.items() if k not in ('episodes', 'cost')}), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['prepare', 'seal', 'run'])
    parser.add_argument('out', type=Path)
    args = parser.parse_args()
    {'prepare': prepare, 'seal': seal, 'run': run}[args.action](args.out.resolve())
