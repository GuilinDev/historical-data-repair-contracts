"""Free reproduction of three complete repair-policy controls. Requires bootstrap."""
import argparse
import json
from pathlib import Path
import time
import uuid

from compat import candidate_from_patch
from engine.dataset import ResourceDataset
from engine.common import write_json

TASK='jsonpickle_dtype_634'
CASES={'official_widest':(84,84),'document_exclusion_v2':(84,24),'negative_explicit_float_narrowing':(0,0)}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--case',choices=['all',*CASES],default='all')
    parser.add_argument('--output',type=Path)
    args=parser.parse_args();root=Path(__file__).resolve().parent;data=ResourceDataset(root)
    data.verify_integrity();data.installation()
    out=(args.output or root/'results'/('multisolution_'+time.strftime('%Y%m%dT%H%M%SZ',time.gmtime())+'_'+uuid.uuid4().hex[:6])).resolve()
    out.mkdir(parents=True,exist_ok=False)
    reference={r['id']:r for r in json.loads((root/'oracle'/f'{TASK}.json').read_text(encoding='utf-8'))['records']}
    rows=[]
    for name,(expected_admissible,expected_exact) in CASES.items():
        if args.case not in ('all',name):continue
        case=out/name;case.mkdir();patch=root/'extensions/multisolution/patches'/(name+'.patch')
        candidate=candidate_from_patch(data,TASK,patch,case/'patch')
        score=data.evaluate(TASK,candidate,'all.json',case/'evaluation')
        observed=json.loads((case/'evaluation/observations.json').read_text(encoding='utf-8'))
        exact=sum(r.get('status')=='observed' and r.get('semantics')==reference[r['id']]['semantics']
                  and r.get('restart_semantics')==reference[r['id']]['restart_semantics'] for r in observed['records'])
        complete=score['execution_status']=='observed' and len({r['id'] for r in observed['records']})==84
        met=complete and score['passed']==expected_admissible and exact==expected_exact
        row={'policy':name,'execution_status':score['execution_status'],'observed_records':len(observed['records']),
             'admissible_passed':score['passed'],'raw_exact_gold_passed':exact,'n':score['n'],'expectations_met':met}
        rows.append(row);write_json(out/'progress.json',rows);print(json.dumps(row),flush=True)
    summary={'passed':all(r['expectations_met'] for r in rows),'rows':rows,'api_calls':0,'gpu':False,
             'interpretation':'Constructed repair-policy calibration on one lineage. Exact-gold is a naive observation-equality comparator, not authentic upstream tests.60 differing rows reuse5 historical files in12 processes.'}
    write_json(out/'summary.json',summary)
    print(json.dumps(summary,indent=2))
    return 0 if summary['passed'] else 1


if __name__=='__main__':raise SystemExit(main())
