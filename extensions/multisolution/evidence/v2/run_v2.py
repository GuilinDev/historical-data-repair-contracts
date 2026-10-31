"""Frozen second version after an independent empty-payload source-review finding."""
import argparse
import ast
from datetime import datetime, timezone
import difflib
import hashlib
import importlib.util
import json
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tarfile
import time

HERE = Path(__file__).resolve().parent
V1 = HERE.parent / "20261030T222403"
ROOT = HERE.parents[2]
DATA = ROOT / "results/resource/20261030T212309"
TASK = "jsonpickle_dtype_634"
HOST = "<author-host>"
spec = importlib.util.spec_from_file_location("multisolution_v1_trusted_audit", V1 / "run_experiment.py")
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)
read, dump, sha, inventory = audit.read, audit.dump, audit.sha, audit.inventory


def prepare():
    assert not (HERE / "plan.json").exists()
    assert read(V1 / "summary.json")["supplemental_pending"] is False
    shutil.copytree(V1 / "candidates/document_exclusion/candidate", HERE / "candidate")
    path = HERE / "candidate/jsonpickle/ext/pandas.py"
    old = '''                # pd/in can also encode an interval; do not reinterpret
                # non-integer payloads as the historical integer alias.
                integer_payload = all(
                    isinstance(value, (int, np.integer))
                    and not isinstance(value, (bool, np.bool_))
                    for value in col_data
                )
                if type_code != "pd/in" or integer_payload:
                    dtype_str = legacy_overrides.get(type_code, dtype_str)
'''
    new = '''                # Bare prefixes may also name non-numeric dtypes. Empty
                # payloads provide no kind evidence: retain the conservative
                # reader behavior. This guard never selects a width by value
                # magnitude; only document tags can establish a unique width.
                integer_payload = bool(col_data) and all(
                    isinstance(value, (int, np.integer))
                    and not isinstance(value, (bool, np.bool_))
                    for value in col_data
                )
                floating_payload = bool(col_data) and all(
                    isinstance(value, (int, float, np.integer, np.floating))
                    and not isinstance(value, (bool, np.bool_))
                    for value in col_data
                )
                can_infer = (type_code in ("pd/i", "pd/in", "pd/u") and integer_payload
                             or type_code == "pd/f" and floating_payload)
                if can_infer:
                    dtype_str = legacy_overrides.get(type_code, dtype_str)
'''
    text = audit.replace(path.read_text(encoding="utf-8"), old, new)
    ast.parse(text); path.write_text(text, encoding="utf-8", newline="\n")
    base = DATA / "bundle/packages" / TASK / "base"
    original, final = inventory(base), inventory(HERE / "candidate")
    changed = sorted(k for k in set(original) | set(final) if original.get(k) != final.get(k))
    allowed = read(DATA / "tasks.json")[TASK]["allowed"]
    assert set(changed) <= set(allowed)
    patch = []
    for rel in changed:
        patch.extend(difflib.unified_diff((base / rel).read_text(encoding="utf-8").splitlines(True),
                    (HERE / "candidate" / rel).read_text(encoding="utf-8").splitlines(True), fromfile="a/" + rel, tofile="b/" + rel))
    (HERE / "repair.patch").write_text("".join(patch), encoding="utf-8", newline="\n")
    dump(HERE / "candidate_manifest.json", final)
    (HERE / "supplemental").mkdir()
    for name in ("empty_interval_alone", "empty_interval_and_explicit_integers", "nonempty_interval_and_integers"):
        shutil.copyfile(V1 / "empty_interval_calibration" / (name + ".json"), HERE / "supplemental" / (name + ".json"))
    shutil.copyfile(V1 / "empty_interval_calibration/producer_receipt.json", HERE / "supplemental/producer_receipt.json")
    shutil.copyfile(V1 / "supplemental/collision.json", HERE / "supplemental/collision.json")
    shutil.copyfile(V1 / "supplemental_worker.py", HERE / "numeric_worker.py")
    dump(HERE / "immutable_inputs_before.json", audit.immutable())
    plan = {"created_utc": datetime.now(timezone.utc).isoformat(), "status": "FROZEN_BEFORE_V2_EXECUTION",
        "parent_plan_sha256": sha(V1 / "plan.json"), "parent_summary_sha256": sha(V1 / "summary.json"),
        "revision_reason": "Independent source review identified vacuous all(empty) and non-numeric bare-tag ambiguity. v1 remains intact; new candidate requires nonempty kind-compatible payload before any metadata-only width deduction.",
        "candidate": "document_exclusion_v2", "changed_paths": changed,
        "manifest_sha256": sha(HERE / "candidate_manifest.json"), "repair_patch_sha256": sha(HERE / "repair.patch"),
        "main_checks": 84, "main_judge": "Unchanged ResourceDataset.judge on the same immutable main84 inputs; raw exact-gold comparison separately.",
        "controls": "Genuine old4.1.1 empty and nonempty Interval documents, with same-process producer receipt; compare old/base, official reference, whole fixed, frozenv1 and v2 through fresh-process restart. Also repeat lone-collision fallback and currentfloat32/64 exact-width controls on v2.",
        "control_interpretation": "Old producer already loses empty Interval dtype to float64. Check for a new regression relative to conservative reference; do not call that pre-existing limitation repaired or claim every Interval API is correct.",
        "success_gate": "v2 passes84/84, has admissible genuine width differences from exact gold, preserves official reference behavior on the empty/non-numeric controls, and passes3 numeric fallback/new-width checks. Source reasoning must survive independent review.",
        "no_retcon": "This is a second author-constructed resource validation after a disclosed source-review defect, not a changed main model protocol, new model success or unmodified-v1 success.",
        "api_calls": 0, "gpu_calls": 0,
        "source_hashes": {p.name: sha(p) for p in (Path(__file__), HERE / "interval_worker.py", HERE / "numeric_worker.py")},
        "trusted_v1_audit_sha256": sha(V1 / "run_experiment.py"), "supplemental_manifest": inventory(HERE / "supplemental")}
    dump(HERE / "plan.json", plan)
    print(json.dumps({"prepared": True, "out": str(HERE), "changed": changed}), flush=True)


def verified():
    plan = read(HERE / "plan.json")
    assert audit.immutable() == read(HERE / "immutable_inputs_before.json")
    for name, expected in plan["source_hashes"].items(): assert sha(HERE / name) == expected
    assert sha(V1 / "run_experiment.py") == plan["trusted_v1_audit_sha256"]
    assert inventory(HERE / "supplemental") == plan["supplemental_manifest"]
    assert sha(HERE / "candidate_manifest.json") == plan["manifest_sha256"]
    assert inventory(HERE / "candidate") == read(HERE / "candidate_manifest.json")
    return plan


def evaluate():
    verified()
    assert not (HERE / "evaluation/score.json").exists()
    sys.path.insert(0, str(ROOT / "scripts"))
    from resource_task_tools import ResourceDataset, ResourceGateway
    dataset = ResourceDataset(DATA)
    gateway = object.__new__(ResourceGateway)
    gateway.dataset, gateway.family, gateway.task = dataset, TASK, dataset.tasks[TASK]
    gateway.out, gateway.source, gateway.edits, gateway.checks = HERE.resolve(), (HERE / "candidate").resolve(), [], 0
    score = gateway.evaluate("all.json", HERE / "evaluation")
    actual = {r["id"]: r for r in read(HERE / "evaluation/observations.json")["records"]}
    reference = read(DATA / "oracle" / (TASK + ".json"))
    metadata = {r["id"]: r for r in read(DATA / "bundle/fixtures" / TASK / "all.json")}
    exact, differences = [], []
    for expected in reference["records"]:
        row = actual[expected["id"]]
        good = row.get("status") == "observed" and all(row.get(k) == expected[k] for k in ("semantics", "restart_semantics"))
        exact.append({"id": row["id"], "passed": good})
        diffs = audit.field_diffs(expected["semantics"], row.get("semantics"), "/semantics") + audit.field_diffs(expected["restart_semantics"], row.get("restart_semantics"), "/restart_semantics")
        for diff in diffs:
            parts = diff["path"].split("/")
            legal = len(parts) == 6 and parts[3] == "dtypes" and parts[5] == "itemsize" and parts[4] in metadata[row["id"]].get("ambiguous_columns", [])
            if legal:
                minimum = int(''.join(c for c in metadata[row["id"]]["input"]["numeric"][parts[4]]["dtype"] if c.isdigit())) // 8
                legal = diff["actual"] >= minimum
                diff["minimum_original_bytes"] = minimum
            diff["admissible_width_only"] = legal
        if diffs: differences.append({"id": row["id"], "differences": diffs})
    dump(HERE / "exact_gold_score.json", {"passed": sum(r["passed"] for r in exact), "n": len(exact), "records": exact})
    dump(HERE / "semantic_differences.json", differences)
    with tarfile.open(HERE / "evaluation/candidate.tar") as archive:
        uploaded = {m.name: hashlib.sha256(archive.extractfile(m).read()).hexdigest() for m in archive.getmembers() if m.isfile()}
    assert uploaded == read(HERE / "candidate_manifest.json")
    verified()
    result = {"n": score["n"], "admissible_passed": score["passed"], "raw_exact_gold_passed": sum(r["passed"] for r in exact),
        "execution_status": score["execution_status"], "difference_rows": len(differences),
        "all_differences_admissible_width_only": all(d["admissible_width_only"] for r in differences for d in r["differences"]),
        "candidate_archive_matches": True, "groups": score["groups"], "controls_pending": True}
    dump(HERE / "primary_summary.json", result)
    print(json.dumps(result), flush=True)


def controls():
    verified()
    settings = read(DATA / "plan.json")
    remote = settings["remote_root"] + "/independent_multisolution_" + HERE.name
    archive = HERE / "controls_input.tar"
    with tarfile.open(archive, "w") as tf:
        for name in ("interval_worker.py", "numeric_worker.py"): tf.add(HERE / name, arcname=name)
        for p in (HERE / "supplemental").glob("*.json"): tf.add(p, arcname=p.name)
    for args in (["ssh", HOST, "mkdir -p " + shlex.quote(remote)], ["scp", "-q", str(archive), HOST + ":" + remote + "/inputs.tar"],
                 ["ssh", HOST, "tar -xf " + shlex.quote(remote + "/inputs.tar") + " -C " + shlex.quote(remote)]):
        completed = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=40)
        assert completed.returncode == 0, completed.stderr
    def candidate_remote(path):
        return next(arg.removesuffix(":/candidate:ro") for arg in read(path)["command"] if arg.endswith(":/candidate:ro"))
    targets = [("old_base", settings["remote_root"] + "/packages/" + TASK + "/base"),
        ("official_widest", candidate_remote(V1 / "candidates/official_widest/evaluation/execution.json")),
        ("whole_fixed", settings["remote_root"] + "/packages/" + TASK + "/fixed"),
        ("document_exclusion_v1", candidate_remote(V1 / "candidates/document_exclusion/evaluation/execution.json")),
        ("document_exclusion_v2", candidate_remote(HERE / "evaluation/execution.json"))]
    records = []
    for name, source in targets:
        jobs = ["interval"] + (["numeric"] if name == "document_exclusion_v2" else [])
        for job in jobs:
            out = HERE / "controls" / name / job
            out.mkdir(parents=True, exist_ok=True)
            assert not (out / "execution.json").exists()
            args = ["timeout", "--kill-after=5s", "45s", "docker", "run", "--rm", "--network", "none", "--cpus", "1", "--memory", "1g",
                "--pids-limit", "64", "--read-only", "--cap-drop", "ALL", "--security-opt", "no-new-privileges", "--user", "65534:65534",
                "--tmpfs", "/tmp:rw,nosuid,nodev,size=32m", "-e", "OPENBLAS_NUM_THREADS=1", "-e", "OMP_NUM_THREADS=1",
                "-v", source + ":/candidate:ro", "-v", settings["runtime_remote"] + ":/runtime:ro", "-v", remote + ":/review:ro",
                settings["image"], "python", "-I", "-B", "/review/" + job + "_worker.py"]
            if job == "numeric": args += ["--mode", "candidate"]
            start = time.perf_counter()
            p = subprocess.run(["ssh", HOST, shlex.join(args)], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=70)
            (out / "stdout.txt").write_text(p.stdout, encoding="utf-8"); (out / "stderr.txt").write_text(p.stderr, encoding="utf-8")
            dump(out / "execution.json", {"command": args, "exit": p.returncode, "wall_seconds": time.perf_counter() - start})
            result = json.loads(p.stdout) if p.returncode == 0 else {"status": "engineering_error", "exit": p.returncode}
            dump(out / "result.json", result)
            records.append({"name": name, "job": job, "result": result})
            print(json.dumps({"name": name, "job": job, "status": result.get("status")}), flush=True)
    byname = {r["name"]: r["result"] for r in records if r["job"] == "interval"}
    reference = byname["official_widest"]["records"]
    comparisons = {}
    for name, result in byname.items():
        comparisons[name] = [{"case": a["case"], "initial_matches_official": a.get("initial") == b.get("initial"),
            "restart_matches_official": a.get("restart") == b.get("restart"), "matches_original_input": a.get("initial") == a["original_input"]}
            for a, b in zip(result["records"], reference)]
    verified()
    summary = {**read(HERE / "primary_summary.json"), "controls_pending": False, "interval_comparisons": comparisons,
        "numeric_fallback": next(r["result"] for r in records if r["job"] == "numeric"),
        "main_experiment_unchanged": True, "candidate_whitelist_passed": True,
        "plan_sha256": sha(HERE / "plan.json"), "source_policy_review": "PENDING_EXTERNAL_REVIEW",
        "interpretation": "Second version after independent source-review correction; old empty Interval dtype loss persists and is not claimed repaired. Main84 numerical contract is unchanged."}
    dump(HERE / "summary.json", summary)
    print(json.dumps(summary), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(); parser.add_argument("action", choices=("prepare", "evaluate", "controls"))
    action = parser.parse_args().action
    {"prepare": prepare, "evaluate": evaluate, "controls": controls}[action]()
