"""Portable task evaluator and general source tools for the final resource."""
import copy
import hashlib
import json
from pathlib import Path
import pickletools
import shlex
import shutil
import time
import uuid

from freeze_and_build import write_json
from model_pilot import schema
from prepare_reader_repairs import HOST
from reader_repair_tools import command
from transfer_task_tools import TransferDataset,TransferGateway,TOOLS as TRANSFER_TOOLS

TOOLS=[t for t in TRANSFER_TOOLS if t["function"]["name"]!="list_sources"]+[
    schema("list_sources","List up to200 source paths; optional prefix or substring query, with total match count.",
           {"prefix":{"type":"string"},"query":{"type":"string"}})]


class ResourceDataset(TransferDataset):
    def observe(self,task,remote,manifest,out,*,generated_tests=False):
        out=Path(out);out.mkdir(parents=True,exist_ok=True);name="gemma-resource-"+uuid.uuid4().hex[:16]
        fixtures="fixtures" if manifest=="all.json" and not generated_tests else "fixtures_public"
        seconds=240 if task=="jsonpickle_dtype_634" and not generated_tests else 90
        args=["timeout","--kill-after=5s",str(seconds)+"s","docker","run","--rm","--name",name,"--network","none",
            "--cpus","1","--memory","1g","--pids-limit","128","--read-only","--cap-drop","ALL","--security-opt","no-new-privileges",
            "--user","65534:65534","--tmpfs","/tmp:rw,nosuid,nodev,size=128m",
            "-e","OPENBLAS_NUM_THREADS=1","-e","OMP_NUM_THREADS=1","-e","NUMEXPR_NUM_THREADS=1",
            "-v",remote+":/candidate:ro","-v",self.remote+"/harness:/harness:ro","-v",self.plan["runtime_remote"]+":/runtime:ro",
            "-v",self.remote+"/"+fixtures+"/"+task+":/fixtures/"+task+":ro",self.plan["image"],"python","-I","-B"]
        if generated_tests:args += ["/harness/run_generated_tests.py"]
        else:args += ["/harness/observe.py","--candidate","/candidate","--support","/harness/support","--runtime","/runtime",
                      "--task",task,"--manifest","/fixtures/"+task+"/"+manifest]
        start=time.perf_counter()
        p=command(["ssh","-o","BatchMode=yes","-o","ConnectTimeout=10",HOST,shlex.join(args)],timeout=seconds+25)
        (out/"stdout.txt").write_text(p.stdout,encoding="utf-8");(out/"stderr.txt").write_text(p.stderr,encoding="utf-8")
        write_json(out/"execution.json",{"command":args,"exit":p.returncode,"wall_seconds":time.perf_counter()-start})
        if p.returncode in (124,137):
            cleaned=command(["ssh",HOST,"docker rm -f "+name]);write_json(out/"owned_cleanup.json",{"container":name,"exit":cleaned.returncode})
        if generated_tests:
            marker="__RESOURCE_TEST_REPORT__";report=None
            if marker in p.stdout:
                try:report=json.loads(p.stdout.rsplit(marker,1)[1].strip())
                except json.JSONDecodeError:pass
            return {"present":True,"invoked":True,"executed":bool(report and report["test_bodies_run"]>0),
                    "collected":report["collected"] if report else None,"test_report":report,
                    "passed":bool(report and p.returncode==0 and report["test_bodies_run"]>0 and report["body_failed"]==0),
                    "exit_code":p.returncode,"stdout":p.stdout[-7000:],"stderr":p.stderr[-2000:]}
        if p.returncode:return {"status":"execution_error","records":[],"exit":p.returncode,"stderr":p.stderr[-2000:]}
        try:result=json.loads(p.stdout)
        except json.JSONDecodeError:return {"status":"invalid_program_output","records":[],"stdout":p.stdout[-2000:]}
        result["status"]="observed";write_json(out/"observations.json",result);return result

    @staticmethod
    def normalize_numeric(row,metadata):
        result=copy.deepcopy(row)
        for key in ("semantics","restart_semantics"):
            for snapshot in result.get(key,{}).values():
                if not isinstance(snapshot,dict) or "dtypes" not in snapshot:continue
                for name in metadata.get("ambiguous_columns",[]):
                    dtype=snapshot["dtypes"].get(name)
                    original=metadata["input"]["numeric"][name]["dtype"]
                    minimum=int(''.join(c for c in original if c.isdigit()))//8
                    if dtype and dtype["itemsize"]>=minimum:dtype["itemsize"]="permitted_widening"
        return result

    def judge(self,task,actual,manifest):
        if task!="jsonpickle_dtype_634":return super().judge(task,actual,manifest)
        # Normalize only declared ambiguous historical code positions. Numeric
        # values, kind and every explicit/current dtype width remain exact.
        metadata={r["id"]:r for r in json.loads((self.bundle/"fixtures"/task/"all.json").read_text(encoding="utf-8"))}
        reference=json.loads((self.path/"oracle"/(task+".json")).read_text(encoding="utf-8"))
        normalized_actual={**actual,"records":[self.normalize_numeric(r,metadata[r["id"]]) for r in actual.get("records",[])]}
        selected={r["id"] for r in json.loads((self.bundle/"fixtures"/task/manifest).read_text(encoding="utf-8"))}
        found={r["id"]:r for r in normalized_actual["records"]};records=[];groups={}
        for raw in reference["records"]:
            if raw["id"] not in selected:continue
            expected=self.normalize_numeric(raw,metadata[raw["id"]]);row=found.get(raw["id"])
            initial=bool(row and row.get("status")=="observed" and row.get("semantics")==expected["semantics"])
            restart=bool(row and row.get("restart_semantics")==expected["restart_semantics"])
            record={"id":raw["id"],"group":raw["group"],"initial_semantics_passed":initial,"restart_semantics_passed":restart,
                    "passed":initial and restart,"actual":actual.get("records",[])[next(i for i,r in enumerate(actual["records"]) if r["id"]==raw["id"])] if row else None}
            records.append(record);group=groups.setdefault(raw["group"],{"n":0,"passed":0});group["n"]+=1;group["passed"]+=record["passed"]
        return {"execution_status":actual["status"],"passed":sum(r["passed"] for r in records),"n":len(records),"groups":groups,"records":records}

    def validate_gold_input(self,task,row,metadata):
        spec=metadata["input"];sem=row["semantics"];restart=row["restart_semantics"]
        if task=="packaging_version_1163":
            assert sem["equal_to_fresh"] and sem["hash_consistent"] and sem["dict_lookup"] and sem["set_lookup"]
            assert restart["equal_to_fresh"] and restart["hash_consistent"]
        elif task=="attrs_slot_state_1085":
            for value in (sem,restart):assert value["count"]==spec["count"] and value["name"]==spec["name"] and value["count_type"]=="int" and value["name_type"]=="str"
        elif task=="sqlalchemy_mutable_8133":
            assert sem["payload"]==spec["payload"]
            expected=spec["payload"].copy();expected["continued"]="first";keys=sorted(k for k in expected if k!="continued")
            if keys:del expected[keys[0]]
            assert sem["database_payload"]==restart["payload"]==expected
            expected["continued"]="second";keys=sorted(k for k in expected if k!="continued")
            if keys:del expected[keys[0]]
            assert restart["database_payload"]==expected
            assert sem["dirty"] and sem["session_modified"] and restart["dirty"] and restart["session_modified"]
        elif task.startswith("joblib_"):
            def reshape(values,shape):
                if len(shape)==1:return values
                step=len(values)//shape[0];return [reshape(values[i*step:(i+1)*step],shape[1:]) for i in range(shape[0])]
            expected=reshape(spec["values"],spec["shape"]);assert sem["initial"]["values"]==expected
            continued=list(spec["values"]);continued[1]+=spec["delta"];expected=reshape(continued,spec["shape"])
            assert sem["continued"]["values"]==restart["values"]==expected
            if task=="joblib_endian_1561" and metadata["group"]!="fresh_api":assert sem["inplace_restart"]["values"]==expected
            if task=="joblib_compressed_1681" and metadata["group"]!="fresh_api" and spec["compress"]==0:
                assert sem["is_memmap"] and sem["readonly"] and sem["readonly_mutation_blocked"]
        else:
            if "numeric" in spec:expected={k:v["values"] for k,v in spec["numeric"].items()}
            else:expected={str(k):[v[i] for v in spec["data"]] for i,k in enumerate(spec["columns"])}
            assert sem["initial"]["values"]==expected
            assert restart["initial"]["values"]==sem["continued"]["values"]
            if task=="pandas_pickle_56308":
                assert sem["selection"]==spec["data"][1][0]
                for k,total in sem["aggregates"].items():assert total==sum(r[spec["columns"].index(k)] for r in spec["data"])

    def preflight(self):
        results=[]
        for task in self.tasks:
            gold=self.observe(task,self.remote+"/packages/"+task+"/fixed","all.json",self.path/"preflight"/task/"gold")
            metadata={r["id"]:r for r in json.loads((self.bundle/"fixtures"/task/"all.json").read_text(encoding="utf-8"))}
            assert gold["status"]=="observed" and len(gold["records"])==len(metadata),gold
            for row in gold["records"]:
                assert row["status"]=="observed" and "restart_semantics" in row,row
                self.validate_gold_input(task,row,metadata[row["id"]])
            write_json(self.path/"oracle"/(task+".json"),gold)
            base=self.observe(task,self.remote+"/packages/"+task+"/base","all.json",self.path/"preflight"/task/"base")
            base_score=self.judge(task,base,"all.json");gold_score=self.judge(task,gold,"all.json")
            assert gold_score["passed"]==gold_score["n"]
            assert base_score["execution_status"]=="observed" and any(not r["passed"] for r in base_score["records"] if r["group"]=="historical")
            write_json(self.path/"preflight"/task/"base_score.json",base_score);write_json(self.path/"preflight"/task/"gold_score.json",gold_score)
            results.append({"task":task,"gold":gold_score["groups"],"base":base_score["groups"]})
            print(json.dumps(results[-1]),flush=True)
        summary={"passed":True,"oracles_frozen_before_model":True,"tasks":results}
        write_json(self.path/"preflight/summary.json",summary);return summary


class ResourceGateway(TransferGateway):
    def initial(self,condition):
        task={"request":self.task["request"],"allowed_source_paths":self.task["allowed"],
            "optional_regression_test_path":"tests/test_regression.py","historical_source_versions":self.task["producers"],
            "rules":"Implement a general library repair. Do not hardcode artifact values, IDs, application class names or property names. Only authorized source and optional regression test may change; fixtures, historical source, expected answers and harness are immutable.",
            "source_entrypoints":self.task["allowed"],"source_tree":self.call("list_sources",{"prefix":self.task["package"]+"/","query":"__init__"}),
            "public_historical_examples":self.call("historical_cases",{})}
        if self.family=="attrs_slot_state_1085":task["application_classes"]=(self.dataset.bundle/"harness/support/compat_models.py").read_text(encoding="utf-8")
        if self.family=="sqlalchemy_mutable_8133":task["application_classes"]=(self.dataset.bundle/"harness/support/compat_records.py").read_text(encoding="utf-8")
        return task

    def call(self,name,args):
        if name=="list_sources":
            prefix=args.get("prefix","");query=args.get("query","").lower()
            paths=[p.relative_to(self.source).as_posix() for p in sorted(self.source.rglob("*")) if p.is_file() and p.suffix in (".py",".pyi",".pyx",".pxd")]
            paths=[p for p in paths if p.startswith(prefix) and query in p.lower()]
            return {"matching_files":len(paths),"paths":paths[:200],"truncated":len(paths)>200}
        if name=="read_source":
            path=(self.source/args["path"]).resolve()
            if not path.is_relative_to(self.source.resolve()) or path.suffix not in (".py",".pyi",".pyx",".pxd"):raise ValueError("Source path outside declared candidate")
            lines=path.read_text(encoding="utf-8").splitlines();start=max(1,int(args.get("start_line",1)));count=min(160,max(1,int(args.get("line_count",100))))
            return {"path":path.relative_to(self.source).as_posix(),"total_lines":len(lines),"lines":[{"line":i+1,"text":lines[i]} for i in range(start-1,min(len(lines),start-1+count))]}
        if name=="historical_artifact":
            rows=json.loads((self.dataset.bundle/"fixtures"/self.family/"visible.json").read_text(encoding="utf-8"))
            selected=[r for r in rows if r["id"]==args["id"] and r["group"]=="historical"]
            if len(selected)!=1:raise ValueError("Only public historical artifact IDs may be inspected")
            row=selected[0];path=self.dataset.bundle/"fixtures"/Path(row["artifact"].removeprefix("/fixtures/"));raw=path.read_bytes()
            assert hashlib.sha256(raw).hexdigest()==row["sha256"]
            if self.task["artifact_format"]=="json":state={"json":json.loads(raw.decode("utf-8"))}
            elif self.task["artifact_format"]=="joblib":
                # Joblib files append raw array buffers, so plain pickletools
                # cannot disassemble them completely. Public bytes/hash remain
                # inspectable, and real checks expose actual loader behavior.
                state={"format":"joblib array container","byte_length":len(raw),"prefix_hex":raw[:192].hex()}
            else:state={"opcodes":[{"offset":pos,"opcode":op.name,"argument":repr(arg)[:180]} for op,arg,pos in pickletools.genops(raw)]}
            return {"id":row["id"],"producer":row["producer"],"sha256":row["sha256"],**state}
        return super().call(name,args)
