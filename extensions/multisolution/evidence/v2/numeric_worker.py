"""Only executed in isolated, no-network containers; never imported on host."""
import argparse
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile

sys.path[:0] = ["/candidate", "/runtime"]


def snapshot(frame):
    return {"columns": frame.columns.tolist(), "index": frame.index.tolist(),
        "columns_data": [{"dtype": str(frame[column].dtype), "kind": frame[column].dtype.kind,
                          "itemsize": frame[column].dtype.itemsize, "values": frame[column].tolist()}
                         for column in frame.columns]}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("producer", "candidate", "restart"))
    parser.add_argument("--input")
    args = parser.parse_args()
    import jsonpickle
    import jsonpickle.ext.pandas
    import jsonpickle.tags_pd as tags
    import numpy as np
    import pandas as pd
    jsonpickle.ext.pandas.register_handlers()
    if args.mode == "producer":
        names = sorted(name for name in tags.get_all_pandas_dtype_strings()
                       if isinstance(name, str) and re.fullmatch(r"(?:float|int|uint)\d+", name))
        universe = {stem: sorted(int(name[len(stem):]) for name in names if re.fullmatch(stem + r"\d+", name))
                    for stem in ("float", "int", "uint")}
        numeric_map = {name: tags.TYPE_MAP[name] for name in names}
        return {"status": "observed", "jsonpickle_version": jsonpickle.__version__, "pandas_version": pd.__version__,
                "numpy_version": np.__version__, "candidate_file": jsonpickle.__file__, "numeric_pd_dtype_names": names,
                "numeric_universe": universe, "actual_numeric_type_map": numeric_map,
                "numeric_tags_injective": len(set(numeric_map.values())) == len(numeric_map),
                "universe_matches_declared": universe == {"float": [32, 64], "int": [8, 16, 32, 64], "uint": [8, 16, 32, 64]}}
    if args.mode == "restart":
        return {"snapshot": snapshot(jsonpickle.decode(Path(args.input).read_text(encoding="utf-8")))}
    cases = [("historical_lone_collision", None, 8), ("new_float32", "float32", 4), ("new_float64", "float64", 8)]
    records = []
    for name, dtype, expected_width in cases:
        if dtype is None:
            document = Path("/review/collision.json").read_text(encoding="utf-8")
        else:
            document = jsonpickle.encode(pd.DataFrame({"x": np.asarray([1.125, -3.25], dtype=dtype)}))
        obj = jsonpickle.decode(document)
        initial = snapshot(obj)
        with tempfile.TemporaryDirectory(prefix="multisolution-") as directory:
            target = Path(directory) / "resaved.json"
            target.write_text(jsonpickle.encode(obj), encoding="utf-8")
            completed = subprocess.run([sys.executable, "-I", "-B", __file__, "--mode", "restart", "--input", str(target)],
                                       capture_output=True, text=True, timeout=15)
            assert completed.returncode == 0, completed.stderr
            restart = json.loads(completed.stdout)["snapshot"]
        expected = {"columns": ["x"], "index": [0, 1], "columns_data": [{"dtype": "float" + str(expected_width * 8),
                    "kind": "f", "itemsize": expected_width, "values": [1.125, -3.25]}]}
        records.append({"case": name, "initial": initial, "restart": restart,
                        "initial_passed": initial == expected, "restart_passed": restart == expected,
                        "passed": initial == expected and restart == expected})
    return {"status": "observed", "records": records, "passed": sum(r["passed"] for r in records), "n": len(records)}


if __name__ == "__main__":
    print(json.dumps(main()))
