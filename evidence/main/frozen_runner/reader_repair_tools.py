"""Bounded repair tools and CPU-container grading with host-frozen oracles."""
from __future__ import annotations
from collections import defaultdict
import ast
import hashlib
import json
import pickletools
from pathlib import Path
import shlex
import shutil
import subprocess
import tarfile
import time
import uuid

from freeze_and_build import ROOT, write_json
from model_pilot import schema
from prepare_reader_repairs import HOST, IMAGE

DATA = ROOT / "results" / "reader_repairs" / "20261030T185431"
PLAN = json.loads((DATA/"plan.json").read_text(encoding="utf-8"))
TASKS = json.loads((DATA/"tasks.json").read_text(encoding="utf-8"))
REMOTE = PLAN["remote_root"]
TOOLS = [schema("list_sources","List this candidate's Python source files."),
 schema("read_source","Read source lines from the candidate.",{"path":{"type":"string"},"start_line":{"type":"integer"},"line_count":{"type":"integer"}},["path"]),
 schema("search_source","Literal source search, up to 16 matches.",{"query":{"type":"string"}},["query"]),
 schema("source_symbols","Find source definitions by optional name substring, with line spans. Useful for generated reader factories.",{"path":{"type":"string"},"query":{"type":"string"}},["path"]),
 schema("historical_cases","Describe the public historical data examples and their producer versions."),
 schema("historical_artifact","Inspect a PUBLIC example's exact pickle opcode/state structure without loading it. Hidden inputs are unavailable.",{"id":{"type":"string"}},["id"]),
 schema("apply_edits","Atomically apply exact text replacements. Only task-authorized source and tests/test_regression.py may change. Empty old text creates a NEW file only.",
   {"edits":{"type":"array","items":{"type":"object","properties":{"path":{"type":"string"},"old":{"type":"string"},"new":{"type":"string"}},"required":["path","old","new"],"additionalProperties":False}}},["edits"]),
 schema("run_checks","Actually execute public checks inside a 1-CPU,1GB,no-network container. current checks current behavior; legacy checks historical examples and current behavior. Hidden inputs are not accessible.",
   {"suite":{"type":"string","enum":["current","legacy"]}},["suite"])]


def command(cmd, *, timeout=70):
    return subprocess.run(cmd,capture_output=True,text=True,encoding="utf-8",timeout=timeout)


def evaluate_remote(family, candidate_remote, manifest, out, *, generated_tests=False):
    out=Path(out);out.mkdir(parents=True,exist_ok=True)
    name="gemma-reader-"+uuid.uuid4().hex[:16]
    fixture_root="fixtures" if manifest=="all.json" and not generated_tests else "fixtures_public"
    args=["timeout","--kill-after=5s","45s","docker","run","--rm","--name",name,"--network","none",
      "--cpus","1","--memory","1g","--pids-limit","128","--read-only","--cap-drop","ALL",
      "--security-opt","no-new-privileges","--user","65534:65534","--tmpfs","/tmp:rw,nosuid,nodev,size=64m",
      "-v",candidate_remote+":/candidate:ro","-v",REMOTE+"/harness:/harness:ro",
      "-v",REMOTE+"/"+fixture_root+"/"+family+":/fixtures/"+family+":ro",
      IMAGE,"python","-I"]
    if generated_tests:args += ["/harness/run_generated_tests.py"]
    else:args += ["/harness/observe.py","--candidate","/candidate","--support","/harness/support",
                  "--family",family,"--manifest","/fixtures/"+family+"/"+manifest]
    start=time.perf_counter()
    p=command(["ssh","-o","BatchMode=yes","-o","ConnectTimeout=10",HOST,shlex.join(args)])
    (out/"stdout.txt").write_text(p.stdout,encoding="utf-8")
    (out/"stderr.txt").write_text(p.stderr,encoding="utf-8")
    write_json(out/"execution.json",{"command":args,"exit_code":p.returncode,"wall_seconds":time.perf_counter()-start})
    if p.returncode in (124,137):
        cleanup=command(["ssh",HOST,"docker rm -f "+name])
        write_json(out/"owned_container_cleanup.json",{"container":name,"exit":cleanup.returncode})
    if generated_tests:
        return {"present":True,"executed":True,"exit_code":p.returncode,"stdout":p.stdout[-6000:],"stderr":p.stderr[-2000:]}
    if p.returncode:
        return {"status":"execution_error","exit_code":p.returncode,"stderr":p.stderr[-2000:],"records":[]}
    try:
        result=json.loads(p.stdout)
    except json.JSONDecodeError:
        return {"status":"invalid_program_output","records":[],"stdout":p.stdout[-2000:]}
    result["status"]="observed"
    write_json(out/"observations.json",result)
    return result


def judge(family, actual, scope):
    expected=json.loads((DATA/"oracle"/f"{family}.json").read_text(encoding="utf-8"))
    manifest=json.loads((DATA/"bundle"/"fixtures"/family/scope).read_text(encoding="utf-8"))
    selected={r["id"] for r in manifest}
    expected={r["id"]:r for r in expected["records"] if r["id"] in selected}
    found={r["id"]:r for r in actual.get("records",[])}
    records=[]
    for identity,reference in expected.items():
        row=found.get(identity)
        initial=bool(row and row.get("status")=="observed" and row.get("semantics")==reference["semantics"])
        restart=bool(row and row.get("restart_semantics")==reference["restart_semantics"])
        records.append({"id":identity,"group":reference["group"],"passed":initial and restart,
                        "initial_semantics_passed":initial,"restart_semantics_passed":restart,"actual":row})
    groups=defaultdict(lambda:{"passed":0,"n":0})
    for row in records:
        groups[row["group"]]["n"]+=1
        groups[row["group"]]["passed"]+=row["passed"]
    return {"execution_status":actual["status"],"passed":sum(r["passed"] for r in records),"n":len(records),
            "groups":dict(groups),"records":records}


def preflight():
    oracle=DATA/"oracle";oracle.mkdir(exist_ok=True)
    results=[]
    for family in TASKS:
        gold=evaluate_remote(family,REMOTE+"/packages/"+family+"/fixed","all.json",DATA/"preflight"/family/"fixed")
        assert gold["status"]=="observed" and all(r["status"]=="observed" and "restart_semantics" in r for r in gold["records"]),gold
        # Freeze the unmodified upstream repaired implementation BEFORE model edits.
        write_json(oracle/f"{family}.json",gold)
        baseline=evaluate_remote(family,REMOTE+"/packages/"+family+"/base","all.json",DATA/"preflight"/family/"base")
        fixed_judge=judge(family,gold,"all.json")
        base_judge=judge(family,baseline,"all.json")
        assert fixed_judge["passed"]==fixed_judge["n"]
        assert base_judge["passed"]<base_judge["n"]
        # The task includes repairing persisted hash-cache behavior. Fresh
        # construction must be unchanged; re-saving that value may still fail
        # before the upstream fix, and is correctly tested as a separate axis.
        assert all(r["initial_semantics_passed"] for r in base_judge["records"] if r["group"]=="fresh_api")
        write_json(DATA/"preflight"/family/"base_score.json",base_judge)
        write_json(DATA/"preflight"/family/"fixed_score.json",fixed_judge)
        results.append({"family":family,"base":base_judge["groups"],"fixed":fixed_judge["groups"]})
    write_json(DATA/"preflight"/"summary.json",{"passed":True,"oracle_frozen_before_model":True,"tasks":results})
    print(json.dumps(results))


class RepairGateway:
    def __init__(self,family,out,*,resume=False):
        self.family=family;self.out=Path(out).resolve();self.out.mkdir(parents=True,exist_ok=True)
        self.source=self.out/"candidate"
        if not resume:shutil.copytree(DATA/"bundle"/"packages"/family/"base",self.source)
        else:assert self.source.is_dir()
        edits=self.out/"applied_edits.json"
        self.edits=json.loads(edits.read_text(encoding="utf-8")) if resume and edits.exists() else []
        self.checks=max([int(p.name.split('_')[1]) for p in self.out.glob("check_[0-9][0-9]")]+[0])

    def initial(self,condition):
        family=self.family;task=TASKS[family]
        if family=="packaging":
            requirement="Repair packaging.version.Version so genuine objects saved by packaging 25.0 and 26.0 load under this 26.1 reader, retain their values/comparisons/hash consistency and can be saved again then loaded in a fresh process. Preserve current Version construction, comparison, hashing and public API."
        else:
            requirement="Repair attrs 22.2's generated state restoration so existing slotted/frozen/cache-hash instances saved by attrs 21.4 load with all field values preserved. Keep current dict-based state, frozen behavior, equality/hash and save/reload in fresh processes working. Application classes have int count and str name fields; the same class definitions are present at load time."
        task_input={"request":requirement,"allowed_source_paths":task["allowed"],"optional_regression_test_path":"tests/test_regression.py",
            "rules":"Do not modify dependencies, producer data, fixtures, oracle, tools or metadata. Change only authorized source; avoid identity/value hardcoding. A final answer is required even if unable.",
            "existing_source_files":self.call("list_sources",{}),"public_historical_examples":self.call("historical_cases",{})}
        if family=="attrs":
            task_input["application_class_definitions"]=(DATA/"bundle/harness/support/compat_models.py").read_text(encoding="utf-8")
        if condition=="reminder":task_input["reminder"]="Use real historical data in validation. A new-reader self-roundtrip alone cannot establish the stated old-data contract."
        if condition=="evidence":task_input["initial_historical_check"]=self.call("run_checks",{"suite":"legacy"})
        return task_input

    def call(self,name,args):
        if name=="list_sources":return [p.relative_to(self.source).as_posix() for p in sorted(self.source.rglob("*.py"))]
        if name=="historical_cases":
            rows=json.loads((DATA/"bundle"/"fixtures"/self.family/"visible.json").read_text(encoding="utf-8"))
            return [{k:r[k] for k in ("id","group","producer","input","warm","artifact") if k in r} for r in rows]
        if name=="historical_artifact":
            rows=json.loads((DATA/"bundle"/"fixtures"/self.family/"visible.json").read_text(encoding="utf-8"))
            matches=[r for r in rows if r["id"]==args["id"] and r["group"]=="historical"]
            if len(matches)!=1:raise ValueError("Only public historical example IDs may be inspected")
            row=matches[0]
            p=DATA/"bundle"/"fixtures"/row["artifact"].removeprefix("/fixtures/")
            raw=p.read_bytes()
            assert hashlib.sha256(raw).hexdigest()==row["sha256"]
            return {"id":row["id"],"producer":row["producer"],"input":row["input"],"sha256":row["sha256"],
                "opcodes":[{"offset":pos,"opcode":op.name,"argument":repr(arg)[:180]} for op,arg,pos in pickletools.genops(raw)]}
        if name=="source_symbols":
            p=(self.source/args["path"]).resolve()
            if not p.is_relative_to(self.source.resolve()) or p.suffix!=".py":raise ValueError("Source path outside task")
            tree=ast.parse(p.read_text(encoding="utf-8"));result=[];query=args.get("query","").lower()
            def visit(node,scope):
                if isinstance(node,(ast.FunctionDef,ast.AsyncFunctionDef,ast.ClassDef)):
                    name=".".join(scope+[node.name])
                    if query in name.lower():result.append({"name":name,"line":node.lineno,"end_line":node.end_lineno})
                    scope=scope+[node.name]
                for child in ast.iter_child_nodes(node):visit(child,scope)
            visit(tree,[])
            return result
        if name=="read_source":
            path=(self.source/args["path"]).resolve()
            if not path.is_relative_to(self.source.resolve()) or path.suffix!=".py":raise ValueError("Source path outside task")
            lines=path.read_text(encoding="utf-8").splitlines();start=max(1,int(args.get("start_line",1)))
            count=min(160,max(1,int(args.get("line_count",100))))
            return {"path":path.relative_to(self.source).as_posix(),"total_lines":len(lines),
                    "lines":[{"line":i+1,"text":lines[i]} for i in range(start-1,min(len(lines),start-1+count))]}
        if name=="search_source":
            query=str(args["query"]).lower()
            if not query or len(query)>100:raise ValueError("Query length 1-100 required")
            matches=[]
            for p in sorted(self.source.rglob("*.py")):
                for i,line in enumerate(p.read_text(encoding="utf-8").splitlines(),1):
                    if query in line.lower():
                        matches.append({"path":p.relative_to(self.source).as_posix(),"line":i,"text":line[:500]})
                        if len(matches)==16:return matches
            return matches
        if name=="apply_edits":
            pending={}
            for edit in args["edits"]:
                rel=edit["path"]
                if rel not in TASKS[self.family]["allowed"]+["tests/test_regression.py"]:raise ValueError("Unauthorized edit path")
                p=self.source/rel;text=pending.get(rel,p.read_text(encoding="utf-8") if p.exists() else None)
                if edit["old"]=="":
                    if text is not None:raise ValueError("Empty old allowed only for a new file")
                    changed=edit["new"]
                else:
                    if text is None or text.count(edit["old"])!=1:raise ValueError("Old text must match exactly once")
                    changed=text.replace(edit["old"],edit["new"],1)
                compile(changed,str(p),"exec")
                pending[rel]=changed
            for rel,text in pending.items():
                p=self.source/rel;p.parent.mkdir(parents=True,exist_ok=True);p.write_text(text,encoding="utf-8")
            self.edits.extend(args["edits"]);write_json(self.out/"applied_edits.json",self.edits)
            return {"applied_files":list(pending),"syntax":"valid"}
        if name=="run_checks":
            self.checks+=1
            manifest="current_visible.json" if args["suite"]=="current" else "visible.json"
            result=self.evaluate(manifest,self.out/f"check_{self.checks:02d}")
            return {"execution_status":result["execution_status"],"passed":result["passed"],"n":result["n"],
                "groups":result["groups"],"failures":[r for r in result["records"] if not r["passed"]][:4],
                "submitted_tests":result["submitted_tests"]}
        raise ValueError("Unknown tool")

    def evaluate(self,manifest,out):
        label="candidate_"+uuid.uuid4().hex[:16]
        out=Path(out);out.mkdir(parents=True,exist_ok=True)
        archive=out/"candidate.tar"
        def public_mode(info):info.mode=0o755 if info.isdir() else 0o644;return info
        with tarfile.open(archive,"w") as tar:
            for p in self.source.iterdir():tar.add(p,arcname=p.name,filter=public_mode)
        remote=REMOTE+"/"+label
        for cmd in [["ssh",HOST,"mkdir -p "+remote],["scp","-q",str(archive),HOST+":"+remote+"/candidate.tar"],
                    ["ssh",HOST,"tar -xf "+remote+"/candidate.tar -C "+remote]]:
            p=command(cmd);assert p.returncode==0,p.stderr
        actual=evaluate_remote(self.family,remote,manifest,out)
        result=judge(self.family,actual,manifest)
        if (self.source/"tests/test_regression.py").exists():
            result["submitted_tests"]=evaluate_remote(self.family,remote,"visible.json",out/"submitted_tests",generated_tests=True)
        else:result["submitted_tests"]={"present":False,"executed":False}
        write_json(out/"score.json",result)
        return result


if __name__=="__main__":preflight()
