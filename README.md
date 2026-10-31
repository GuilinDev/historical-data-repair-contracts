# Historical-Data Repair Contracts for Gemma Agents

**Guilin Zhang** · A Calibrated Resource for Compatibility and Persistence Repair

[Read the paper](paper/historical-data-compatibility.pdf) · [Download v0.1.0](https://github.com/GuilinDev/historical-data-repair-contracts/releases/tag/v0.1.0) · [Full reproduction guide](README.rst) · [CLI reference](CLI_USAGE.txt)

An old file can load successfully while losing a later mutation. An old format can also omit information that no reader can recover. This resource makes the accepted behavior explicit and tests loading, continued use, and persistence in fresh processes.

| Included | Scale or result |
| --- | --- |
| Executable contracts | 8 defect lineages across 6 Python projects |
| Historical artifacts | 65 distinct files; 17 public to the agent, 48 withheld during repair |
| Frozen observations | 252 dependent checks, **not 252 independent bugs** |
| Calibration | Two valid repairs: 84/84 each; a corrupting repair: 0/84 |
| Literal reference equality | Accepts only 24/84 observations for the valid alternative |
| Gemma baseline | All 32 attempts completed and evaluated; 4 pass semantics and source review |
| Independent patch replay | 1,008 observations; the same 4 complete acceptances |

The contribution is the executable resource and acceptance calibration. The motivating jsonpickle ambiguity was known upstream. The sample does not estimate general coding-agent ability, and provider/precision differences confound model comparisons.

## Start without a model API

Download and extract the versioned archive, or clone this repository. Preserve file bytes; `.gitattributes` disables line-ending conversion. Python 3.10+ is sufficient for these static checks:

```sh
python3 compat.py integrity
python3 reproduce_main.py --records-only
python3 paper/verify_numbers.py
```

Integrity verifies file identities; it does **not** rerun repair behavior. To execute a complete task, use **Linux ARM64 with Docker** (the qualified environment), then:

```sh
python3 compat.py bootstrap --pull
python3 compat.py reproduce --task joblib_endian_1561 --controls base,fixed,reference
```

Bootstrap downloads a digest-pinned base image. Included wheels install the fixed dependencies. Candidate execution is network-disabled; these verification commands make no model API calls. Other architectures are not qualified. See the [full guide](README.rst) for all-task reproduction, patch evaluation and optional, explicitly paid model reruns.

## Inspect the evidence

- [Tasks and source edit scopes](tasks.json), [frozen observations](oracle/), [historical artifacts and source](bundle/), and [upstream provenance and licenses](provenance/).
- [Completed model results](evidence/main/summary.json), [source-policy audit](evidence/independent_review/main_audit.json), and [cold reproduction](validation/cold_reproduction_summary.json).
- [Calibration witness](evidence/collision/summary.json), [trajectory verification](evidence/independent_review/clarity_revision_20261031/trajectory_verification.json), and [latest manuscript reviews](evidence/independent_review/publication_revision_20261031/).
- [Manifest](release_manifest.json) and [SHA-256 file list](SHA256SUMS). The versioned release also contains the archive checksum and paper PDF.

Recorded logs have declared author-path redactions with original/exported hashes. No API keys or model weights are distributed. Frozen data and original verdicts are retained; this is an open research resource, not a secure hidden evaluation service.

## License and citation

Original task definitions, verifier, documentation and manuscript are released under [Apache-2.0](LICENSE). Third-party artifacts retain their own terms; see [NOTICE](NOTICE) and [license copies](provenance/licenses/). Use [CITATION.cff](CITATION.cff) to cite the resource. Codex assistance and evaluated Gemma patches are disclosed in the paper.
