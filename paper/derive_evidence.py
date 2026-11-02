"""Derive current figure data from preserved baseline and complete new records."""
from pathlib import Path
import hashlib,json
HERE=Path(__file__).resolve().parent;ROOT=HERE.parent
read=lambda p:json.loads(p.read_text(encoding='utf-8'))
def main():
    data=read(ROOT/'supplement/v0.1.1_paper/figure_data.json')
    path=ROOT/'evidence/editing_ablation/analysis.json';a=read(path)
    data['controlled']=a['by_lineage'];data['controlled_arms']=a['arms']
    case=ROOT/'extensions/approval_signature'
    names=[('upstream_base','Pre-fix upstream'),('upstream_fixed','Upstream v7 fix'),('reject_all_legacy','Reject all legacy'),('unguarded_legacy','Unrestricted fallback'),('reject_control_chars','Reject all control chars')]
    rows=[]
    for v,label in names:
        records=read(case/'observations'/v/'score.json')['records']
        valid=sum(r['passed'] and r['expected'] for r in records);invalid=sum(r['passed'] and not r['expected'] for r in records)
        rows.append([label,f'{valid}/37',f'{invalid}/17',f'{valid+invalid}/54'])
    data['signature_calibration']=rows
    data['new_source_sha256']={p.relative_to(ROOT).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in [path,case/'summary.json']}
    (HERE/'figure_data.json').write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
if __name__=='__main__':main()
