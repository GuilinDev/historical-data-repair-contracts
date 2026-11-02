"""Derive manuscript tables and figure data from the released, audited records."""
import hashlib
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
PUBLIC = ROOT if (ROOT / 'tasks.json').exists() else ROOT / 'release/historical-data-compatibility-v0.1.0'


def main():
    sources = {}
    def read(name):
        raw = (PUBLIC / name).read_bytes()
        sources[name] = hashlib.sha256(raw).hexdigest()
        return json.loads(raw)
    tasks = read('tasks.json')
    main = read('evidence/main/summary.json')
    audit = read('evidence/independent_review/main_audit.json')
    diag = read('evidence/independent_review/main_diagnostics.json')
    cold = read('validation/cold_reproduction_summary.json')
    multi = read('validation/multisolution_entrypoint.json')
    collision = read('evidence/collision/summary.json')
    accepted = set(audit['source_policy_accepted_episode_ids'])
    assert main['planned'] == main['finalized'] == main['observed_evaluations'] == 32
    assert len(accepted) == 4 and not audit['failures']
    aliases = ['packaging', 'attrs', 'jsonpickle CSV', 'SQLAlchemy', 'joblib endian', 'joblib compressed', 'jsonpickle dtype', 'pandas']
    versions = ['25.0 / 26.0\n26.1 > 26.2','21.4.0\n22.2.0 > 23.1.0','3.3.0\n4.1.0 > 4.1.1',
                '1.4.37\n1.4.38 > 1.4.39','1.2.0\n1.4.2 > 1.5.0','1.2.0\n1.4.2 > 1.5.0',
                '4.1.1\n4.1.1 > e7d95a6','1.3.5\n2.1.0 > 2.1.4']
    obligations = ['Hash / comparison; collections','Slot state; cached hash','CSV text; mutated resave','MutableDict; two commits',
                   'Writable mmap; flush','Compressed load; raw mmap','Numeric kind / width','Frame operations; resave']
    models = ['google/gemma-4-26b-a4b-it', 'google/gemma-4-31b-it']
    dmap = {d['episode']: d for d in diag['episodes']}
    rows = []; episodes = []
    for i, (task, meta) in enumerate(tasks.items()):
        visible = read('bundle/fixtures/' + task + '/visible.json')
        public = len({r['sha256'] for r in visible if r['group'] == 'historical'})
        controls = {r['control']: r for r in cold['controls']['rows'] if r['task'] == task}
        split = controls['reference']['groups']
        counts = []
        for model in models:
            group = sorted((r for r in main['episodes'] if r['family'] == task and r['model'] == model), key=lambda r: r['replicate'])
            assert len(group) == 2
            counts.append(sum(r['episode'] in accepted for r in group))
            for r in group:
                d = dmap[r['episode']]
                episodes.append({k:r[k] for k in ['episode','family','model','replicate','passed','n']} | {
                    'accepted':r['episode'] in accepted, 'unchanged':not d['changed_source_paths'], 'context_stop':d['context_stop']})
        rows.append({'task': task, 'lineage': aliases[i], 'versions': versions[i], 'obligations': obligations[i],
                     'public': public, 'files': meta['unique_historical_bytes'], 'n': meta['cases'],
                     'historical':split['historical']['n'], 'fresh':split['fresh_api']['n'],
                     'current':split['current_roundtrip']['n'], 'base':controls['base']['passed'],
                     'fixed':controls['fixed']['passed'], 'reference':controls['reference']['passed'],
                     'A':counts[0], 'B':counts[1], 'development_exposed':i<4})
    labels={'official_widest':'Conservative backport','document_exclusion_v2':'Document exclusion v2',
            'negative_explicit_float_narrowing':'Explicit-width narrowing'}
    calibration=[]
    assert multi['passed'] and multi['completed_observations']==252
    for r in multi['rows']:
        assert r['execution_status']=='observed' and r['observed_records']==r['n']==84
        calibration.append({'policy':labels[r['policy']], 'admissible':r['admissible_passed'],
                            'exact':r['raw_exact_gold_passed'], 'n':r['n']})
    assert [(r['admissible'],r['exact']) for r in calibration]==[(84,84),(84,24),(0,0)]
    data = {'rows':rows, 'episodes':episodes, 'calibration':calibration, 'collision':collision,
            'source_sha256':sources, 'main_cost_usd':main['project_cost']['reported_usd'] - 0.5994684809999992,
            'accepted_episode_ids':sorted(accepted),
            'semantics':'Counts describe dependent observations and completed conversations, not independent bugs. Base is the fresh-installation control run; dtype baseline varies by process.'}
    assert sum(r['files'] for r in rows)==65 and sum(r['public'] for r in rows)==17
    assert sum(r['n'] for r in rows)==252 and sum(r['base'] for r in rows)==99
    assert sum(r['A'] for r in rows)==1 and sum(r['B'] for r in rows)==3
    (HERE / 'figure_data.json').write_text(json.dumps(data,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'rows':len(rows),'episodes':len(episodes),'source_files':len(sources),'api_calls':0}))


if __name__ == '__main__': main()
