import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
sys.path[:0] = ["/candidate", "/runtime"]


def snapshot(frame):
    return {"columns": frame.columns.tolist(), "length": len(frame),
            "dtypes": {c: str(frame[c].dtype) for c in frame},
            "values_repr": {c: [repr(v) for v in frame[c].tolist()] for c in frame}}


def main():
    parser = argparse.ArgumentParser(); parser.add_argument("--input"); args = parser.parse_args()
    import jsonpickle
    import jsonpickle.ext.pandas
    jsonpickle.ext.pandas.register_handlers()
    if args.input:
        return snapshot(jsonpickle.decode(Path(args.input).read_text(encoding="utf-8")))
    rows = []
    producer = json.loads(Path("/review/producer_receipt.json").read_text(encoding="utf-8"))
    for original in producer["rows"]:
        document = Path("/review") / (original["case"] + ".json")
        assert hashlib.sha256(document.read_bytes()).hexdigest() == original["artifact_sha256"]
        obj = jsonpickle.decode(document.read_text(encoding="utf-8")); initial = snapshot(obj)
        with tempfile.TemporaryDirectory(prefix="multisolution-interval-") as directory:
            target = Path(directory) / "resaved.json"
            target.write_text(jsonpickle.encode(obj), encoding="utf-8")
            p = subprocess.run([sys.executable, "-I", "-B", __file__, "--input", str(target)], capture_output=True, text=True, timeout=15)
            assert p.returncode == 0, p.stderr
            restart = json.loads(p.stdout)
        rows.append({"case": original["case"], "original_input": original["input"], "initial": initial, "restart": restart})
    hashes = {name: hashlib.sha256((Path("/candidate") / name).read_bytes()).hexdigest()
              for name in ("jsonpickle/__init__.py", "jsonpickle/tags_pd.py", "jsonpickle/ext/pandas.py")}
    return {"status": "observed", "records": rows, "loaded_source_hashes": hashes}


if __name__ == "__main__": print(json.dumps(main()))
