"""Check the displayed v0.1.2 tables, figures and counts using public records only."""
from pathlib import Path
import contextlib,hashlib,io,json,re,runpy,sys
HERE=Path(__file__).resolve().parent
ROOT=HERE.parent if (HERE.parent/'tasks.json').exists() else HERE.parents[1]/'release/historical-data-compatibility-v0.1.2'
read=lambda p:json.loads(p.read_text(encoding='utf-8'))
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()

def word_count(p):
    text=[p['title'],p['subtitle'],p['author'],p['abstract'],p['disclosure'],*p['references'],*p['captions'],*p['table_notes'],
          p['software_acknowledgment'],p['conclusion']['title'],*p['conclusion']['points']]
    e=p['contract_example'];text += [e['title'],e['explanation'],*e['code'],e['caption']]
    for s in p['sections']:text += [s['title'],*s['paragraphs']]
    text += [str(c) for table in p['tables'] for row in table for c in row]
    for f in p['figures']:text += [f['caption'],f['label_text']]
    t=p['calibration_table'];text += [t['caption'],*t['headers']]+[c for row in t['rows'] for c in row]
    return len(re.findall(r'\S+','\n'.join(text)))

def main():
    p=read(HERE/'final_content.json');data=read(HERE/'figure_data.json');manifest=read(HERE/'figure_manifest.json')
    # Independently retain the original baseline checks and every old displayed datum.
    legacy=ROOT/'supplement/v0.1.1_paper'
    old=runpy.run_path(str(legacy/'verify_numbers.py'))['main']
    old.__globals__.update(HERE=legacy,PUBLIC=ROOT)
    with contextlib.redirect_stdout(io.StringIO()):old()
    previous=read(legacy/'final_content.json')
    assert p['tables']==previous['tables'] and p['contract_example']==previous['contract_example']
    assert p['source_policy_accepted_episode_ids']==previous['source_policy_accepted_episode_ids']
    a=read(ROOT/'evidence/editing_ablation/analysis.json')
    assert data['controlled']==a['by_lineage'] and p['new_study']==a['arms']
    assert [a['arms'][arm]['complete_acceptances'] for arm in ['exact','span']]==[10,4]
    case=ROOT/'extensions/approval_signature';variants=['upstream_base','upstream_fixed','reject_all_legacy','unguarded_legacy','reject_control_chars']
    for row,v in zip(p['calibration_table']['rows'],variants):
        records=read(case/'observations'/v/'score.json')['records'];assert len(records)==54 and all(r['execution_complete'] for r in records)
        valid=sum(r['passed'] and r['expected'] for r in records);invalid=sum(r['passed'] and not r['expected'] for r in records)
        assert row[1:]==[f'{valid}/37',f'{invalid}/17',f'{valid+invalid}/54']
    assert p['calibration_table']['rows']==data['signature_calibration']
    for name,expected in data['new_source_sha256'].items():assert sha(ROOT/name)==expected
    assert p['figures']==manifest['figures'] and manifest['data_sha256']==sha(HERE/'figure_data.json')
    for f in p['figures']:assert sha(HERE/f['pdf'])==f['pdf_sha256']
    assert len(p['figures'])==3 and len(p['references'])==17
    assert [int(re.match(r'\[(\d+)\]',r)[1]) for r in p['references']]==list(range(1,18))
    cites={int(x) for s in p['sections'] for t in s['paragraphs'] for m in re.finditer(r'\[(\d+(?:,\d+)*)\]',t) for x in m.group(1).split(',')}
    assert cites==set(range(1,18))
    words=word_count(p);assert words==p['conservative_word_count']<3000
    assert p['version']=='0.1.2' and 'experimental data unchanged' not in p['revision']
    print(json.dumps({'passed':True,'original_attempts':32,'original_accepted':4,'new_attempts':64,'new_exact_accepted':10,
        'new_span_accepted':4,'separate_signature_scenarios':54,'figures':3,'tables':2,'references':17,
        'conservative_inclusive_word_count':words,'api_calls':0,'docker_calls':0},indent=2))

if __name__=='__main__':main()
