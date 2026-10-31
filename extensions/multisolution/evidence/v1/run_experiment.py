"""Independent source-level admissibility experiment; no candidate execution on host."""
import argparse
import ast
from collections import Counter
from datetime import datetime, timezone
import difflib
import hashlib
import json
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import tarfile
import time
import traceback

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
DATA = ROOT / "results/resource/20261030T212309"
TASK = "jsonpickle_dtype_634"
HOST = "<author-host>"
PROBE = ROOT / "results/ready_curation/jsonpickle634_noninjectivity/20261030T214814Z"


def read(p): return json.loads(p.read_text(encoding="utf-8"))
def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()
def dump(p, value):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
def inventory(root): return {p.relative_to(root).as_posix(): sha(p) for p in sorted(root.rglob("*")) if p.is_file()}
def replace(text, old, new):
    assert text.count(old) == 1, old
    return text.replace(old, new, 1)
def immutable():
    paths = [DATA / "bundle", DATA / "oracle"]
    result = {p.relative_to(ROOT).as_posix(): sha(p) for d in paths for p in sorted(d.rglob("*")) if p.is_file()}
    core = read(DATA / "main_20261030T215327/plan.json")["source_hashes"]
    for name in set(core) | {"prepare_reader_repairs.py", "historical_artifacts.py"}:
        p = ROOT / "scripts" / name
        result[p.relative_to(ROOT).as_posix()] = sha(p)
    for p in (DATA / "tasks.json", DATA / "plan.json"):
        result[p.relative_to(ROOT).as_posix()] = sha(p)
    return result


HELPER = '''def _legacy_document_dtype_overrides(type_codes):
    """Use complete document evidence; incomplete legacy evidence stays wide.

    A genuine producer uses one TYPE_MAP entry per dtype for the entire
    document. Therefore an explicit-width tag rules that dtype out as the
    meaning of another bare tag. Infer only a unique remaining dtype among
    the pandas numeric dtypes of the declared compatible runtime. This does
    not infer dtype from numeric magnitude or application column names.
    """
    present = set(type_codes)
    result = {}
    families = (("float", "f", ("pd/f",)),
                ("int", "i", ("pd/i", "pd/in")),
                ("uint", "u", ("pd/u",)))
    for stem, short, bare_tags in families:
        universe = {
            int(name[len(stem):]) for name in pd_dtypes
            if isinstance(name, str) and re.fullmatch(stem + r"\\d+", name)
        }
        bare = present.intersection(bare_tags)
        if len(bare) != 1 or not universe:
            continue
        explicit = set()
        for tag in present:
            match = re.fullmatch(r"pd/(?:" + short + "|" + stem + r")(\\d+)", tag)
            if match:
                explicit.add(int(match.group(1)))
        remaining = universe - explicit
        if len(remaining) == 1:
            result[next(iter(bare))] = stem + str(next(iter(remaining)))
    return result


'''


def prepare():
    assert not (HERE / "plan.json").exists(), "Frozen plan already exists"
    contract = read(DATA / "tasks.json")[TASK]
    before = immutable()
    dump(HERE / "immutable_inputs_before.json", before)
    base = DATA / "bundle/packages" / TASK / "base"
    reference = DATA / "reference_repairs/tasks" / TASK / "candidate"
    source = (reference / "jsonpickle/tags_pd.py").read_text(encoding="utf-8")
    pandas_base = (base / "jsonpickle/ext/pandas.py").read_text(encoding="utf-8")
    candidates = []
    policies = {
        "official_widest": "Authentic upstream-derived conservative repair: legacy ambiguous numeric tags use widest historical numeric widths; explicit/current tags are pinned.",
        "document_exclusion": "Same stable writer and conservative fallback, plus document-global injective-tag exclusion. Only infer an old dtype when all other supported historical widths are explicitly present under other tags; never infer from values or column names.",
        "negative_explicit_float_narrowing": "Deliberate near-neighbor control: stable repair except explicit pd/f64 is read as float32. Loading/resaving may finish while values or explicit width are wrong. This is not a claimed valid repair."
    }
    for name, policy in policies.items():
        out = HERE / "candidates" / name
        candidate = out / "candidate"
        shutil.copytree(base, candidate)
        tags = source
        pandas_text = pandas_base
        if name == "document_exclusion":
            pandas_text = replace(pandas_text, "import warnings\n", "import re\nimport warnings\n")
            pandas_text = replace(pandas_text, "from ..tags_pd import REVERSE_TYPE_MAP, TYPE_MAP\n", "from ..tags_pd import REVERSE_TYPE_MAP, TYPE_MAP, pd_dtypes\n")
            pandas_text = replace(pandas_text, "class PandasDfHandler(BaseHandler):\n", HELPER + "class PandasDfHandler(BaseHandler):\n")
            pandas_text = replace(pandas_text, "        type_codes = rle_decode(rle_types)\n", "        type_codes = rle_decode(rle_types)\n        legacy_overrides = _legacy_document_dtype_overrides(type_codes)\n")
            pandas_text = replace(pandas_text, '                dtype_str = REVERSE_TYPE_MAP.get(type_code, "object")\n', '''                dtype_str = REVERSE_TYPE_MAP.get(type_code, "object")
                # pd/in can also encode an interval; do not reinterpret
                # non-integer payloads as the historical integer alias.
                integer_payload = all(
                    isinstance(value, (int, np.integer))
                    and not isinstance(value, (bool, np.bool_))
                    for value in col_data
                )
                if type_code != "pd/in" or integer_payload:
                    dtype_str = legacy_overrides.get(type_code, dtype_str)
''')
        elif name == "negative_explicit_float_narrowing":
            tags += '\n# Deliberate negative control: breaks an explicitly encoded width.\nREVERSE_TYPE_MAP["pd/f64"] = "float32"\n'
        for rel, text in (("jsonpickle/tags_pd.py", tags), ("jsonpickle/ext/pandas.py", pandas_text)):
            ast.parse(text)
            if (base / rel).read_text(encoding="utf-8") != text:
                (candidate / rel).write_text(text, encoding="utf-8", newline="\n")
        original, changed = inventory(base), inventory(candidate)
        paths = sorted(p for p in set(original) | set(changed) if original.get(p) != changed.get(p))
        assert set(paths) <= set(contract["allowed"])
        diff = []
        for rel in paths:
            diff.extend(difflib.unified_diff((base / rel).read_text(encoding="utf-8").splitlines(True),
                (candidate / rel).read_text(encoding="utf-8").splitlines(True), fromfile="a/" + rel, tofile="b/" + rel))
        (out / "repair.patch").write_text("".join(diff), encoding="utf-8", newline="\n")
        dump(out / "candidate_manifest.json", changed)
        candidates.append({"name": name, "policy": policy, "changed_paths": paths,
                           "manifest_sha256": sha(out / "candidate_manifest.json"), "patch_sha256": sha(out / "repair.patch")})
    supplemental = HERE / "supplemental"
    supplemental.mkdir()
    a, b = PROBE / "04_float32.json", PROBE / "05_float64.json"
    assert a.read_bytes() == b.read_bytes() and len(a.read_bytes()) == 267
    shutil.copyfile(a, supplemental / "collision.json")
    shutil.copyfile(PROBE / "summary.json", supplemental / "producer_collision_receipt.json")
    # Read immutable gold directly, checking that normalization cannot hide a
    # too-narrow expected width. No candidate or framework execution here.
    metadata = {r["id"]: r for r in read(DATA / "bundle/fixtures" / TASK / "all.json")}
    gold = read(DATA / "oracle" / (TASK + ".json"))
    checked, issues, widths = 0, [], Counter()
    for row in gold["records"]:
        spec = metadata[row["id"]]
        for stage in ("semantics", "restart_semantics"):
            for snapshot_name, snapshot in row[stage].items():
                if not isinstance(snapshot, dict) or "dtypes" not in snapshot: continue
                for column in spec.get("ambiguous_columns", []):
                    minimum = int(re.search(r"\d+$", spec["input"]["numeric"][column]["dtype"]).group()) // 8
                    actual = snapshot["dtypes"][column]
                    checked += 1; widths[str(actual["itemsize"])] += 1
                    if actual["itemsize"] < minimum: issues.append({"id": row["id"], "stage": stage, "column": column, "minimum": minimum, "actual": actual})
    assert not issues
    dump(HERE / "raw_gold_width_review.json", {"positions_checked": checked, "width_counts": dict(widths), "too_narrow": issues,
         "oracle_sha256": sha(DATA / "oracle" / (TASK + ".json")), "metadata_sha256": sha(DATA / "bundle/fixtures" / TASK / "all.json")})
    source_paths = [base / "jsonpickle/tags_pd.py", base / "jsonpickle/ext/pandas.py",
        DATA / "bundle/packages/pandas_pickle_56308/fixed/pandas/core/arrays/floating.py",
        DATA / "bundle/packages/pandas_pickle_56308/fixed/pandas/core/arrays/integer.py"]
    dump(HERE / "deduction_provenance.json", {
        "source_hashes": {p.relative_to(ROOT).as_posix(): sha(p) for p in source_paths},
        "premises": [
            "jsonpickle4.1.1 TYPE_MAP is a dictionary indexed by dtype name. Each dtype_name queried by PandasDfHandler.flatten has one tag for that producer process; all columns use that same TYPE_MAP.",
            "get_smallest_unique_substrings keeps used_substrings, so distinct numeric dtype names receive distinct tags. Explicit numeric suffixes identify their widths; the first width receives a bare prefix.",
            "The declared pandas2.1.4 ExtensionDtype numeric classes define signed8/16/32/64, unsigned8/16/32/64 and floating32/64. Candidate derives these from pd_dtypes, not fixture columns or values.",
            "If a genuine document shows tags for every width except one, a distinct bare numeric tag cannot refer to any already-tagged dtype; the remaining dtype is forced. With incomplete evidence, retain the upstream widest-type fallback.",
            "A lone pd/f collision document has insufficient global evidence. Both positive policies must read it as float64, retaining values; current new writes retain explicit original widths."
        ],
        "scope": "Documents from the task's genuine4.1.1 producer with its declared compatible pandas runtime. Does not assert validity for hand-edited mixed-producer tag dictionaries or arbitrary future dtype plugins.",
        "not_a_new_discovery_claim": "Document-level constraints validate an alternate repair policy and set-valued acceptance; they do not establish prevalence or a novel upgrade-testing method."
    })
    plan = {
        "created_utc": datetime.now(timezone.utc).isoformat(), "status": "FROZEN_BEFORE_CANDIDATE_EXECUTION",
        "task": TASK, "dataset": str(DATA.relative_to(ROOT)), "candidate_count": 3, "candidates": candidates,
        "main_observations_per_candidate": 84, "historical_process_trials": 12,
        "primary_contrast": "On each same complete candidate observation record, compare unmodified ResourceDataset.judge against exact raw frozen-gold semantics and restart_semantics equality. No source-text comparison is an outcome.",
        "success_gate": "Both claimed-positive full patches pass84/84 and independent generic-source review; document_exclusion differs from official in at least one admissibly preserved width; at least one positive is rejected by exact gold. Negative loads/resaves real cases yet is rejected for actual values/kind/explicit-width contract violations. Lone collision must retain conservative fallback.",
        "failure_gate": "Record any failed positive, absent semantic distinction, engineering incompatibility, or invalid deduction as failure/limitation; do not tune candidates or change main judge, oracle, inputs or model denominator.",
        "supplemental": "Existing genuine267B single-column collision input, frozen here, plus fresh construction/encode/decode width controls. Separate from main84/252. No new model calls.",
        "cpu": "One CPU,1GB,network none,readonly candidate/fixtures/runtime in existing qualified ARM image; no candidate execution on host.",
        "candidate_bytes_frozen": True, "api_calls": 0, "gpu_calls": 0,
        "supplemental_sha256": sha(supplemental / "collision.json"), "immutable_manifest_sha256": sha(HERE / "immutable_inputs_before.json"),
        "experiment_script_sha256": sha(Path(__file__))
    }
    plan["supplemental_worker_sha256"] = sha(HERE / "supplemental_worker.py")
    dump(HERE / "plan.json", plan)
    assert before == immutable()
    print(json.dumps({"prepared": len(candidates), "out": str(HERE), "raw_gold_positions_checked": checked, "gold_too_narrow": 0}), flush=True)


def field_diffs(a, b, path=""):
    if isinstance(a, dict) and isinstance(b, dict):
        out = []
        for key in sorted(set(a) | set(b)):
            out.extend(field_diffs(a.get(key), b.get(key), path + "/" + str(key)))
        return out
    if a != b: return [{"path": path, "exact_gold": a, "actual": b}]
    return []


def evaluate():
    plan = read(HERE / "plan.json")
    assert immutable() == read(HERE / "immutable_inputs_before.json")
    assert sha(Path(__file__)) == plan["experiment_script_sha256"]
    sys.path.insert(0, str(ROOT / "scripts"))
    from resource_task_tools import ResourceDataset, ResourceGateway
    dataset = ResourceDataset(DATA)
    reference = read(DATA / "oracle" / (TASK + ".json"))
    expected = {r["id"]: r for r in reference["records"]}
    metadata = {r["id"]: r for r in read(DATA / "bundle/fixtures" / TASK / "all.json")}
    rows = []
    for entry in plan["candidates"]:
        name = entry["name"]; out = HERE / "candidates" / name
        assert sha(out / "candidate_manifest.json") == entry["manifest_sha256"]
        manifest = read(out / "candidate_manifest.json")
        assert inventory(out / "candidate") == manifest
        assert not (out / "evaluation/score.json").exists(), "Do not rerun completed observations"
        gateway = object.__new__(ResourceGateway)
        gateway.dataset, gateway.family, gateway.task = dataset, TASK, dataset.tasks[TASK]
        gateway.out, gateway.source, gateway.edits, gateway.checks = out.resolve(), (out / "candidate").resolve(), [], 0
        try:
            score = gateway.evaluate("all.json", out / "evaluation")
        except Exception as exc:
            dump(out / "infrastructure_error.json", {"type": type(exc).__name__, "message": str(exc), "traceback": traceback.format_exc()})
            rows.append({"name": name, "status": "infrastructure_error", "error": str(exc)})
            print(json.dumps(rows[-1]), flush=True)
            continue
        assert inventory(out / "candidate") == manifest
        with tarfile.open(out / "evaluation/candidate.tar") as archive:
            packed = {m.name: hashlib.sha256(archive.extractfile(m).read()).hexdigest() for m in archive.getmembers() if m.isfile()}
        assert packed == manifest
        observations = read(out / "evaluation/observations.json") if (out / "evaluation/observations.json").exists() else {"records": []}
        found = {r["id"]: r for r in observations["records"]}
        exact, differences = [], []
        for spec in reference["records"]:
            actual = found.get(spec["id"])
            initial = bool(actual and actual.get("status") == "observed" and actual.get("semantics") == spec["semantics"])
            restart = bool(actual and actual.get("restart_semantics") == spec["restart_semantics"])
            exact.append({"id": spec["id"], "group": spec["group"], "initial_passed": initial, "restart_passed": restart, "passed": initial and restart})
            if actual:
                diffs = field_diffs(spec.get("semantics"), actual.get("semantics"), "/semantics") + field_diffs(spec.get("restart_semantics"), actual.get("restart_semantics"), "/restart_semantics")
                if diffs: differences.append({"id": spec["id"], "group": spec["group"], "ambiguous_columns": metadata[spec["id"]].get("ambiguous_columns", []), "differences": diffs})
        dump(out / "exact_gold_score.json", {"passed": sum(r["passed"] for r in exact), "n": len(exact), "records": exact})
        dump(out / "semantic_differences.json", differences)
        observed = sum(r.get("status") == "observed" for r in found.values())
        restarted = sum("restart_semantics" in r for r in found.values())
        rows.append({"name": name, "status": score["execution_status"], "n": score["n"], "admissible_passed": score["passed"],
                     "raw_exact_gold_passed": sum(r["passed"] for r in exact), "initial_observations": observed, "restarted_observations": restarted,
                     "differences_rows": len(differences), "groups": score["groups"], "candidate_archive_matches": True,
                     "score_sha256": sha(out / "evaluation/score.json"), "observations_sha256": sha(out / "evaluation/observations.json")})
        dump(HERE / "progress.json", rows)
        print(json.dumps(rows[-1]), flush=True)
    unchanged = immutable() == read(HERE / "immutable_inputs_before.json")
    dump(HERE / "primary_summary.json", {"planned_candidates": 3, "recorded_candidates": len(rows), "rows": rows,
         "immutable_inputs_unchanged": unchanged, "supplemental_pending": True,
         "interpretation": "An author-constructed resource validation, not spontaneous agent behavior, prevalence, or a main-model intervention. Full source/premise and supplemental fallback review still required."})
    assert unchanged


def supplemental():
    plan = read(HERE / "plan.json")
    assert immutable() == read(HERE / "immutable_inputs_before.json")
    assert sha(Path(__file__)) == plan["experiment_script_sha256"]
    assert sha(HERE / "supplemental_worker.py") == plan["supplemental_worker_sha256"]
    primary = read(HERE / "primary_summary.json")
    assert primary["recorded_candidates"] == 3
    settings = read(DATA / "plan.json")
    remote = settings["remote_root"] + "/independent_multisolution_" + HERE.name
    archive_path = HERE / "supplemental_input.tar"
    with tarfile.open(archive_path, "w") as archive:
        archive.add(HERE / "supplemental_worker.py", arcname="supplemental_worker.py")
        archive.add(HERE / "supplemental/collision.json", arcname="collision.json")
    def command(args, timeout=40):
        p = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)
        assert p.returncode == 0, (args[:2], p.returncode, p.stderr)
        return p
    command(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", HOST, "mkdir -p " + shlex.quote(remote)])
    command(["scp", "-q", str(archive_path), HOST + ":" + remote + "/inputs.tar"])
    command(["ssh", HOST, "tar -xf " + shlex.quote(remote + "/inputs.tar") + " -C " + shlex.quote(remote)])
    rows = []
    targets = [("producer_premises", settings["remote_root"] + "/packages/" + TASK + "/base", "producer")]
    for entry in plan["candidates"]:
        execution = read(HERE / "candidates" / entry["name"] / "evaluation/execution.json")
        candidate_remote = next(arg.removesuffix(":/candidate:ro") for arg in execution["command"] if arg.endswith(":/candidate:ro"))
        targets.append((entry["name"], candidate_remote, "candidate"))
    for name, candidate_remote, mode in targets:
        output = HERE / "supplemental_results" / name
        output.mkdir(parents=True, exist_ok=True)
        assert not (output / "execution.json").exists(), "Do not repeat supplemental executions"
        args = ["timeout", "--kill-after=5s", "45s", "docker", "run", "--rm", "--network", "none", "--cpus", "1", "--memory", "1g",
            "--pids-limit", "64", "--read-only", "--cap-drop", "ALL", "--security-opt", "no-new-privileges", "--user", "65534:65534",
            "--tmpfs", "/tmp:rw,nosuid,nodev,size=32m", "-e", "OPENBLAS_NUM_THREADS=1", "-e", "OMP_NUM_THREADS=1",
            "-v", candidate_remote + ":/candidate:ro", "-v", settings["runtime_remote"] + ":/runtime:ro",
            "-v", remote + ":/review:ro", settings["image"], "python", "-I", "-B", "/review/supplemental_worker.py", "--mode", mode]
        start = time.perf_counter()
        p = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", HOST, shlex.join(args)],
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=70)
        (output / "stdout.txt").write_text(p.stdout, encoding="utf-8")
        (output / "stderr.txt").write_text(p.stderr, encoding="utf-8")
        dump(output / "execution.json", {"command": args, "exit": p.returncode, "wall_seconds": time.perf_counter() - start})
        try: result = json.loads(p.stdout) if p.returncode == 0 else {"status": "engineering_error", "exit": p.returncode}
        except json.JSONDecodeError: result = {"status": "invalid_output"}
        dump(output / "result.json", result)
        rows.append({"name": name, "result": result})
        print(json.dumps(rows[-1]), flush=True)
    unchanged = immutable() == read(HERE / "immutable_inputs_before.json")
    summary = {**primary, "supplemental_pending": False, "supplemental": rows, "immutable_inputs_unchanged": unchanged,
        "plan_sha256": sha(HERE / "plan.json"), "source_policy_review": "PENDING_INDEPENDENT_REVIEW",
        "main_experiment_modified": False, "api_calls": 0, "gpu_calls": 0}
    dump(HERE / "summary.json", summary)
    assert unchanged


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("prepare", "evaluate", "supplemental"))
    action = parser.parse_args().action
    {"prepare": prepare, "evaluate": evaluate, "supplemental": supplemental}[action]()
