"""Portable local-Docker dataset and tools. Candidate code never runs on host."""
import ast
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import time
import uuid

from .common import ROOT,inventory,read_json,redact,sha,stamp,write_json
from .frozen_judge import ResourceDataset as FrozenJudge
from .frozen_tools import ResourceGateway as FrozenGateway,TOOLS

class ResourceDataset(FrozenJudge):
    def __init__(self,path=ROOT):
        self.path=Path(path).resolve();self.bundle=self.path/'bundle'
        self.plan=read_json(self.path/'plan.json');self.tasks=read_json(self.path/'tasks.json')
        self.remote=str(self.bundle)  # Compatibility alias; all execution is local.
        self.integrity_checked=False

    def verify_integrity(self):
        expected=read_json(self.path/'data_integrity.json')['sha256']
        mismatches=[name for name,digest in expected.items() if not (self.path/name).is_file() or sha(self.path/name)!=digest]
        if mismatches:raise ValueError('Immutable release files changed: '+', '.join(mismatches[:8]))
        for dep in read_json(self.path/'dependencies.json'):
            if sha(self.path/dep['file'])!=dep['sha256']:raise ValueError('Dependency wheel hash mismatch: '+dep['file'])
        self.integrity_checked=True
        return {'files':len(expected),'passed':True}

    def installation(self):
        receipt=self.path/'.local/installation.json'
        if not receipt.exists():raise RuntimeError('Run python3 compat.py bootstrap first')
        install=read_json(receipt)
        if install['image']!=self.plan['image']:raise RuntimeError('Runtime image differs from frozen plan')
        return install

    def candidate_audit(self,task,candidate):
        base=inventory(self.bundle/'packages'/task/'base');actual=inventory(Path(candidate))
        changed=sorted(p for p in set(base)|set(actual) if base.get(p)!=actual.get(p))
        permitted=set(self.tasks[task]['allowed'])|{'tests/test_regression.py'}
        forbidden=[p for p in changed if p not in permitted]
        if forbidden:raise ValueError('Unauthorized candidate changes: '+', '.join(forbidden[:8]))
        for name in changed:
            p=Path(candidate)/name
            if p.is_file():ast.parse(p.read_text(encoding='utf-8'),filename=name)
        return {'whitelist_passed':True,'changed_paths':changed,'base_files':len(base),'candidate_files':len(actual),'candidate_sha256':actual,'scope':'Path/syntax validation only. General-repair admissibility still requires source review for fixture hardcoding or output fabrication.'}

    def observe(self,task,candidate,manifest,out,*,generated_tests=False):
        if not self.integrity_checked:self.verify_integrity()
        if task not in self.tasks:raise ValueError('Unknown task')
        if manifest not in ('all.json','visible.json','current_visible.json'):raise ValueError('Unknown manifest')
        install=self.installation();out=Path(out).resolve();out.mkdir(parents=True,exist_ok=True)
        candidate=Path(candidate).resolve()
        fixtures='fixtures' if manifest=='all.json' and not generated_tests else 'fixtures_public'
        name='historical-compat-'+uuid.uuid4().hex[:16]
        args=['docker','run','--rm','--name',name,'--platform',self.plan['platform'],'--network','none','--cpus','1','--memory','1g','--pids-limit','128','--read-only','--cap-drop','ALL','--security-opt','no-new-privileges','--user','65534:65534','--tmpfs','/tmp:rw,nosuid,nodev,size=128m','-e','OPENBLAS_NUM_THREADS=1','-e','OMP_NUM_THREADS=1','-e','NUMEXPR_NUM_THREADS=1','-v',str(candidate)+':/candidate:ro','-v',str(self.bundle/'harness')+':/harness:ro','-v',str(self.path/install['runtime_directory'])+':/runtime:ro','-v',str(self.path/install['test_dependency_directory'])+':/harness/test_deps:ro','-v',str(self.bundle/fixtures/task)+':/fixtures/'+task+':ro',self.plan['image'],'python','-I','-B']
        if generated_tests:args+=['/harness/run_generated_tests.py']
        else:args+=['/harness/observe.py','--candidate','/candidate','--support','/harness/support','--runtime','/runtime','--task',task,'--manifest','/fixtures/'+task+'/'+manifest]
        replacements={str(self.path):'<release>',str(candidate):'<candidate>',str(out):'<output>'}
        start=time.perf_counter();timed_out=False
        try:
            cp=subprocess.run(args,capture_output=True,text=True,encoding='utf-8',timeout=300 if not generated_tests else 90)
            code,stdout,stderr=cp.returncode,cp.stdout,cp.stderr
        except subprocess.TimeoutExpired as exc:
            timed_out=True;code=124
            stdout=exc.stdout or '';stderr=exc.stderr or ''
            if isinstance(stdout,bytes):stdout=stdout.decode('utf-8',errors='replace')
            if isinstance(stderr,bytes):stderr=stderr.decode('utf-8',errors='replace')
            cleanup=subprocess.run(['docker','rm','-f',name],capture_output=True,text=True,timeout=15)
            write_json(out/'owned_container_cleanup.json',{'container':name,'exit':cleanup.returncode})
        stdout=redact(stdout,replacements);stderr=redact(stderr,replacements)
        (out/'stdout.txt').write_text(stdout,encoding='utf-8');(out/'stderr.txt').write_text(stderr,encoding='utf-8')
        write_json(out/'execution.json',{'command':redact(args,replacements),'exit':code,'wall_seconds':time.perf_counter()-start,'timed_out':timed_out,'network':'none','gpu':False,'oracle_mounted':False,'fixture_scope':fixtures})
        if generated_tests:
            report=None;marker='__RESOURCE_TEST_REPORT__'
            if marker in stdout:
                try:report=json.loads(stdout.rsplit(marker,1)[1].strip())
                except json.JSONDecodeError:pass
            return {'present':True,'invoked':True,'executed':bool(report and report['test_bodies_run']>0),'collected':report['collected'] if report else None,'test_report':report,'passed':bool(report and code==0 and report['test_bodies_run']>0 and report['body_failed']==0),'exit_code':code,'stdout':stdout[-7000:],'stderr':stderr[-2000:]}
        if code:return {'status':'execution_error','records':[],'exit':code,'stderr':stderr[-2000:]}
        try:actual=json.loads(stdout)
        except json.JSONDecodeError:return {'status':'invalid_program_output','records':[],'stdout':stdout[-2000:]}
        actual['status']='observed';write_json(out/'observations.json',actual);return actual

    def evaluate(self,task,candidate,manifest,out,*,trusted_control=False):
        out=Path(out);out.mkdir(parents=True,exist_ok=True)
        if not trusted_control:write_json(out/'candidate_audit.json',self.candidate_audit(task,candidate))
        before=sha(self.path/'oracle'/(task+'.json'))
        actual=self.observe(task,candidate,manifest,out);result=self.judge(task,actual,manifest)
        if (Path(candidate)/'tests/test_regression.py').exists():result['submitted_tests']=self.observe(task,candidate,'visible.json',out/'submitted_tests',generated_tests=True)
        else:result['submitted_tests']={'present':False,'executed':False}
        assert sha(self.path/'oracle'/(task+'.json'))==before,'Frozen oracle changed'
        result['oracle_sha256']=before;write_json(out/'score.json',result);return result

class ResourceGateway(FrozenGateway):
    def evaluate(self,manifest,out):return self.dataset.evaluate(self.family,self.source,manifest,out)

def bootstrap(path=ROOT,*,pull=False):
    dataset=ResourceDataset(path);dataset.verify_integrity();root=dataset.path;plan=dataset.plan
    if platform.system()!='Linux' or platform.machine().lower() not in ('aarch64','arm64'):raise RuntimeError('This release requires native Linux ARM64 and local Docker')
    out=root/'.local'/('bootstrap_'+stamp()+'_'+uuid.uuid4().hex[:6]);out.mkdir(parents=True)
    command_n=0
    def run(args,timeout=300):
        nonlocal command_n
        command_n+=1;start=time.perf_counter()
        try:
            cp=subprocess.run(args,capture_output=True,text=True,encoding='utf-8',timeout=timeout)
            rec={'command':redact(args,{str(root):'<release>'}),'exit':cp.returncode,'stdout':redact(cp.stdout,{str(root):'<release>'}),'stderr':redact(cp.stderr,{str(root):'<release>'}),'wall_seconds':time.perf_counter()-start}
        except subprocess.TimeoutExpired:
            rec={'command':redact(args,{str(root):'<release>'}),'exit':124,'error':'timeout','wall_seconds':time.perf_counter()-start}
        write_json(out/f'{command_n:02d}_command.json',rec)
        if rec['exit']:raise RuntimeError('Bootstrap command failed; receipt retained: '+out.relative_to(root).as_posix()+f'/{command_n:02d}_command.json')
        return rec.get('stdout','')
    try:
        server=run(['docker','info','--format','{{.Architecture}}'],30).strip()
        if server not in ('aarch64','arm64'):raise RuntimeError('Docker daemon must be native ARM64')
        if pull:run(['docker','pull','--platform',plan['platform'],plan['image']],300)
        image=json.loads(run(['docker','image','inspect',plan['image'],'--format','{{json .}}'],30))
        if image['Architecture']!='arm64' or image['Id']!=plan['expected_image_id']:raise RuntimeError('Pinned image identity/architecture mismatch')
        for name,lock in (('runtime','runtime-requirements.lock'),('test_deps','test-requirements.lock')):
            dest=out/name;dest.mkdir()
            run(['docker','run','--rm','--platform',plan['platform'],'--network','none','--cpus','1','--memory','1g','--pids-limit','128','--read-only','--cap-drop','ALL','--security-opt','no-new-privileges','--user',str(os.getuid())+':'+str(os.getgid()),'--tmpfs','/tmp:rw,nosuid,nodev,size=128m','-e','PIP_DISABLE_PIP_VERSION_CHECK=1','-v',str(root/'wheelhouse')+':/wheels:ro','-v',str(root/lock)+':/requirements.lock:ro','-v',str(dest)+':/install:rw',plan['image'],'python','-m','pip','install','--no-index','--find-links=/wheels','--require-hashes','--no-deps','--no-cache-dir','--no-compile','--target','/install','-r','/requirements.lock'])
        code="import sys,json,platform;sys.path[:0]=['/runtime','/tests'];import numpy,pandas,pytest;print(json.dumps({'python':platform.python_version(),'architecture':platform.machine(),'numpy':numpy.__version__,'pandas':pandas.__version__,'pytest':pytest.__version__}))"
        identity=json.loads(run(['docker','run','--rm','--network','none','--cpus','1','--memory','512m','--read-only','--user','65534:65534','-e','OPENBLAS_NUM_THREADS=1','-v',str(out/'runtime')+':/runtime:ro','-v',str(out/'test_deps')+':/tests:ro',plan['image'],'python','-I','-B','-c',code],45))
        assert identity=={'python':'3.10.20','architecture':'aarch64','numpy':'1.26.4','pandas':'2.1.4','pytest':'8.4.2'},identity
        receipt={'passed':True,'image':plan['image'],'image_id':image['Id'],'platform':plan['platform'],'identity':identity,'runtime_directory':(out/'runtime').relative_to(root).as_posix(),'test_dependency_directory':(out/'test_deps').relative_to(root).as_posix(),'receipt_directory':out.relative_to(root).as_posix(),'fresh_target_directories':True,'install_network':'none','pip_cache':False,'image_pull_requested':pull,'prior_installs_reused':False}
        write_json(out/'summary.json',receipt);write_json(root/'.local/installation.json',receipt);return receipt
    except Exception as exc:
        write_json(out/'summary.json',{'passed':False,'error':redact(str(exc),{str(root):'<release>'}),'failure_preserved':True});raise
