"""Task-scoped producer reading, source edits and host-frozen CPU grading."""
from collections import defaultdict
import ast
import hashlib
import json
from pathlib import Path
import pickletools
import shlex
import shutil
import tarfile
import time
import uuid

from freeze_and_build import ROOT,write_json
from model_pilot import schema
from reader_repair_tools import RepairGateway,TOOLS as CORE_TOOLS,command
from prepare_reader_repairs import HOST,IMAGE


TOOLS=CORE_TOOLS+[
    schema("read_historical_source","Read immutable producer-version source, not a fixed reader. Same line limits as read_source.",
        {"version":{"type":"string"},"path":{"type":"string"},"start_line":{"type":"integer"},"line_count":{"type":"integer"}},["version","path"]),
    schema("search_historical_source","Literal search of immutable old producer source, up to16matches.",
        {"version":{"type":"string"},"query":{"type":"string"}},["version","query"])]


class TransferDataset:
    def __init__(self,path):
        self.path=Path(path).resolve();self.bundle=self.path/"bundle"
        self.plan=json.loads((self.path/"plan.json").read_text(encoding="utf-8"))
        self.tasks=json.loads((self.path/"tasks.json").read_text(encoding="utf-8"))
        self.remote=self.plan["remote_root"]

    def observe(self,family,remote,manifest,out,*,generated_tests=False):
        out=Path(out);out.mkdir(parents=True,exist_ok=True)
        name="gemma-transfer-"+uuid.uuid4().hex[:16]
        fixtures="fixtures" if manifest=="all.json" and not generated_tests else "fixtures_public"
        args=["timeout","--kill-after=5s","60s","docker","run","--rm","--name",name,"--network","none",
              "--cpus","1","--memory","1g","--pids-limit","128","--read-only","--cap-drop","ALL",
              "--security-opt","no-new-privileges","--user","65534:65534","--tmpfs","/tmp:rw,nosuid,nodev,size=64m",
              "-v",remote+":/candidate:ro","-v",self.remote+"/harness:/harness:ro",
              "-v",self.remote+"/runtime_deps:/runtime:ro",
              "-v",self.remote+"/"+fixtures+"/"+family+":/fixtures/"+family+":ro",IMAGE,"python","-I"]
        if generated_tests:args += ["/harness/run_generated_tests.py"]
        else:args += ["/harness/observe.py","--candidate","/candidate","--support","/harness/support","--runtime","/runtime",
                      "--family",family,"--manifest","/fixtures/"+family+"/"+manifest]
        start=time.perf_counter()
        p=command(["ssh","-o","BatchMode=yes","-o","ConnectTimeout=10",HOST,shlex.join(args)],timeout=85)
        (out/"stdout.txt").write_text(p.stdout,encoding="utf-8");(out/"stderr.txt").write_text(p.stderr,encoding="utf-8")
        write_json(out/"execution.json",{"command":args,"exit":p.returncode,"wall_seconds":time.perf_counter()-start})
        if p.returncode in (124,137):
            cleaned=command(["ssh",HOST,"docker rm -f "+name]);write_json(out/"owned_cleanup.json",{"container":name,"exit":cleaned.returncode})
        if generated_tests:
            return {"present":True,"invoked":True,"executed":p.returncode in (0,1,2,5),"exit_code":p.returncode,
                    "stdout":p.stdout[-6000:],"stderr":p.stderr[-2000:]}
        if p.returncode:return {"status":"execution_error","records":[],"exit":p.returncode,"stderr":p.stderr[-1500:]}
        try:result=json.loads(p.stdout)
        except json.JSONDecodeError:return {"status":"invalid_program_output","records":[],"stdout":p.stdout[-2000:]}
        result["status"]="observed";write_json(out/"observations.json",result);return result

    def judge(self,family,actual,manifest):
        reference=json.loads((self.path/"oracle"/(family+".json")).read_text(encoding="utf-8"))
        selected={r["id"] for r in json.loads((self.bundle/"fixtures"/family/manifest).read_text(encoding="utf-8"))}
        found={r["id"]:r for r in actual.get("records",[])};records=[];groups=defaultdict(lambda:{"passed":0,"n":0})
        for expected in reference["records"]:
            if expected["id"] not in selected:continue
            row=found.get(expected["id"])
            initial=bool(row and row.get("status")=="observed" and row.get("semantics")==expected["semantics"])
            restart=bool(row and row.get("restart_semantics")==expected["restart_semantics"])
            result={"id":expected["id"],"group":expected["group"],"initial_semantics_passed":initial,"restart_semantics_passed":restart,
                    "passed":initial and restart,"actual":row}
            records.append(result);groups[result["group"]]["n"]+=1;groups[result["group"]]["passed"]+=result["passed"]
        return {"execution_status":actual["status"],"passed":sum(r["passed"] for r in records),"n":len(records),"groups":dict(groups),"records":records}

    def preflight(self):
        results=[]
        for family in self.tasks:
            gold=self.observe(family,self.remote+"/packages/"+family+"/fixed","all.json",self.path/"preflight"/family/"gold")
            assert gold["status"]=="observed" and all(r["status"]=="observed" and "restart_semantics" in r for r in gold["records"]),gold
            # Independent input-derived invariants prevent freezing a merely
            # self-consistent gold implementation with the wrong original data.
            inputs={r["id"]:r for r in json.loads((self.bundle/"fixtures"/family/"all.json").read_text(encoding="utf-8"))}
            for row in gold["records"]:
                spec=inputs[row["id"]]["input"];sem=row["semantics"];restart=row["restart_semantics"]
                if family=="jsonpickle":
                    for observed in (sem,restart):
                        assert observed["initial"]["values"]==spec["data"]
                        assert observed["initial"]["columns"]==spec["columns"]
                        assert observed["initial"]["columns_name"]==spec.get("columns_name")
                        assert observed["initial"]["dtypes"]==["object"]*len(spec["columns"])
                        assert all(t=="str" for types in observed["initial"]["value_types"] for t in types)
                else:
                    assert sem["payload"]==spec["payload"]
                    expected=spec["payload"].copy();expected["continued"]="first"
                    keys=sorted(k for k in expected if k!="continued")
                    if keys:del expected[keys[0]]
                    assert sem["database_payload"]==restart["payload"]==expected
                    expected["continued"]="second";keys=sorted(k for k in expected if k!="continued")
                    if keys:del expected[keys[0]]
                    assert restart["database_payload"]==expected
                    assert sem["dirty"] and sem["session_modified"] and restart["dirty"] and restart["session_modified"]
            write_json(self.path/"oracle"/(family+".json"),gold)
            base=self.observe(family,self.remote+"/packages/"+family+"/base","all.json",self.path/"preflight"/family/"base")
            gold_score=self.judge(family,gold,"all.json");base_score=self.judge(family,base,"all.json")
            assert gold_score["passed"]==gold_score["n"]
            assert base_score["groups"]["historical"]["passed"]==0
            assert all(r["passed"] for r in base_score["records"] if r["group"]!="historical")
            write_json(self.path/"preflight"/family/"gold_score.json",gold_score)
            write_json(self.path/"preflight"/family/"base_score.json",base_score)
            results.append({"family":family,"gold":gold_score["groups"],"base":base_score["groups"]})
        summary={"passed":True,"oracles_frozen_before_model":True,"tasks":results}
        write_json(self.path/"preflight/summary.json",summary);return summary


class TransferGateway(RepairGateway):
    def __init__(self,dataset,family,out,*,resume=False):
        self.dataset=dataset;self.family=family;self.task=dataset.tasks[family]
        self.out=Path(out).resolve();self.out.mkdir(parents=True,exist_ok=True);self.source=self.out/"candidate"
        assert not resume,"New frozen transfer attempts do not resume explored model conversations"
        shutil.copytree(dataset.bundle/"packages"/family/"base",self.source)
        self.edits=[];self.checks=0

    def initial(self,condition):
        task={"request":self.task["request"],"allowed_source_paths":self.task["allowed"],
              "optional_regression_test_path":"tests/test_regression.py","historical_source_versions":self.task["producers"],
              "rules":"Only candidate source and optional regression test may change. Historical data/source are immutable. No fixed source, hidden examples, expected answers or tools may be inspected or changed.",
              "existing_source_files":self.call("list_sources",{}),"public_historical_examples":self.call("historical_cases",{})}
        if self.family=="sqlalchemy":task["application_class_definitions"]=(self.dataset.bundle/"harness/support/compat_records.py").read_text(encoding="utf-8")
        if condition=="evidence":task["initial_historical_check"]=self.call("run_checks",{"suite":"legacy"})
        return task

    def call(self,name,args):
        if name in ("list_sources","read_source","search_source","source_symbols"):return super().call(name,args)
        if name in ("read_historical_source","search_historical_source"):
            version=args["version"]
            if version not in self.task["producers"]:raise ValueError("Only declared historical producers are available")
            root=self.dataset.bundle/"producers"/self.family/version
            if name=="read_historical_source":
                path=(root/args["path"]).resolve()
                if not path.is_relative_to(root.resolve()) or path.suffix!=".py":raise ValueError("Historical source path outside task")
                lines=path.read_text(encoding="utf-8").splitlines();start=max(1,int(args.get("start_line",1)));count=min(160,max(1,int(args.get("line_count",100))))
                return {"version":version,"path":args["path"],"total_lines":len(lines),"lines":[{"line":i+1,"text":lines[i]} for i in range(start-1,min(len(lines),start-1+count))]}
            query=str(args["query"]).lower()
            if not query or len(query)>100:raise ValueError("Search query length1-100")
            result=[]
            for path in sorted(root.rglob("*.py")):
                for i,line in enumerate(path.read_text(encoding="utf-8").splitlines(),1):
                    if query in line.lower():
                        result.append({"version":version,"path":path.relative_to(root).as_posix(),"line":i,"text":line[:500]})
                        if len(result)==16:return result
            return result
        visible=json.loads((self.dataset.bundle/"fixtures"/self.family/"visible.json").read_text(encoding="utf-8"))
        if name=="historical_cases":return [{k:r[k] for k in ("id","group","producer","input","artifact") if k in r} for r in visible]
        if name=="historical_artifact":
            rows=[r for r in visible if r["id"]==args["id"] and r["group"]=="historical"]
            if len(rows)!=1:raise ValueError("Only public historical artifact IDs may be inspected")
            row=rows[0];path=self.dataset.bundle/"fixtures"/Path(row["artifact"].removeprefix("/fixtures/"));raw=path.read_bytes()
            assert hashlib.sha256(raw).hexdigest()==row["sha256"]
            if self.family=="jsonpickle":state={"json":json.loads(raw.decode("utf-8"))}
            else:state={"opcodes":[{"offset":pos,"opcode":op.name,"argument":repr(arg)[:180]} for op,arg,pos in pickletools.genops(raw)]}
            return {"id":row["id"],"producer":row["producer"],"sha256":row["sha256"],**state}
        if name=="apply_edits":
            pending={}
            for edit in args["edits"]:
                rel=edit["path"]
                if rel not in self.task["allowed"]+["tests/test_regression.py"]:raise ValueError("Unauthorized edit path")
                path=self.source/rel;text=pending.get(rel,path.read_text(encoding="utf-8") if path.exists() else None)
                if edit["old"]=="":
                    if text is not None:raise ValueError("Empty old text is only valid for new files")
                    changed=edit["new"]
                else:
                    if text is None or text.count(edit["old"])!=1:raise ValueError("Old text must match exactly once")
                    changed=text.replace(edit["old"],edit["new"],1)
                compile(changed,str(path),"exec");pending[rel]=changed
            for rel,text in pending.items():
                path=self.source/rel;path.parent.mkdir(parents=True,exist_ok=True);path.write_text(text,encoding="utf-8")
            self.edits.extend(args["edits"]);write_json(self.out/"applied_edits.json",self.edits)
            return {"applied_files":list(pending),"syntax":"valid"}
        if name=="run_checks":
            self.checks+=1;manifest="current_visible.json" if args["suite"]=="current" else "visible.json"
            result=self.evaluate(manifest,self.out/f"check_{self.checks:02d}")
            return {"execution_status":result["execution_status"],"passed":result["passed"],"n":result["n"],"groups":result["groups"],
                    "failures":[r for r in result["records"] if not r["passed"]][:4],"submitted_tests":result["submitted_tests"]}
        raise ValueError("Unknown tool")

    def evaluate(self,manifest,out):
        out=Path(out);out.mkdir(parents=True,exist_ok=True);archive=out/"candidate.tar"
        def mode(info):info.mode=0o755 if info.isdir() else 0o644;return info
        with tarfile.open(archive,"w") as tar:
            for path in self.source.iterdir():tar.add(path,arcname=path.name,filter=mode)
        remote=self.dataset.remote+"/candidate_"+uuid.uuid4().hex[:16]
        for args in [["ssh",HOST,"mkdir -p "+remote],["scp","-q",str(archive),HOST+":"+remote+"/candidate.tar"],
                     ["ssh",HOST,"tar -xf "+remote+"/candidate.tar -C "+remote]]:
            p=command(args);assert p.returncode==0,p.stderr
        actual=self.dataset.observe(self.family,remote,manifest,out);result=self.dataset.judge(self.family,actual,manifest)
        if (self.source/"tests/test_regression.py").exists():
            result["submitted_tests"]=self.dataset.observe(self.family,remote,"visible.json",out/"submitted_tests",generated_tests=True)
        else:result["submitted_tests"]={"present":False,"executed":False}
        write_json(out/"score.json",result);return result
