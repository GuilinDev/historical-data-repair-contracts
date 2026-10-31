"""Verify every plotted/table outcome against retained execution records; no API."""
import hashlib
import json
from pathlib import Path
import re
import runpy
import contextlib
import io

HERE=Path(__file__).resolve().parent
PUBLIC=HERE.parent if (HERE.parent/'tasks.json').exists() else HERE.parent/'release/historical-data-compatibility-v0.1.0'


def main():
    paper=json.loads((HERE/'final_content.json').read_text(encoding='utf-8'))
    data=json.loads((HERE/'figure_data.json').read_text(encoding='utf-8'))
    manifest=json.loads((HERE/'figure_manifest.json').read_text(encoding='utf-8'))
    read=lambda name:json.loads((PUBLIC/name).read_text(encoding='utf-8'))
    for name,digest in data['source_sha256'].items():assert hashlib.sha256((PUBLIC/name).read_bytes()).hexdigest()==digest,name
    main=read('evidence/main/summary.json');audit=read('evidence/independent_review/main_audit.json')
    diag=read('evidence/independent_review/main_diagnostics.json');cold=read('validation/cold_reproduction_summary.json')
    accepted=set(audit['source_policy_accepted_episode_ids'])
    assert audit['decision']=='PASS' and not audit['failures']
    assert main['planned']==main['finalized']==main['observed_evaluations']==32
    assert len(accepted)==4 and set(paper['source_policy_accepted_episode_ids'])==accepted
    tasks=read('tasks.json');dmap={r['episode']:r for r in diag['episodes']}
    emap={r['episode']:r for r in main['episodes']}
    assert len(data['episodes'])==len(emap)==32
    for row in data['episodes']:
        raw=emap[row['episode']];detail=dmap[row['episode']]
        for k in ['family','model','replicate','passed','n']:assert row[k]==raw[k]
        assert row['accepted']==(row['episode'] in accepted)==raw['all_checks_passed']
        assert row['unchanged']==(not detail['changed_source_paths'])
        assert row['context_stop']==detail['context_stop']
    assert sum(r['unchanged'] for r in data['episodes'])==16
    assert sum(r['context_stop'] for r in data['episodes'])==15
    assert sum(r['accepted'] and r['context_stop'] for r in data['episodes'])==1
    assert sum(not r['unchanged'] and not r['accepted'] for r in data['episodes'])==12
    assert paper['table_group_rows']==[2,7]
    assert paper['tables'][0][2][0]=='Development-exposed lineages (4)'
    assert paper['tables'][0][7][0]=='Newly added lineages (4)'
    for i,r in enumerate(data['rows']):
        controls={x['control']:x for x in cold['controls']['rows'] if x['task']==r['task']}
        meta=tasks[r['task']]
        assert r['n']==meta['cases'] and r['files']==meta['unique_historical_bytes']
        for k in ['base','fixed','reference']:assert r[k]==controls[k]['passed']
        row=paper['tables'][0][i+3+(i>=4)]
        assert row[3:]==[f'{r["public"]}/{r["files"]}',f'{r["historical"]}/{r["fresh"]}/{r["current"]}',
                         f'{r["base"]}/{r["n"]}',f'{r["fixed"]}/{r["n"]}\neach',f'{r["A"]}/2',f'{r["B"]}/2']
        for name,model in [('A','google/gemma-4-26b-a4b-it'),('B','google/gemma-4-31b-it')]:
            assert r[name]==sum(x['episode'] in accepted for x in main['episodes'] if x['family']==r['task'] and x['model']==model)
    assert paper['tables'][0][-1][3:]==['17/65','131/53/68','99/252','252/252\neach','1/16','3/16']
    multi=read('validation/multisolution_entrypoint.json')
    for derived,observed in zip(data['calibration'],multi['rows']):
        assert (derived['admissible'],derived['exact'],derived['n'])==(observed['admissible_passed'],observed['raw_exact_gold_passed'],observed['n'])
    collision=read('evidence/collision/summary.json')['witness']
    a=(PUBLIC/'evidence/collision/04_float32.json').read_bytes();b=(PUBLIC/'evidence/collision/05_float64.json').read_bytes()
    assert a==b and len(a)==collision['bytes']==267
    assert hashlib.sha256(a).hexdigest()==collision['sha256']
    assert collision['both_genuine_self_roundtrips'] and collision['distinct_original_widths']==[4,8]
    for f,current in zip(paper['figures'],manifest['figures']):
        assert f['label_text']==current['label_text'] and f['caption']==current['caption']
        assert hashlib.sha256((HERE/current['pdf']).read_bytes()).hexdigest()==current['pdf_sha256']
    assert manifest['data_sha256']==hashlib.sha256((HERE/'figure_data.json').read_bytes()).hexdigest()
    cost=sum(float(json.loads(p.read_text(encoding='utf-8'))['usage']['cost']) for p in (PUBLIC/'evidence/main/episodes').glob('*/*.response.json'))
    assert abs(cost-paper['main_cost_usd'])<1e-9
    example=paper['contract_example']
    contract=read('evidence/independent_review/clarity_revision_20261031/contract_example_audit.json')
    for anchor in contract['source_anchors']:
        assert hashlib.sha256((PUBLIC/anchor['path']).read_bytes()).hexdigest()==anchor['sha256'],anchor['path']
    case=contract['public_case'];meta=case['metadata']
    assert hashlib.sha256((PUBLIC/meta['artifact']).read_bytes()).hexdigest()==meta['sha256']
    assert case['initial']['values'][1]==23 and meta['input']['delta']==91211
    assert case['after_mutation']['values'][1]==91234
    oracle=next(r for r in read('oracle/joblib_endian_1561.json')['records'] if r['id']==meta['id'])
    assert oracle['semantics']['initial']==case['initial']
    assert oracle['semantics']['continued']==case['after_mutation']
    assert 'a.flat[1] += 91211' in example['code'] and '    assert b.flat[1] == 91234' in example['code']
    text=[paper['title'],paper['subtitle'],paper['author'],paper['abstract'],paper['disclosure'],*paper['references'],*paper['captions'],*paper['table_notes'],
          example['title'],example['explanation'],*example['code'],example['caption'],
          paper['software_acknowledgment'],paper['conclusion']['title'],*paper['conclusion']['points']]
    body=[paper['abstract']]
    for s in paper['sections']:text += [s['title'],*s['paragraphs']];body+=s['paragraphs']
    body.append(paper['disclosure'])
    text += [str(c) for table in paper['tables'] for row in table for c in row]
    for f in paper['figures']:text += [f['caption'],f['label_text']]
    words=len(re.findall(r'\S+','\n'.join(text)))
    assert words==paper['conservative_word_count']<=3000
    assert [int(re.match(r'\[(\d+)\]',r).group(1)) for r in paper['references']]==list(range(1,16))
    cites={int(x) for m in re.finditer(r'\[(\d+(?:,\d+)*)\]','\n'.join(body)) for x in m.group(1).split(',')}
    assert cites==set(range(1,16)) and '\\u00f6' not in '\n'.join(text)
    # Recompute the new trajectory claims directly from the public retained logs.
    verifier=PUBLIC/'evidence/independent_review/clarity_revision_20261031/verify_trajectory.py'
    with contextlib.redirect_stdout(io.StringIO()):
        trajectory=runpy.run_path(str(verifier))['main']()
    assert trajectory['passed'] and trajectory['counts']['finalized_and_observed']==32
    print(json.dumps({'passed':True,'source_files_hash_checked':len(data['source_sha256']), 'figure_count':3,
                      'table_lineages':8,'episodes_verified':32,'accepted':4,'references':15,
                      'word_count_including_references_tables_figure_labels_captions':words,
                      'trajectory_public_inputs_verified':len(trajectory['verified_public_inputs']),
                      'contract_source_anchors_verified':len(contract['source_anchors']),
                      'main_api_reported_usd':cost,'api_calls':0,'docker_calls':0},indent=2))


if __name__=='__main__':main()
