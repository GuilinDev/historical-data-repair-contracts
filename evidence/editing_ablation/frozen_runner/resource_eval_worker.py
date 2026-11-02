"""Oracle-free observations across eight historical-data repair lineages."""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import pickle
import shutil
import subprocess
import sys
import tempfile
import warnings

CORE={"packaging_version_1163":"packaging","attrs_slot_state_1085":"attrs"}


def configure(args):
    sys.path[:0]=[args.candidate,args.support,str(Path(__file__).resolve().parent)]+args.runtime.split(os.pathsep)


def frame(spec):
    import pandas as pd
    import numpy as np
    if "numeric" in spec:return pd.DataFrame({k:np.asarray(v["values"],dtype=v["dtype"]) for k,v in spec["numeric"].items()})
    obj=pd.DataFrame(spec["data"],columns=spec["columns"],dtype=object if spec.get("text_only") else None)
    if "index" in spec:obj.index=spec["index"]
    obj.columns.name=spec.get("columns_name")
    return obj


def frame_snapshot(obj):
    return {"columns":obj.columns.tolist(),"columns_name":obj.columns.name,"index":obj.index.tolist(),"index_name":obj.index.name,
        "values":{str(k):obj[k].tolist() for k in obj.columns},
        "types":{str(k):[type(v).__name__ for v in obj[k].tolist()] for k in obj.columns},
        "dtypes":{str(k):{"kind":obj[k].dtype.kind,"itemsize":obj[k].dtype.itemsize} for k in obj.columns}}


def construct(task,spec):
    if task in CORE:
        import core_observe
        return core_observe.construct(CORE[task],spec)
    if task=="sqlalchemy_mutable_8133":
        import transfer_observe
        return transfer_observe.construct("sqlalchemy",spec)
    if task.startswith("jsonpickle_") or task=="pandas_pickle_56308":return frame(spec)
    import numpy as np
    return np.asarray(spec["values"],dtype=spec["dtype"]).reshape(spec["shape"])


def encode(task,obj):
    if task.startswith("jsonpickle_"):
        import jsonpickle
        import jsonpickle.ext.pandas
        jsonpickle.ext.pandas.register_handlers()
        return jsonpickle.encode(obj).encode("utf-8")
    if task=="pandas_pickle_56308":
        import pandas as pd
        data=io.BytesIO();pd.to_pickle(obj,data,protocol=4);return data.getvalue()
    return pickle.dumps(obj,protocol=4)


def decode(task,data):
    if task.startswith("jsonpickle_"):
        import jsonpickle
        import jsonpickle.ext.pandas
        jsonpickle.ext.pandas.register_handlers()
        return jsonpickle.decode(data.decode("utf-8"))
    if task=="pandas_pickle_56308":
        import pandas as pd
        return pd.read_pickle(io.BytesIO(data))
    if task=="sqlalchemy_mutable_8133":import compat_records
    return pickle.loads(data)


def observe(task,obj,spec,*,replay=False):
    if task in CORE:
        import core_observe
        return core_observe.observe(CORE[task],obj,spec)
    if task=="sqlalchemy_mutable_8133":
        import transfer_observe
        return transfer_observe.observe("sqlalchemy",obj,spec,replay=replay)
    result={"initial":frame_snapshot(obj)}
    if task=="jsonpickle_csv_562":
        obj["continued_column"]=["row"+str(i) for i in range(len(obj))];continued=obj
    elif task=="jsonpickle_dtype_634":
        obj.loc[0,"i64"]+=17;obj.loc[1,"f64"]+=0.5;continued=obj
    else:
        selected=obj.loc[obj.index[1],obj.columns[0]]
        result["selection"]=selected.item() if hasattr(selected,"item") else selected
        result["aggregates"]={str(k):obj[k].sum().item() for k in obj.columns if obj[k].dtype.kind in ("i","u","f")}
        obj.iloc[0,0]+=7;continued=obj
    result["continued"]=frame_snapshot(continued)
    return result


def array_snapshot(obj):
    return {"values":obj.tolist(),"shape":list(obj.shape),"dtype":{"kind":obj.dtype.kind,"itemsize":obj.dtype.itemsize},
            "python_sum":sum(obj.ravel().tolist())}


def child_args(args):
    return [sys.executable,"-I","-B",__file__,"--candidate",args.candidate,"--support",args.support,
            "--runtime",args.runtime,"--task",args.task]


def joblib_observe(args,row):
    import joblib
    import numpy as np
    spec=row["input"];result={"id":row["id"],"group":row["group"]}
    try:
        with tempfile.TemporaryDirectory(prefix="resource-joblib-") as directory:
            path=Path(directory)/"working.pkl";resaved=Path(directory)/"resaved.pkl"
            if row["group"]=="historical":
                original=Path(row["artifact"]);assert hashlib.sha256(original.read_bytes()).hexdigest()==row["sha256"]
                shutil.copyfile(original,path)
            else:joblib.dump(construct(args.task,spec),path,compress=tuple(spec["compress"]) if isinstance(spec["compress"],list) else spec["compress"],protocol=4)
            mode=None if row["group"]=="fresh_api" else ("r+" if args.task=="joblib_endian_1561" else "r")
            with warnings.catch_warnings(record=True) as notes:
                warnings.simplefilter("always");obj=joblib.load(path,mmap_mode=mode)
                sem={"initial":array_snapshot(obj),"is_memmap":isinstance(obj,np.memmap)}
                if args.task=="joblib_compressed_1681" and mode and spec["compress"]==0:
                    sem["readonly"]=not obj.flags.writeable
                    previous=obj.flat[1].item()
                    try:
                        obj.flat[1]=previous+spec["delta"]
                        sem["readonly_mutation_blocked"]=False
                        obj.flat[1]=previous
                    except ValueError:sem["readonly_mutation_blocked"]=True
                    obj=np.array(obj,copy=True)
                obj.flat[1]+=spec["delta"]
                sem["continued"]=array_snapshot(obj)
                if args.task=="joblib_endian_1561" and mode:obj.flush()
                joblib.dump(obj,resaved,compress=tuple(spec["compress"]) if isinstance(spec["compress"],list) else spec["compress"],protocol=4)
                result["diagnostics"]={"warnings":[str(n.message) for n in notes],"observed_byteorder":obj.dtype.byteorder}
                del obj
            if args.task=="joblib_endian_1561" and mode:
                p=subprocess.run(child_args(args)+["--array-replay",str(path)],capture_output=True,text=True,encoding="utf-8",timeout=12)
                if p.returncode:sem["inplace_restart_error"]=p.stderr[-1600:]
                else:sem["inplace_restart"]=json.loads(p.stdout)
            p=subprocess.run(child_args(args)+["--array-replay",str(resaved)],capture_output=True,text=True,encoding="utf-8",timeout=12)
            result["semantics"]=sem
            if p.returncode:result["restart_error"]=p.stderr[-1600:]
            else:result["restart_semantics"]=json.loads(p.stdout)
            result["status"]="observed"
    except Exception as exc:result.update(status="program_error",error=type(exc).__name__+": "+str(exc)[:1600])
    return result


def one(args,row):
    if args.task.startswith("joblib_"):return joblib_observe(args,row)
    result={"id":row["id"],"group":row["group"]}
    try:
        if row["group"]=="fresh_api":obj=construct(args.task,row["input"])
        elif row["group"]=="current_roundtrip":
            obj=construct(args.task,row["input"])
            if row.get("warm"):
                try:hash(obj)
                except TypeError:pass
            obj=decode(args.task,encode(args.task,obj))
        else:
            data=Path(row["artifact"]).read_bytes();assert hashlib.sha256(data).hexdigest()==row["sha256"]
            obj=decode(args.task,data)
        result["semantics"]=observe(args.task,obj,row["input"])
        with tempfile.TemporaryDirectory(prefix="resource-restart-") as directory:
            artifact=Path(directory)/"resaved.bin";artifact.write_bytes(encode(args.task,obj))
            p=subprocess.run(child_args(args)+["--replay",str(artifact),"--spec",json.dumps(row["input"])],
                capture_output=True,text=True,encoding="utf-8",timeout=12)
            if p.returncode:result["restart_error"]=p.stderr[-1800:]
            else:result["restart_semantics"]=json.loads(p.stdout)
        result["status"]="observed"
    except Exception as exc:result.update(status="program_error",error=type(exc).__name__+": "+str(exc)[:1600])
    return result


def main(args):
    configure(args)
    if args.array_replay:
        import joblib
        print(json.dumps(array_snapshot(joblib.load(args.array_replay)),ensure_ascii=False));return
    if args.replay:
        obj=decode(args.task,Path(args.replay).read_bytes())
        print(json.dumps(observe(args.task,obj,json.loads(args.spec),replay=True),ensure_ascii=False));return
    if args.produce:
        rows=json.loads(Path(args.produce).read_text(encoding="utf-8"));records=[]
        for row in rows:
            path=Path(row["artifact"]);path.parent.mkdir(parents=True,exist_ok=True);obj=construct(args.task,row["input"])
            if row.get("warm"):
                try:hash(obj)
                except TypeError:pass
            if args.task.startswith("joblib_"):
                import joblib
                compress=row["input"]["compress"];compress=tuple(compress) if isinstance(compress,list) else compress
                joblib.dump(obj,path,compress=compress,protocol=4);selfread=joblib.load(path)
                records.append({"id":row["id"],"self_read":array_snapshot(selfread)})
            else:
                path.write_bytes(encode(args.task,obj));selfread=decode(args.task,path.read_bytes())
                if args.task.startswith("jsonpickle_") or args.task=="pandas_pickle_56308":records.append({"id":row["id"],"self_read":frame_snapshot(selfread)})
                else:records.append({"id":row["id"],"produced":True})
        print(json.dumps({"produced":len(rows),"records":records,"python":sys.version},ensure_ascii=False));return
    rows=json.loads(Path(args.manifest).read_text(encoding="utf-8"))
    if args.trial is not None:
        records=[one(args,r) for r in rows if r.get("trial")==args.trial]
    elif args.task=="jsonpickle_dtype_634":
        # Each process reads the whole old-artifact set. PYTHONHASHSEED does not
        # control class-address order; the repeated-process gate is explicit.
        records=[]
        for trial in sorted({r["trial"] for r in rows if "trial" in r}):
            p=subprocess.run(child_args(args)+["--manifest",args.manifest,"--trial",str(trial)],
                capture_output=True,text=True,encoding="utf-8",timeout=90)
            if p.returncode:raise RuntimeError("Independent mapping trial failed to execute: "+p.stderr[-1400:])
            records.extend(json.loads(p.stdout)["records"])
        records.extend(one(args,r) for r in rows if "trial" not in r)
    else:records=[one(args,r) for r in rows]
    print(json.dumps({"records":records,"python":sys.version},ensure_ascii=False))


if __name__=="__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("--candidate",required=True);parser.add_argument("--support",required=True);parser.add_argument("--runtime",required=True)
    parser.add_argument("--task",required=True);parser.add_argument("--manifest");parser.add_argument("--produce")
    parser.add_argument("--replay");parser.add_argument("--spec");parser.add_argument("--array-replay");parser.add_argument("--trial",type=int)
    main(parser.parse_args())
