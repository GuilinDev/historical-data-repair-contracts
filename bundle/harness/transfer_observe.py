"""Oracle-free CPU observations for two new artifact repair projects."""
import argparse
import hashlib
import json
from pathlib import Path
import pickle
import subprocess
import sys
import tempfile


def configure(args):
    sys.path[:0]=[args.candidate,args.support,args.runtime]


def construct(family,spec):
    if family=="jsonpickle":
        import pandas as pd
        df=pd.DataFrame(spec["data"],columns=spec["columns"],dtype=object)
        df.columns.name=spec.get("columns_name")
        return df
    from compat_records import Base,PersistedRecord
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session
    engine=create_engine("sqlite://");Base.metadata.create_all(engine)
    with Session(engine,expire_on_commit=False) as session:
        obj=PersistedRecord(id=1,payload=spec["payload"].copy())
        session.add(obj);session.commit();session.expunge(obj)
    engine.dispose()
    return obj


def encode(family,obj):
    if family=="jsonpickle":
        import jsonpickle
        import jsonpickle.ext.pandas
        jsonpickle.ext.pandas.register_handlers()
        return jsonpickle.encode(obj).encode("utf-8")
    return pickle.dumps(obj,protocol=4)


def decode(family,data):
    if family=="jsonpickle":
        import jsonpickle
        import jsonpickle.ext.pandas
        jsonpickle.ext.pandas.register_handlers()
        return jsonpickle.decode(data.decode("utf-8"))
    import compat_records
    return pickle.loads(data)


def frame_snapshot(obj):
    return {"type":type(obj).__module__+"."+type(obj).__name__,"values":obj.values.tolist(),
        "value_types":[[type(x).__name__ for x in row] for row in obj.values.tolist()],
        "columns":obj.columns.tolist(),"columns_name":obj.columns.name,"index":obj.index.tolist(),
        "index_name":obj.index.name,"dtypes":[str(x) for x in obj.dtypes]}


def observe(family,obj,spec,*,replay=False):
    if family=="jsonpickle":
        initial=frame_snapshot(obj)
        continued=obj.copy();continued["continued_column"]=["row"+str(i) for i in range(len(continued))]
        return {"initial":initial,"continued":frame_snapshot(continued)}
    from compat_records import Base,PersistedRecord
    import sqlalchemy as sa
    from sqlalchemy.orm import Session
    result={"id":obj.id,"payload":json.loads(json.dumps(dict(obj.payload))),"payload_type":type(obj.payload).__name__}
    engine=sa.create_engine("sqlite://");Base.metadata.create_all(engine)
    with Session(engine,expire_on_commit=False) as session:
        session.add(PersistedRecord(id=1,payload=dict(obj.payload)));session.commit()
    with Session(engine,expire_on_commit=False) as session:
        session.add(obj)
        obj.payload["continued"]="second" if replay else "first"
        # Demonstrate deletion as well as insertion when an original key exists.
        old_keys=sorted(k for k in obj.payload if k!="continued")
        if old_keys:del obj.payload[old_keys[0]]
        result["dirty"]=sa.inspect(obj).attrs.payload.history.has_changes()
        result["session_modified"]=session.is_modified(obj)
        session.commit()
    with Session(engine) as session:
        result["database_payload"]=dict(session.get(PersistedRecord,1).payload)
    engine.dispose()
    return result


def main(args):
    configure(args)
    if args.produce:
        rows=json.loads(Path(args.produce).read_text(encoding="utf-8"))
        for row in rows:
            path=Path(row["artifact"]);path.parent.mkdir(parents=True,exist_ok=True)
            path.write_bytes(encode(args.family,construct(args.family,row["input"])))
        print(json.dumps({"produced":len(rows),"python":sys.version}));return
    if args.replay:
        obj=decode(args.family,Path(args.replay).read_bytes())
        print(json.dumps(observe(args.family,obj,json.loads(args.spec),replay=True),ensure_ascii=False));return
    rows=json.loads(Path(args.manifest).read_text(encoding="utf-8"));records=[]
    for row in rows:
        result={"id":row["id"],"group":row["group"]}
        try:
            if row["group"]=="fresh_api":obj=construct(args.family,row["input"])
            elif row["group"]=="current_roundtrip":obj=decode(args.family,encode(args.family,construct(args.family,row["input"])))
            else:
                data=Path(row["artifact"]).read_bytes()
                assert hashlib.sha256(data).hexdigest()==row["sha256"]
                obj=decode(args.family,data)
            result["semantics"]=observe(args.family,obj,row["input"])
            with tempfile.TemporaryDirectory(prefix="artifact-continue-") as directory:
                resaved=Path(directory)/"resaved.bin";resaved.write_bytes(encode(args.family,obj))
                command=[sys.executable,"-I",__file__,"--candidate",args.candidate,"--support",args.support,
                    "--runtime",args.runtime,"--family",args.family,"--replay",str(resaved),"--spec",json.dumps(row["input"])]
                p=subprocess.run(command,capture_output=True,text=True,encoding="utf-8",timeout=8)
                if p.returncode:result["restart_error"]=p.stderr[-1600:]
                else:result["restart_semantics"]=json.loads(p.stdout)
            result["status"]="observed"
        except Exception as exc:result.update(status="program_error",error=type(exc).__name__+": "+str(exc)[:1200])
        records.append(result)
    print(json.dumps({"records":records,"python":sys.version},ensure_ascii=False))


if __name__=="__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("--candidate",required=True);parser.add_argument("--support",required=True)
    parser.add_argument("--runtime",required=True);parser.add_argument("--family",choices=["jsonpickle","sqlalchemy"],required=True)
    parser.add_argument("--manifest");parser.add_argument("--produce");parser.add_argument("--replay");parser.add_argument("--spec")
    main(parser.parse_args())
