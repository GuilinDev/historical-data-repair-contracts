"""Small free CPU integration controls; separate from the 756 dataset observations."""
import json
from pathlib import Path
import subprocess
import sys
import time
import uuid

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from compat import reference_candidate
from engine.dataset import ResourceDataset
from engine.common import write_json

def main():
    dataset=ResourceDataset(ROOT);dataset.verify_integrity();dataset.installation()
    out=ROOT/'results'/('interface_'+time.strftime('%Y%m%dT%H%M%SZ',time.gmtime())+'_'+uuid.uuid4().hex[:6]);out.mkdir(parents=True)
    sources={
        'positive':"from pathlib import Path\ndef test_public_boundary():\n    assert not Path('/fixtures/attrs_slot_state_1085/all.json').exists()\n    assert not Path('/oracle').exists()\n    assert Path('/fixtures/attrs_slot_state_1085/visible.json').is_file()\n",
        'negative':"def test_fails():\n    assert False\n",
        'collection_error':"import deliberately_missing_test_dependency\n",
        'no_tests':"value = 1\n",
    }
    rows=[]
    for name,source in sources.items():
        case=out/name;case.mkdir();candidate=reference_candidate(dataset,'attrs_slot_state_1085',case)
        test=candidate/'tests/test_regression.py';test.parent.mkdir(exist_ok=True);test.write_text(source,encoding='utf-8',newline='\n')
        dest=case/'cli';args=[sys.executable,'-B',str(ROOT/'compat.py'),'verify','--task','attrs_slot_state_1085','--candidate',str(candidate),'--scope','public','--output',str(dest)]
        cp=subprocess.run(args,capture_output=True,text=True,encoding='utf-8',timeout=180)
        (case/'cli_stdout.txt').write_text(cp.stdout.replace(str(ROOT),'<release>'),encoding='utf-8');(case/'cli_stderr.txt').write_text(cp.stderr.replace(str(ROOT),'<release>'),encoding='utf-8')
        result=json.loads((dest/'summary.json').read_text());report=result['submitted_tests']['test_report']
        semantic=result['execution_status']=='observed' and result['passed']==result['n']
        if name=='positive':expected=report and report['test_bodies_run']==1 and report['body_passed']==1 and report['exit_code']==0
        elif name=='negative':expected=report and report['test_bodies_run']==1 and report['body_failed']==1 and report['exit_code']==1
        elif name=='collection_error':expected=report and report['test_bodies_run']==0 and report['collection_errors']>0 and report['exit_code']==2
        else:expected=report and report['test_bodies_run']==0 and report['exit_code']==5
        rows.append({'control':name,'cli_exit':cp.returncode,'semantic_checks_passed':semantic,'test_report':report,'expected_test_behavior':bool(expected),'passed':cp.returncode==0 and semantic and bool(expected)})
        print(json.dumps(rows[-1]),flush=True)
    # Exercise the public CLI patch path as well as the API adapter used above.
    dest=out/'unified_patch_cli'
    cp=subprocess.run([sys.executable,'-B',str(ROOT/'compat.py'),'verify','--task','attrs_slot_state_1085','--candidate',str(ROOT/'reference_repairs/attrs_slot_state_1085/reference.patch'),'--scope','public','--output',str(dest)],capture_output=True,text=True,encoding='utf-8',timeout=180)
    result=json.loads((dest/'summary.json').read_text()) if (dest/'summary.json').exists() else {}
    rows.append({'control':'unified_patch_cli','cli_exit':cp.returncode,'passed':cp.returncode==0 and result.get('passed')==result.get('n') and result.get('n',0)>0})
    summary={'passed':all(r['passed'] for r in rows),'rows':rows,'dataset_control_denominator_separate':True,'api_calls':0,'gpu':False,'receipts_directory':out.relative_to(ROOT).as_posix()}
    write_json(out/'summary.json',summary);write_json(ROOT/'validation/cold_interface_summary.json',summary)
    print(json.dumps(summary,indent=2));return 0 if summary['passed'] else 1

if __name__=='__main__':raise SystemExit(main())
