# RAG Evaluation

The public repository intentionally ships without private evaluation baselines.
Run the commands below against your own vault to populate `eval/baselines/` and
register publishable experiments in `eval/experiment_registry.json`.

`questions.jsonl` contains user-facing questions and a category. `expected.jsonl`
contains the manually reviewed labels with the same `id`; keeping them separate
prevents an application query from accidentally receiving expected answers.

For answerable cases, label the source IDs as they appear in the corresponding
video's source map, plus short answer points that an evaluator can review. For
unanswerable cases, set `expected_answer` to `false` and leave sources empty.
Source IDs are only unique inside one video. Multi-video cases must also provide
`expected_sources`, an array of `{ "video_id": "...", "source_ref": "S0001" }`
pairs, so source coverage cannot be falsely credited by another video's S-number.
Do not label uncertain ASR terms as medical facts.

Run retrieval-only evaluation:

```powershell
uv run --no-sync python eval/run_eval.py
```

Run the optional Ollama generation checks and token capture:

```powershell
uv run --no-sync python eval/run_eval.py --with-generation
```

Reports are timestamped under `eval/reports/` and ignored by Git. Retrieval
metrics are exact and deterministic against the labels. Reports include Hit Rate@K,
Precision@K, MRR, novelty-aware nDCG@K, and source coverage. nDCG only rewards the
first Chunk that covers each reviewed `video_id + source_ref`, so duplicate retrieval
cannot inflate ranking quality. `heuristic_grounded_answer_rate`
is intentionally conservative: it requires every answer point to occur in the
output and every cited video to have been retrieved. It is a regression signal,
not a replacement for clinical expert review or an entailment judge.

## Claim-level semantic grounding

Run generation plus claim/evidence entailment checks with the local Ollama model:

```powershell
uv run --no-sync python eval/run_eval.py --with-generation --claim-semantic-grounding
```

This adds `claim_semantic_grounding_rate` to each generated answer and to the
aggregate report. The judge only sees each claim and the exact chunks cited by
that claim. It returns `supported`, `contradicted`, or `insufficient_evidence`.
Citation failures are classified as insufficient evidence without calling the
judge. Semantic checks are opt-in because they add approximately one model call
per claim.

The standalone judge benchmark uses `claim_labels.jsonl`:

```json
{"id":"case-id","claim":"claim text","evidence":[{"chunk_id":"actual-id","source_refs":["S0001"],"text":"exact evidence"}],"label":"supported","annotation_status":"reviewed","notes":"review note"}
```

Allowed labels are `supported`, `contradicted`, and `insufficient_evidence`.
Run it with:

```powershell
uv run --no-sync python eval/claim_grounding_eval.py --reviewed-only `
  --baseline-output eval/baselines/claim-grounding-reviewed.json
```

The script writes JSON and Markdown reports plus a `*-review.jsonl` queue. A
case enters that queue on label disagreement, low/medium confidence, judge
failure, or an insufficient-evidence verdict. These labels assess textual
entailment only; they do not certify the clinical truth of the evidence.
The committed set currently contains 30 labels marked `reviewed`. This status means
the human checked the textual claim/evidence relation; it does not certify source
transcript authenticity or medical truth. BVID/S-reference verification and seven
medical-risk checks remain listed in `CLAIM_LABEL_REVIEW.md`.

Repeat stochastic generation evaluation at least three times before reporting a
grounding result:

```powershell
uv run --no-sync python eval/repeat_generation_eval.py --runs 3 `
  --claim-semantic-grounding --baseline-output eval/baselines/generation-repeat.json
```

The output includes mean, min, max, standard deviation, weighted claim metrics,
and a `same_model_judge` flag. Prefer an independent judge or blinded human review;
the local generation setup uses `qwen2.5:7b` for both generation and judging.

An independent Chinese judge can be run with the separately downloaded `glm4:9b`:

```powershell
ollama pull glm4:9b
uv run --no-sync python eval/claim_grounding_eval.py --reviewed-only `
  --model glm4:9b `
  --output-prefix claim-grounding-glm4-9b `
  --baseline-output eval/baselines/claim-grounding-glm4-9b.json
```

The 30-case independent baseline currently reports Accuracy `0.9667`, Macro-F1
`0.9645`, and 9 review cases. `thyroid-002` is the only label disagreement;
the model treated the evidence as insufficient because it says primary Graves'
is one major cause while also saying there are multiple causes. Keep both model
outputs and send disagreements or low-confidence cases to human review. The
independent model is a textual entailment checker, not a clinical truth oracle.

To compare two completed judge reports without re-running either model:

```powershell
uv run --no-sync python tools/compare_claim_judges.py `
  --qwen-report eval/reports/claim-grounding-reviewed-30-evidence-v2-20260918.json `
  --glm-report eval/reports/claim-grounding-glm4-9b.json `
  --output eval/reports/claim-judge-dual-YYYYMMDD.json `
  --baseline-output eval/baselines/claim-judge-dual-YYYYMMDD.json
```

The comparison marks any model disagreement, non-high confidence result, and
`insufficient_evidence` result for human review. The current comparison has
29/30 agreement and one disagreement (`thyroid-002`).

To regenerate the remaining source-authenticity queue after a human review record:

```powershell
uv run --no-sync python tools/audit_claim_sources.py `
  --remaining-only `
  --review-record eval/reports/claim-grounding-glm4-9b-human-review-20260919.json `
  --output eval/reports/claim-source-audit-remaining-YYYYMMDD.json
```

## RRF versus reranker

Run both retrieval modes against the same labels and write a compact baseline:

```powershell
uv run --no-sync python eval/compare_reranker.py `
  --reranker-model "D:\tool\bge-reranker-v2-m3" `
  --top-k 5 --candidate-k 20 `
  --baseline-output eval/baselines/reranker-comparison-evidence-v2-dedup-20260918.json
```

The timestamped reports contain per-case details and stay ignored by Git. The
optional baseline contains corpus size, aggregate metrics, model path, and
latencies so results from different index snapshots are not mixed together.

## Experiment registry

`experiment_registry.json` is the machine-readable record of optimization
experiments. `EXPERIMENT_LOG.md` is its human-readable companion. Every entry
records the corpus snapshot, control, variant, metric deltas, latency cost,
decision, limitations, evidence files, and implementation commit.

Validate both files before committing a new experiment:

```powershell
uv run --no-sync python tools/validate_experiments.py
```
