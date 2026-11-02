"""Public, oracle-free observations for two reader-repair tasks.

Executed in an isolated CPU container. Expected answers remain in the host
controller. Only trusted, locally generated historical artifacts are loaded.
"""
import argparse
import base64
import hashlib
import json
from pathlib import Path
import pickle
import subprocess
import sys
import tempfile


def construct(family, spec):
    if family == "packaging":
        from packaging.version import Version
        return Version(spec["value"])
    import compat_models
    return getattr(compat_models, spec["kind"])(spec["count"], spec["name"])


def observe(family, obj, spec):
    peer = construct(family, spec)
    if family == "packaging":
        from packaging.version import Version
        other = Version("999!999")
        result = {"text": str(obj), "epoch": obj.epoch, "release": list(obj.release),
            "pre": list(obj.pre) if obj.pre else None, "post": obj.post, "dev": obj.dev,
            "local": obj.local, "less_than_other": obj < other,
            "public_types": {key:type(getattr(obj,key)).__name__ for key in ("epoch","release","pre","post","dev","local")},
            "release_item_types": [type(x).__name__ for x in obj.release],
            "representative_ordering": {v:{"lt":obj<Version(v),"eq":obj==Version(v),"gt":obj>Version(v)}
                for v in ("0.0.dev0","1.0rc1","1.0","1!0","999!999")}}
    else:
        other = type(peer)(spec["count"]+1, spec["name"]+"-different")
        result = {"count": obj.count, "name": obj.name,
                  "count_type":type(obj.count).__name__,"name_type":type(obj.name).__name__}
        original=obj.count
        try:
            obj.count=original+1
            result["mutation"]={"accepted":True,"value":obj.count,"type":type(obj.count).__name__}
        except Exception as exc:
            result["mutation"]={"accepted":False,"exception":type(exc).__name__}
        finally:
            object.__setattr__(obj,"count",original)
    result.update(equal_to_fresh=obj == peer, equal_to_different=obj == other,
                  type_name=type(obj).__module__+"."+type(obj).__qualname__)
    try:
        result["hash_consistent"] = hash(obj) == hash(peer)
        result["dict_lookup"] = {obj: "value"}.get(peer) == "value"
        result["set_lookup"] = peer in {obj}
    except TypeError:
        result["hashable"] = False
    return result


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--candidate", required=True)
    ap.add_argument("--support", required=True)
    ap.add_argument("--family", choices=["packaging", "attrs"], required=True)
    ap.add_argument("--manifest")
    ap.add_argument("--produce")
    ap.add_argument("--replay")
    ap.add_argument("--spec")
    args = ap.parse_args()
    sys.path.insert(0, args.candidate)
    sys.path.insert(0, args.support)
    if args.replay:
        spec = json.loads(args.spec)
        obj = pickle.loads(Path(args.replay).read_bytes())
        print(json.dumps(observe(args.family, obj, spec), ensure_ascii=False))
        return
    if args.produce:
        request = json.loads(Path(args.produce).read_text(encoding="utf-8"))
        for row in request:
            obj = construct(args.family, row["input"])
            if row["warm"]:
                try:
                    hash(obj)
                except TypeError:
                    pass
            p = Path(row["artifact"])
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(pickle.dumps(obj, protocol=4))
        print(json.dumps({"produced": len(request)}))
        return
    rows = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
    results = []
    for row in rows:
        output = {"id": row["id"], "group": row["group"]}
        try:
            if row["group"] == "fresh_api":
                obj = construct(args.family, row["input"])
            elif row["group"] == "current_roundtrip":
                obj = construct(args.family, row["input"])
                if row["warm"]:
                    try:
                        hash(obj)
                    except TypeError:
                        pass  # mutable attrs classes are legitimately unhashable
                obj = pickle.loads(pickle.dumps(obj, protocol=4))
            else:
                p = Path(row["artifact"])
                assert hashlib.sha256(p.read_bytes()).hexdigest() == row["sha256"]
                obj = pickle.loads(p.read_bytes())
            output["semantics"] = observe(args.family, obj, row["input"])
            # Observe a second load in a NEW interpreter, after normal usage
            # has populated hash caches. No expected values enter this process.
            with tempfile.TemporaryDirectory(prefix="reader-restart-") as d:
                artifact = Path(d) / "resaved.pickle"
                artifact.write_bytes(pickle.dumps(obj, protocol=4))
                cmd = [sys.executable, "-I", __file__, "--candidate", args.candidate, "--support", args.support,
                    "--family", args.family, "--replay", str(artifact), "--spec", json.dumps(row["input"])]
                replay = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", timeout=5)
                if replay.returncode:
                    output["restart_error"] = replay.stderr[-1200:]
                else:
                    output["restart_semantics"] = json.loads(replay.stdout)
            output["status"] = "observed"
        except Exception as exc:
            output.update(status="program_error", error=type(exc).__name__+": "+str(exc))
        results.append(output)
    print(json.dumps({"python": sys.version, "records": results}, ensure_ascii=False))


if __name__ == "__main__":
    main()
