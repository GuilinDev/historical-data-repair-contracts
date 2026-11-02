# Historical-Data Repair Contracts for Gemma Agents

**Guilin Zhang** · A Calibrated Resource for Compatibility and Persistence Repair

[Paper](paper/historical-data-compatibility.pdf) · [Frozen v0.1.2 release](https://github.com/GuilinDev/historical-data-repair-contracts/releases/tag/v0.1.2) · [Reproduction guide](README.rst)

Historical data may load successfully and still lose a later mutation. It may also omit information that no reader can recover. These executable contracts make accepted behavior explicit, including when compatibility must yield to an authorization boundary.

| Evidence | Scope and completed result |
|---|---|
| Repair resource | Eight lineages, six Python projects, 65 historical files, 252 dependent observations |
| Numeric calibration | Two valid repairs 84/84 each; literal equality rejects 60 observations for one; corrupting control 0/84 |
| Original Gemma baseline | 32 completed attempts, 4 complete acceptances; unchanged from v0.1.1 |
| New controlled experiment | 64 completed attempts on one deployment: exact text 10/32, version-checked span 4/32 |
| Complete patch replay | All 64 complete-acceptance decisions stable; 4 rejected dtype attempts change partial observations |
| Separate AI SDK calibration | One known defect, 54 related scenarios; upstream fix 54/54; all five implementations completed |

The editor result is negative for the tested interface package; it is not a universal editor ranking. The signature calibration is not a new vulnerability, a ninth Gemma task, or a full authorization-system audit. The original 32, new 64 and separate 54-scenario groups retain distinct denominators.

## Verify without a model API

Python 3.10+ suffices for static checks on any OS:

```sh
python3 compat.py integrity
python3 reproduce_ablation.py --records-only
python3 reproduce_approval.py --records-only
python3 paper/verify_numbers.py
```

To **execute** all new patches, use the qualified Linux ARM64/Docker environment:

```sh
python3 compat.py bootstrap --pull
python3 reproduce_ablation.py --output results/new64-check
```

The signature calibration needs Node 22.22.3 or 24.22.1 (qualified), Python and no npm packages:

```sh
python3 reproduce_approval.py --output results/approval-check
```

Each execution writes a fresh directory. These commands make **no model API calls**. Static checks validate recorded evidence; they do not execute patched library behavior. [Full commands and limitations](README.rst).

## Inspect and reuse

- [All 64 attempts, frozen plan, overlays and recoveries](evidence/editing_ablation/) and [independent final audit](evidence/editing_ablation/final_independent_audit.json).
- [AI SDK fixtures, source, five controls and audit](extensions/approval_signature/).
- [Original 32 baseline](evidence/main/) and [prior paper/attempt matrix](supplement/v0.1.1_paper/).
- [Paper source, figures and alt text](paper/), [current independent publication reviews](evidence/independent_review/v012/), [file manifest](release_manifest.json).

Every failed attempt is retained. Author-path redactions have original/exported hashes; source overlays remain byte-exact. Duplicate tar containers are replaced by reconstruction plus member hashes. Historical pending-status snapshots are retained; final independent audits and the portable verifier establish completion.

Original contributions use [Apache-2.0](LICENSE); upstream terms and attribution are retained in [NOTICE](NOTICE) and source license copies. No model weights or private credentials are distributed. See [CITATION.cff](CITATION.cff). Codex assistance is disclosed in the paper. The earlier [v0.1.1 release](https://github.com/GuilinDev/historical-data-repair-contracts/releases/tag/v0.1.1) remains available.
