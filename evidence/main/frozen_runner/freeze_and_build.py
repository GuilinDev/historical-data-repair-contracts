"""Freeze public inputs and run the tool's documented default CPU build."""
from __future__ import annotations

import hashlib
import importlib.metadata as md
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "results" / "cpu_pilot_v0"
REPOS = {
    "packaging": ("https://github.com/pypa/packaging", "78d8673da4d275bb7aee0126a5edc3cf90976af1"),
    "itsdangerous": ("https://github.com/pallets/itsdangerous", "672971d66a2ef9f85151e53283113f33d642dabd"),
    "cachetools": ("https://github.com/tkem/cachetools", "505aece98f29a1aca26c4ae5ba57e03a8a3397f3"),
}


def write_json(path, data):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def git(repo, *args):
    return subprocess.check_output(["git", "-C", str(repo), *args], text=True).strip()


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    installed = sorted(f"{d.metadata['Name']}=={d.version}" for d in md.distributions())
    (OUT / "environment.txt").write_text("\n".join(installed) + "\n", encoding="utf-8")
    write_json(OUT / "machine.json", {"python": sys.version, "platform": platform.platform(),
                                     "tool": md.version("code-review-graph"), "jedi": md.version("jedi")})
    for name, (url, expected) in REPOS.items():
        repo = ROOT / "corpus" / name
        head = git(repo, "rev-parse", "HEAD")
        assert head == expected, (name, head, expected)
        tracked = git(repo, "ls-files").splitlines()
        selected = [p for p in tracked if p.endswith(".py") or "LICENSE" in p or p == "pyproject.toml"]
        manifest = {p: {"sha256": hashlib.sha256((repo / p).read_bytes()).hexdigest(),
                        "bytes": (repo / p).stat().st_size} for p in selected}
        write_json(OUT / name / "source_manifest.json", {
            "url": url, "commit": head, "selection": "all tracked Python plus license and project metadata",
            "files": manifest, "git_status_before": git(repo, "status", "--porcelain"),
        })
        env = os.environ.copy()
        env.update(CRG_HOME=str(ROOT / ".cache" / "crg-home"),
                   CRG_DATA_DIR=str(OUT / name / "graph"),
                   PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
        cmd = [str(Path(sys.executable).with_name("code-review-graph.exe")), "build", "--repo", str(repo)]
        start = time.perf_counter()
        proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", env=env, timeout=600)
        elapsed = time.perf_counter() - start
        (OUT / name / "build.stdout.txt").write_text(proc.stdout, encoding="utf-8")
        (OUT / name / "build.stderr.txt").write_text(proc.stderr, encoding="utf-8")
        write_json(OUT / name / "build.json", {"command": cmd, "wall_seconds": elapsed,
                   "exit_code": proc.returncode, "git_status_after": git(repo, "status", "--porcelain"),
                   "source_unchanged": all(hashlib.sha256((repo/p).read_bytes()).hexdigest() == v["sha256"]
                                           for p, v in manifest.items()),
                   "environment_overrides": {k: env[k] for k in ("CRG_HOME", "CRG_DATA_DIR", "PYTHONUTF8")}})
        print(json.dumps({"repo": name, "build_seconds": elapsed, "exit_code": proc.returncode}), flush=True)
        if proc.returncode:
            raise RuntimeError(f"{name} build failed; inspect stored logs")


if __name__ == "__main__":
    main()
