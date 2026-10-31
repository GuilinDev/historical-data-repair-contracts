import json
import sys
sys.path[:0] = ["/candidate", "/runtime"]
import numpy as np
import pandas as pd
import jsonpickle
import jsonpickle.ext.pandas
import jsonpickle.tags_pd as tags
jsonpickle.ext.pandas.register_handlers()


def snapshot(frame):
    return {"columns": frame.columns.tolist(), "length": len(frame),
            "dtypes": {c: str(frame[c].dtype) for c in frame},
            "values_repr": {c: [repr(v) for v in frame[c].tolist()] for c in frame}}


frames = {
    "empty_interval_alone": pd.DataFrame({"span": pd.Series([], dtype=pd.IntervalDtype("int64", "right"))}),
    "empty_interval_and_explicit_integers": pd.DataFrame({
        "span": pd.Series([], dtype=pd.IntervalDtype("int64", "right")),
        "signed16": np.asarray([], dtype="int16"), "signed32": np.asarray([], dtype="int32"), "signed64": np.asarray([], dtype="int64")}),
    "nonempty_interval_and_integers": pd.DataFrame({
        "span": pd.arrays.IntervalArray.from_tuples([(1, 3), (5, 8)]),
        "signed16": np.asarray([1, 2], dtype="int16"), "signed32": np.asarray([3, 4], dtype="int32"), "signed64": np.asarray([5, 6], dtype="int64")})
}
rows = []
for name, frame in frames.items():
    document = jsonpickle.encode(frame)
    try: restored = snapshot(jsonpickle.decode(document))
    except Exception as exc: restored = {"error": type(exc).__name__ + ": " + str(exc)}
    rows.append({"case": name, "input": snapshot(frame), "dtype_names": {c: frame[c].dtype.name for c in frame},
                 "document": document, "same_process_restored": restored})
print(json.dumps({"jsonpickle_version": jsonpickle.__version__, "pandas_version": pd.__version__,
                  "interval_type_map": {str(k): v for k, v in tags.TYPE_MAP.items() if "interval" in str(k).lower()}, "rows": rows}))
