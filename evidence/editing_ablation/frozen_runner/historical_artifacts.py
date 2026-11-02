"""Complete fresh-process historical producer/consumer compatibility matrix.

This is development resource validation. Artifacts are generated locally by
trusted released packages, never downloaded pickle payloads or edited opcodes.
"""
from __future__ import annotations
import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import importlib.metadata as md
import json
import os
from pathlib import Path
import pickle
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")

VERSIONS = {"packaging": ["25.0", "26.0", "26.1", "26.2"], "attrs": ["21.4.0", "22.2.0", "23.1.0"]}
MODELS = '''import attr
@attr.s(slots=True)
class SlottedRecord:
    count = attr.ib(type=int)
    name = attr.ib(type=str)
@attr.s(slots=True, frozen=True)
class FrozenRecord:
    count = attr.ib(type=int)
    name = attr.ib(type=str)
@attr.s(slots=True, frozen=True, cache_hash=True)
class CachedRecord:
    count = attr.ib(type=int)
    name = attr.ib(type=str)
'''


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def objects(family):
    if family == "packaging":
        from packaging.version import Version
        from packaging.specifiers import Specifier, SpecifierSet
        from packaging.tags import Tag
        from packaging.markers import Marker
        from packaging.requirements import Requirement
        return {"Version": Version("1!1.2rc1.post2.dev3+local.4"), "Specifier": Specifier(">=1.0"),
            "SpecifierSet": SpecifierSet(">=1.0,<3"), "Tag": Tag("py3", "none", "any"),
            "Marker": Marker('python_version >= "3.8" and os_name == "posix"'),
            "Requirement": Requirement('demo[extra]>=1.0; python_version >= "3.8"')}
    import compat_models
    return {name: getattr(compat_models, name)(17, "historical-样例") for name in ("SlottedRecord", "FrozenRecord", "CachedRecord")}


def semantics(family, name, obj):
    if family == "attrs":
        clone = type(obj)(17, "historical-样例")
        result = {"count": obj.count, "name": obj.name, "same_as_constructed": obj == clone}
        if name != "SlottedRecord":
            result["hash_consistent"] = hash(obj) == hash(clone)
        return result
    peer = objects(family)[name]
    result = {"text": str(obj), "equal_to_fresh": obj == peer}
    try:
        result["hash_consistent"] = hash(obj) == hash(peer)
        result["dict_lookup"] = {obj: "same-value"}.get(peer) == "same-value"
        result["set_lookup"] = peer in {obj}
    except TypeError:
        result["hashable"] = False
    if name == "Version":
        from packaging.version import Version
        result.update(epoch=obj.epoch, release=list(obj.release), pre=list(obj.pre) if obj.pre else None,
            post=obj.post, dev=obj.dev, local=obj.local, below_bound=obj < Version("2!0"), equal=obj == Version(str(obj)))
    elif name in {"Specifier", "SpecifierSet"}:
        result["membership"] = {s: obj.contains(s) for s in ("0.9", "1.0", "2.0", "3.0", "1.5rc1")}
    elif name == "Tag":
        result.update(interpreter=obj.interpreter, abi=obj.abi, platform=obj.platform)
    elif name == "Marker":
        result["evaluations"] = [obj.evaluate({"python_version": "3.12", "os_name": "posix"}),
                                 obj.evaluate({"python_version": "3.7", "os_name": "posix"})]
    elif name == "Requirement":
        result.update(name=obj.name, extras=sorted(obj.extras), membership=[obj.specifier.contains("0.9"), obj.specifier.contains("2.0")],
                      marker=obj.marker.evaluate({"python_version": "3.12"}))
    return result


def worker(args):
    target = Path(args.target).resolve()
    sys.path.insert(0, str(target))
    if args.support:
        sys.path.insert(0, args.support)
    family = args.family
    module = __import__("attr" if family == "attrs" else family)
    assert Path(module.__file__).resolve().is_relative_to(target), module.__file__
    distribution = next(d for d in md.distributions(path=[str(target)]) if d.metadata["Name"].lower() == family)
    identity = {"family": family, "version": distribution.version, "module_file": module.__file__,
                "python": sys.version, "source_sha256": sha(module.__file__)}
    if args.mode == "produce":
        destination = Path(args.destination)
        destination.mkdir(parents=True, exist_ok=True)
        entries = []
        for name in objects(family):
            for warm in (False, True):
                obj = objects(family)[name]
                warm_result = "not_requested"
                if warm:
                    try:
                        hash(obj)
                        warm_result = "hashed"
                    except TypeError:
                        warm_result = "unhashable"
                artifact = destination / f"{name}_{'warm' if warm else 'cold'}.pickle"
                data = pickle.dumps(obj, protocol=4)
                artifact.write_bytes(data)
                expected = semantics(family, name, obj)
                entries.append({"case": artifact.stem, "object": name, "warm": warm, "warm_result": warm_result,
                    "artifact": str(artifact), "sha256": sha(artifact), "bytes": len(data), "pickle_protocol": 4,
                    "expected_semantics": expected, "producer": identity})
        write_json(destination / "artifacts.json", entries)
        print(json.dumps({"produced": len(entries), "identity": identity}))
        return
    entries = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
    results = []
    for entry in entries:
        artifact = Path(entry["artifact"])
        assert sha(artifact) == entry["sha256"]
        stage = "decode"
        observed = None
        reference = None
        try:
            obj = pickle.loads(artifact.read_bytes())
            stage = "semantic_operations"
            observed = semantics(family, entry["object"], obj)
            # Upgrading may intentionally change an API's default policy.
            # The compatibility contract is that restored data behave like the
            # equivalent freshly constructed value under the CURRENT reader.
            # Keep old-version behavior as a separate diagnostic, not an oracle.
            reference = semantics(family, entry["object"], objects(family)[entry["object"]])
            success = observed == reference
            error = None if success else "semantic_mismatch"
        except Exception as exc:
            success = False
            error = type(exc).__name__ + ": " + str(exc)
        results.append({"case": entry["case"], "object": entry["object"], "producer_version": entry["producer"]["version"],
            "consumer_version": identity["version"], "success": success, "failure_stage": None if success else stage,
            "error": error, "observed_semantics": observed, "producer_semantics": entry["expected_semantics"],
            "current_reader_reference": reference,
            "matches_producer_semantics": observed == entry["expected_semantics"] if observed is not None else None,
            "artifact_sha256": entry["sha256"], "artifact_unchanged": sha(artifact) == entry["sha256"], "consumer": identity})
    write_json(args.result, results)
    print(json.dumps({"consumer": identity["version"], "success": sum(r["success"] for r in results), "n": len(results)}))


def matrix():
    stamp = time.strftime("%Y%m%dT%H%M%S", time.gmtime())
    out = ROOT / "results" / "historical_artifacts" / stamp
    out.mkdir(parents=True)
    support = out / "support"
    support.mkdir()
    (support / "compat_models.py").write_text(MODELS, encoding="utf-8")
    plan = {"purpose": "CPU resource validation only; released producer and consumer versions; no model-effect claim",
        "created_utc": datetime.now(timezone.utc).isoformat(), "versions": VERSIONS,
        "sources": {"packaging": ["https://github.com/pypa/packaging/pull/1163", "https://github.com/pypa/packaging/blob/main/CHANGELOG.rst"],
                    "attrs": ["https://github.com/python-attrs/attrs/pull/1085"]},
        "protocol": 4, "all_consumers_fresh_process": True, "artifacts": "locally generated by trusted releases; no opcode editing",
        "implementation_sha256": sha(__file__), "support_sha256": sha(support / "compat_models.py"),
        "gate": "Report every planned cell, plus self-roundtrip controls; classify engine failure separately from decoder/semantic failures.",
        "oracle": "Loaded value must match the same-input fresh value under the current reader; old-reader defaults are diagnostic, not universally preserved.",
        "amendment": "v0 exact old-semantics comparison falsely flags intentional prerelease policy changes. Preserve that run, use current-reader reference for compatibility."}
    write_json(out / "plan.json", plan)
    manifests = {}
    for family, versions in VERSIONS.items():
        for version in versions:
            target = ROOT / ".cache" / "compat_packages" / f"{family}_{version}"
            files = [p for p in target.rglob("*") if p.is_file() and (p.suffix == ".py" or "LICENSE" in p.name.upper() or p.name == "METADATA")]
            manifests[f"{family}=={version}"] = {p.relative_to(target).as_posix(): sha(p) for p in files}
    write_json(out / "package_manifests.json", manifests)
    all_results, commands = [], []
    env = os.environ.copy()
    env.pop("OPENROUTER_API_KEY", None)
    env.update(PYTHONUTF8="1", PYTHONDONTWRITEBYTECODE="1")

    def run(cmd, prefix):
        p = subprocess.run(cmd, env=env, capture_output=True, text=True, encoding="utf-8", timeout=60)
        prefix.parent.mkdir(parents=True, exist_ok=True)
        prefix.with_suffix(".stdout.txt").write_text(p.stdout, encoding="utf-8")
        prefix.with_suffix(".stderr.txt").write_text(p.stderr, encoding="utf-8")
        commands.append({"command": cmd, "exit_code": p.returncode, "prefix": str(prefix)})
        write_json(out / "commands.json", commands)
        if p.returncode:
            raise RuntimeError("Matrix engine failed; preserved logs; not a scientific failure")

    for family, versions in VERSIONS.items():
        for producer in versions:
            destination = out / family / "artifacts" / producer
            target = ROOT / ".cache" / "compat_packages" / f"{family}_{producer}"
            base = [sys.executable, "-I", str(Path(__file__).resolve()), "--mode", "produce", "--family", family,
                    "--target", str(target), "--support", str(support), "--destination", str(destination)]
            run(base, out / family / "logs" / f"produce_{producer}")
            for consumer in versions:
                result_file = out / family / "cells" / f"{producer}_to_{consumer}.json"
                consumer_target = ROOT / ".cache" / "compat_packages" / f"{family}_{consumer}"
                cmd = [sys.executable, "-I", str(Path(__file__).resolve()), "--mode", "consume", "--family", family,
                       "--target", str(consumer_target), "--support", str(support), "--manifest", str(destination / "artifacts.json"),
                       "--result", str(result_file)]
                run(cmd, out / family / "logs" / f"{producer}_to_{consumer}")
                records = json.loads(result_file.read_text(encoding="utf-8"))
                all_results.extend({"family": family, **r} for r in records)
                print(json.dumps({"family": family, "producer": producer, "consumer": consumer,
                    "success": sum(r["success"] for r in records), "n": len(records)}), flush=True)
    groups = defaultdict(list)
    for row in all_results:
        groups[(row["family"], row["producer_version"], row["consumer_version"])].append(row)
    summary = [{"family": f, "producer": p, "consumer": c, "success": sum(r["success"] for r in rs), "n": len(rs),
        "errors": dict(Counter(r["error"] for r in rs if r["error"]))} for (f,p,c),rs in groups.items()]
    write_json(out / "summary.json", {"status": "all_matrix_cells_completed", "cells": summary,
        "artifact_consumer_cases": len(all_results), "all_artifacts_unchanged": all(r["artifact_unchanged"] for r in all_results),
        "limitations": ["Development/resource gate; no model test or novelty proof.", "Objects within a project share defect lineages.",
                       "Fixed CPython 3.12.14; no cross-interpreter guarantee.", "Only selected public APIs and finite input cases checked."]})
    print(json.dumps({"out": str(out), "complete": True, "artifact_consumer_cases": len(all_results)}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["produce", "consume"])
    parser.add_argument("--family", choices=list(VERSIONS))
    parser.add_argument("--target")
    parser.add_argument("--support")
    parser.add_argument("--destination")
    parser.add_argument("--manifest")
    parser.add_argument("--result")
    args = parser.parse_args()
    if args.mode:
        worker(args)
    else:
        matrix()
