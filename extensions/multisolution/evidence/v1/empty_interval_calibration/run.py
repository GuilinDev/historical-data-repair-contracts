import hashlib
import json
from pathlib import Path
import shlex
import subprocess
import time

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
DATA = ROOT / "results/resource/20261030T212309"
settings = json.loads((DATA / "plan.json").read_text())
host = "<author-host>"
remote = settings["remote_root"] + "/independent_multisolution_20261030T222403/interval_calibration"
for args in (["ssh", host, "mkdir -p " + shlex.quote(remote)], ["scp", "-q", str(HERE / "worker.py"), host + ":" + remote + "/worker.py"]):
    p = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30)
    assert p.returncode == 0, p.stderr
args = ["timeout", "--kill-after=5s", "30s", "docker", "run", "--rm", "--network", "none", "--cpus", "1", "--memory", "1g", "--read-only",
        "--cap-drop", "ALL", "--security-opt", "no-new-privileges", "--user", "65534:65534", "--tmpfs", "/tmp:rw,nosuid,nodev,size=32m",
        "-e", "OPENBLAS_NUM_THREADS=1", "-v", settings["remote_root"] + "/packages/jsonpickle_dtype_634/base:/candidate:ro",
        "-v", settings["runtime_remote"] + ":/runtime:ro", "-v", remote + ":/review:ro", settings["image"], "python", "-I", "-B", "/review/worker.py"]
start = time.perf_counter()
p = subprocess.run(["ssh", host, shlex.join(args)], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=50)
(HERE / "stdout.txt").write_text(p.stdout, encoding="utf-8")
(HERE / "stderr.txt").write_text(p.stderr, encoding="utf-8")
(HERE / "execution.json").write_text(json.dumps({"command": args, "exit": p.returncode, "wall_seconds": time.perf_counter() - start}, indent=2), encoding="utf-8")
assert p.returncode == 0, p.stderr
result = json.loads(p.stdout)
for row in result["rows"]:
    artifact = HERE / (row["case"] + ".json")
    artifact.write_text(row["document"], encoding="utf-8")
    row["artifact_sha256"] = hashlib.sha256(artifact.read_bytes()).hexdigest()
(HERE / "producer_receipt.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
print(json.dumps(result))
