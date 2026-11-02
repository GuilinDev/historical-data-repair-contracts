Paper figures and data
======================

The three figures are vector PDF and SVG, with 600 dpi PNG copies for web use.
Each has an accessible description in figure_manifest.json. figure_data.json
contains all table rows, all 32 episode identities and scores, all calibration
counts, the collision witness, and hashes of the underlying public evidence.

Rebuild from the released records (no model API or Docker execution)::

    python3 -m pip install -r paper/requirements-paper.txt
    python3 paper/derive_evidence.py
    python3 paper/render_figures.py
    python3 paper/verify_numbers.py
    python3 paper/render.py --output ./manuscript-rerender.pdf

Figure 1 is a schematic of actual identical complete documents, not a measured
collision frequency. Document-wide type inference in Figure 2 uses different,
multi-column documents with more explicit information. It cannot identify the
original width from Figure 1's single ambiguous column.

Figure 2 reports complete fixed-corpus counts with a zero baseline. Its two valid
repairs and one corrupting control are researcher-built; none is a Gemma outcome.
Literal reference equality is not an authentic upstream test suite.

Figure 3 includes every main attempt. Its counts are the original completed main
evaluation, not the later patch replay. Colors mark full acceptance only. U means
unchanged source; E means edited but incomplete; C marks the exploration request
byte guard and can accompany U, E or PASS. These are not inferred failure causes. Repeated
observations, artifacts and conversations are not independent defect samples.

The table uses the fresh-installation control run: base 99/252, including dtype
6/84. An earlier saved dtype control had 1/84. Process-dependent partial counts
are disclosed; the full acceptances are stable in the completed patch replay.

No confidence intervals are drawn because no independent-sample inference is
being made. No observations were omitted, smoothed or imputed. The source files
and provenance hashes allow exact numerical audit; visual QA does not prove
scientific validity.

The subtitle, defect-lineage definition, H/F/R input regimes and writable-joblib
example explain the resource contract. The two table groups distinguish the four
development-exposed from the four newly added lineages, without claiming pristine
held-out data. Static diagnostics and the contract example's source anchors are
under evidence/independent_review/clarity_revision_20261031/. Request byte counts
in that audit refer to archived requests before privacy redaction, not tokens or
the model's measured context capacity. Public logs preserve the tool events, but
their serialized byte lengths can differ after redaction. No new model calls
were made for the clarity revision.

Scientific Agent Skills guided figure preparation and audit:
Timothy Kassis, Vinayak Agarwal, Yuhuan He, Darshil Patel and Aubrey M. Brueckner
(2026), Scientific Agent Skills: A Library of Procedural Knowledge for Research
Agents, https://doi.org/10.48550/arXiv.2609.00065.

The source Markdown image paths are local assets. When publishing the Kaggle
writeup, upload those assets or replace the paths with the actual public URLs;
the supplied standalone PDF already includes every figure.
