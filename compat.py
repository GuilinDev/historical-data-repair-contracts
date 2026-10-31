#!/usr/bin/env python3
"""Free local-Docker verification; no API client or credential imports."""
import argparse
import json
from pathlib import Path
import re
import shutil
import sys
import uuid

from engine.common import ROOT,read_json,sha,stamp,write_json
from engine.dataset import ResourceDataset,ResourceGateway,bootstrap

def candidate_from_patch(dataset,task,patch,out):
    gateway=ResourceGateway(dataset,task,out)
    text=patch.read_text(encoding='utf-8')
    if patch.suffix=='.json':
        edits=json.loads(text)
        if not isinstance(edits,dict) or set(edits)!={'edits'}:raise ValueError('Patch JSON must contain exactly an edits list')
        gateway.call('apply_edits',edits)
        return gateway.source
    # Strict ordinary unified diff: no shell/git subprocess, rename, binary or
    # symlink application. All output paths are validated before any write.
    lines=text.splitlines();i=0;edits=[];seen=set()
    while i<len(lines):
        if not lines[i].startswith('--- '):
            if lines[i].startswith(('GIT binary patch','Binary files','rename from','rename to','new mode','old mode','new file mode 120000')):raise ValueError('Unsupported patch operation')
            i+=1;continue
        oldname=lines[i][4:].split('\t',1)[0];i+=1
        if i>=len(lines) or not lines[i].startswith('+++ '):raise ValueError('Missing unified diff target')
        newname=lines[i][4:].split('\t',1)[0];i+=1
        oldname=oldname[2:] if oldname.startswith('a/') else oldname
        newname=newname[2:] if newname.startswith('b/') else newname
        if newname=='/dev/null':raise ValueError('Source deletion is not supported by this patch reader; provide a whitelisted candidate directory')
        if oldname not in ('/dev/null',newname):raise ValueError('Renames are not supported')
        if newname not in dataset.tasks[task]['allowed']+['tests/test_regression.py'] or newname in seen:raise ValueError('Unauthorized/duplicate unified-diff path')
        seen.add(newname);path=gateway.source/newname
        original=path.read_text(encoding='utf-8') if path.exists() else None
        if (original is None)!=(oldname=='/dev/null'):raise ValueError('Patch create/existing state mismatch')
        source=(original or '').splitlines();output=[];pos=0;hunks=0;newline=True
        while i<len(lines) and not lines[i].startswith(('--- ','diff --git ')):
            if not lines[i].startswith('@@ '):i+=1;continue
            m=re.fullmatch(r'@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@.*',lines[i])
            if not m:raise ValueError('Malformed unified hunk')
            start=int(m[1]);oldcount=int(m[2] or 1);newcount=int(m[4] or 1);index=start-1 if oldcount else start
            if index<pos or index>len(source):raise ValueError('Hunk position outside source')
            output.extend(source[pos:index]);pos=index;i+=1;oldused=newused=0
            while i<len(lines) and not lines[i].startswith(('@@ ','--- ','diff --git ')):
                line=lines[i]
                if line.startswith('\\ No newline at end of file'):
                    newline=False;i+=1;continue
                if not line or line[0] not in ' +-':break
                flag,value=line[0],line[1:]
                if flag in ' -':
                    if pos>=len(source) or source[pos]!=value:raise ValueError('Patch context does not exactly match')
                    pos+=1;oldused+=1
                if flag in ' +':output.append(value);newused+=1
                i+=1
                if oldused==oldcount and newused==newcount:break
            if (oldused,newused)!=(oldcount,newcount):raise ValueError('Hunk line counts mismatch')
            hunks+=1
        if not hunks:raise ValueError('No text hunks for patch path')
        output.extend(source[pos:]);new='\n'.join(output)+('\n' if newline and output else '')
        edits.append({'path':newname,'old':original or '', 'new':new})
    if not edits:raise ValueError('No supported source patches found')
    gateway.call('apply_edits',{'edits':edits})
    return gateway.source

def reference_candidate(dataset,task,out):
    target=out/'candidate';shutil.copytree(dataset.bundle/'packages'/task/'base',target)
    folder=dataset.path/'reference_repairs'/task
    for row in read_json(folder/'files.json'):
        if row['path'] not in dataset.tasks[task]['allowed']:raise ValueError('Reference changes forbidden path')
        src=folder/'files'/row['path'];dest=target/row['path']
        if sha(src)!=row['sha256']:raise ValueError('Reference source hash mismatch')
        if row['base_sha256'] is not None and sha(dest)!=row['base_sha256']:raise ValueError('Reference base identity mismatch')
        dest.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(src,dest)
    dataset.candidate_audit(task,target);return target

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,default=ROOT,help='Relocated release directory')
    sub=parser.add_subparsers(dest='command',required=True)
    boot=sub.add_parser('bootstrap',help='Fresh offline wheel installation into a new local directory')
    boot.add_argument('--pull',action='store_true',help='Fetch the pinned Docker image first (network needed only here)')
    sub.add_parser('integrity',help='Verify frozen content and wheel hashes without Docker')
    sub.add_parser('list',help='List eight tasks')
    verify=sub.add_parser('verify',help='Verify a candidate directory, unified diff, or exact-edit JSON')
    verify.add_argument('--task',required=True);verify.add_argument('--candidate',type=Path,required=True)
    verify.add_argument('--scope',choices=['all','public','current'],default='all');verify.add_argument('--output',type=Path)
    repro=sub.add_parser('reproduce',help='Execute frozen calibration controls; no oracle regeneration')
    repro.add_argument('--task',action='append');repro.add_argument('--controls',default='base,fixed,reference');repro.add_argument('--output',type=Path)
    args=parser.parse_args();dataset=ResourceDataset(args.root)
    if args.command=='list':
        print(json.dumps({k:{'project':v['project'],'base':v['base'],'cases':v['cases']} for k,v in dataset.tasks.items()},indent=2));return 0
    if args.command=='integrity':print(json.dumps(dataset.verify_integrity()));return 0
    if args.command=='bootstrap':print(json.dumps(bootstrap(dataset.path,pull=args.pull),indent=2));return 0
    dataset.verify_integrity();dataset.installation()
    out=(args.output or dataset.path/'results'/(args.command+'_'+stamp()+'_'+uuid.uuid4().hex[:6])).resolve()
    if out.exists():raise ValueError('Output directory already exists; use a new one to preserve receipts')
    out.mkdir(parents=True)
    if args.command=='verify':
        if args.task not in dataset.tasks:raise ValueError('Unknown task')
        source=args.candidate.resolve()
        if source.is_dir():
            dataset.candidate_audit(args.task,source);candidate=out/'candidate';shutil.copytree(source,candidate)
        elif source.is_file():candidate=candidate_from_patch(dataset,args.task,source,out)
        else:raise ValueError('Candidate does not exist')
        manifest={'all':'all.json','public':'visible.json','current':'current_visible.json'}[args.scope]
        result=dataset.evaluate(args.task,candidate,manifest,out/'evaluation')
        brief={k:result[k] for k in ('execution_status','passed','n','groups','submitted_tests')}
        write_json(out/'summary.json',brief);print(json.dumps(brief,indent=2))
        return 0 if result['execution_status']=='observed' and result['passed']==result['n'] else 1
    controls=args.controls.split(',');tasks=args.task or list(dataset.tasks)
    if not controls or any(c not in ('base','fixed','reference') for c in controls):raise ValueError('Controls must be comma-separated base,fixed,reference')
    if len(set(controls))!=len(controls) or any(t not in dataset.tasks for t in tasks):raise ValueError('Duplicate controls or unknown task')
    rows=[]
    for task in tasks:
        for control in controls:
            case=out/task/control;case.mkdir(parents=True)
            candidate=reference_candidate(dataset,task,case) if control=='reference' else dataset.bundle/'packages'/task/control
            score=dataset.evaluate(task,candidate,'all.json',case/'evaluation',trusted_control=control in ('base','fixed'))
            observed=read_json(case/'evaluation/observations.json') if (case/'evaluation/observations.json').exists() else {'records':[]}
            completed=score['execution_status']=='observed' and len(observed['records'])==dataset.tasks[task]['cases'] and len({r['id'] for r in observed['records']})==dataset.tasks[task]['cases']
            expected=(score['passed']==score['n']) if control!='base' else any(not r['passed'] for r in score['records'] if r['group']=='historical')
            row={'task':task,'control':control,'execution_status':score['execution_status'],'n':score['n'],'observed_records':len(observed['records']),'passed':score['passed'],'groups':score['groups'],'completed':completed,'control_expectation_met':completed and expected,'oracle_sha256':score['oracle_sha256']}
            rows.append(row);write_json(out/'progress.json',rows);print(json.dumps(row),flush=True)
    summary={'controls':controls,'tasks':tasks,'evaluations':len(rows),'observations':sum(r['observed_records'] for r in rows),'expected_observations':sum(dataset.tasks[t]['cases'] for t in tasks)*len(controls),'all_completed':all(r['completed'] for r in rows),'all_control_expectations_met':all(r['control_expectation_met'] for r in rows),'rows':rows,'oracle_regenerated':False,'network':'none','api_calls':0,'gpu':False,'runtime_installation':dataset.installation()}
    write_json(out/'summary.json',summary);print(json.dumps({k:v for k,v in summary.items() if k not in ('rows','runtime_installation')},indent=2))
    return 0 if summary['all_control_expectations_met'] else 1

if __name__=='__main__':
    try:raise SystemExit(main())
    except (ValueError,RuntimeError,FileNotFoundError) as exc:
        print(json.dumps({'error':str(exc),'engineering_failure':True}),file=sys.stderr);raise SystemExit(2)
