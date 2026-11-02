"""Freeze repair fixtures and a host-owned expected-answer set before models."""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import time

from freeze_and_build import ROOT, write_json
from historical_artifacts import MODELS

HOST = "<author-host>"
REMOTE_BASE = "<author-workdir>"
IMAGE = "sha256:ff710677e697054c516f12c53c92166f1aef075f21b2a593f4c615e522112b4e"


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def main():
    stamp = time.strftime("%Y%m%dT%H%M%S", time.gmtime())
    out = ROOT / "results" / "reader_repairs" / stamp
    bundle = out / "bundle"
    support = bundle / "harness" / "support"
    support.mkdir(parents=True)
    (support / "compat_models.py").write_text(MODELS, encoding="utf-8")
    shutil.copy2(ROOT / "scripts" / "repair_eval_worker.py", bundle / "harness" / "observe.py")
    shutil.copy2(ROOT / "scripts" / "run_generated_tests.py", bundle / "harness" / "run_generated_tests.py")
    deps=bundle/"harness"/"test_deps";deps.mkdir()
    site=ROOT/".venv"/"Lib"/"site-packages"
    shutil.copy2(site/"py.py",deps/"py.py")
    for name in ("pytest","_pytest","pluggy","iniconfig","pygments"):
        shutil.copytree(site/name,deps/name,ignore=shutil.ignore_patterns("__pycache__","*.pyc"))
        for metadata in site.glob(name+"-*.dist-info"):
            shutil.copytree(metadata,deps/metadata.name)
    tasks = {
        "packaging": {"base": "26.1", "fixed": "26.2", "producers": ["25.0", "26.0"],
            "allowed": ["packaging/version.py", "packaging/_structures.py"],
            "source": "https://github.com/pypa/packaging/pull/1163"},
        "attrs": {"base": "22.2.0", "fixed": "23.1.0", "producers": ["21.4.0"],
            "allowed": ["attr/_make.py"], "source": "https://github.com/python-attrs/attrs/pull/1085"}}
    for family, task in tasks.items():
        for label, version in [("base",task["base"]),("fixed",task["fixed"])]:
            shutil.copytree(ROOT / ".cache" / "compat_packages" / f"{family}_{version}", bundle / "packages" / family / label,
                ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        if family == "packaging":
            inputs = [{"kind":"Version", "value":s} for s in ["1.0", "1!1.2rc1.post2.dev3+local.4", "0.9.dev1", "2!3.4.post2", "1.0+linux.x86", "2024.1rc2"]]
        else:
            inputs = [{"kind":kind,"count":c,"name":n} for kind in ("SlottedRecord","FrozenRecord","CachedRecord")
                for c,n in [(17,"historical-样例"),(0,""),(-19,"δοκιμή-中文")]]
        rows = []
        for producer in task["producers"]:
            requests = []
            for i, spec in enumerate(inputs):
                for warm in (False,True):
                    identity = hashlib.sha256(f"{family}|{producer}|{i}|{warm}".encode()).hexdigest()[:12]
                    p = bundle / "fixtures" / family / "artifacts" / f"{identity}.pickle"
                    row = {"id": identity,"group":"historical","producer":producer,"input":spec,"warm":warm,
                           "artifact":str(p),"visible":i == 0 and not warm}
                    requests.append(row)
            req = out / f"{family}_{producer}_producer.json"
            write_json(req, requests)
            target = ROOT / ".cache" / "compat_packages" / f"{family}_{producer}"
            cmd=[sys.executable,"-I",str(ROOT/"scripts"/"repair_eval_worker.py"),"--candidate",str(target),
                "--support",str(support),"--family",family,"--produce",str(req)]
            p=subprocess.run(cmd,capture_output=True,text=True,encoding="utf-8",timeout=60)
            write_json(out/f"{family}_{producer}_producer_receipt.json",{"command":cmd,"exit":p.returncode,"stdout":p.stdout,"stderr":p.stderr})
            assert p.returncode == 0, p.stderr
            for row in requests:
                path=Path(row["artifact"])
                row["sha256"]=sha(path)
                row["artifact"]="/fixtures/"+path.relative_to(bundle/"fixtures").as_posix()
                rows.append(row)
        for i,spec in enumerate(inputs):
            for group in ("fresh_api","current_roundtrip"):
                for warm in (False,True):
                    identity=hashlib.sha256(f"{family}|{group}|{i}|{warm}".encode()).hexdigest()[:12]
                    rows.append({"id":identity,"group":group,"input":spec,"warm":warm,"visible":i == 0 and not warm})
        write_json(bundle/"fixtures"/family/"all.json",rows)
        write_json(bundle/"fixtures"/family/"visible.json",[r for r in rows if r["visible"]])
        write_json(bundle/"fixtures"/family/"current_visible.json",[r for r in rows if r["visible"] and r["group"] != "historical"])
        public=bundle/"fixtures_public"/family
        public.mkdir(parents=True)
        for manifest_name in ("visible.json","current_visible.json"):
            shutil.copy2(bundle/"fixtures"/family/manifest_name,public/manifest_name)
        for row in rows:
            if row["visible"] and row["group"]=="historical":
                relative=Path(row["artifact"].removeprefix("/fixtures/"+family+"/"))
                p=public/relative;p.parent.mkdir(parents=True,exist_ok=True)
                shutil.copy2(bundle/"fixtures"/family/relative,p)
        assert not (public/"all.json").exists()
        task["cases"] = len(rows)
        task["visible_cases"] = sum(r["visible"] for r in rows)
    write_json(out/"tasks.json",tasks)
    write_json(out/"plan.json",{"purpose":"Two real reader repair development tasks; not a heldout generalization claim",
        "image":IMAGE,"host":HOST,"remote_root":REMOTE_BASE+"/"+stamp,"cpu_limit":1,"memory":"1g","network":"none",
        "oracle":"Freeze unmodified fixed-reader observations before model; independent input fields and negative comparisons; no candidate-derived expected values.",
        "groups":["fresh_api","current_roundtrip","historical","fresh-process resave/reload"],
        "input_data":"Locally generated by genuine historical releases; immutable hashes; no external pickle download",
        "task_unit":"2 actual upstream repair lineages, not number of serialized files"})
    archive=out/"public_bundle.tar"
    def public_mode(info):
        info.mode=0o755 if info.isdir() else 0o644
        return info
    with tarfile.open(archive,"w") as tar:
        for p in bundle.iterdir():tar.add(p,arcname=p.name,filter=public_mode)
    remote=REMOTE_BASE+"/"+stamp
    commands=[['ssh',HOST,'mkdir -p '+remote],['scp','-q',str(archive),HOST+':'+remote+'/public_bundle.tar'],
        ['ssh',HOST,'tar -xf '+remote+'/public_bundle.tar -C '+remote]]
    receipts=[]
    for cmd in commands:
        p=subprocess.run(cmd,capture_output=True,text=True,encoding="utf-8",timeout=90)
        receipts.append({"command":cmd,"exit":p.returncode,"stdout":p.stdout,"stderr":p.stderr})
        write_json(out/"upload.json",receipts)
        assert p.returncode == 0,p.stderr
    print(json.dumps({"out":str(out),"remote":remote,"cases":{f:t['cases'] for f,t in tasks.items()}}))


if __name__ == "__main__":main()
