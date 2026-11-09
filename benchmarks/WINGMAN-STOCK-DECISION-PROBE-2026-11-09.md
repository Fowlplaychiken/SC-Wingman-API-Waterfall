# Stock Wingman decision-stage probe

Jev alone supplied bounded decisions through original Wingman interfaces. Of
22 provider responses, 21 reader outcomes matched the prepared references
(95.5% agreement). Median request wall time was 136 ms; total reported cost
was USD 0.000537978. This is a small decision-stage observation, not general
assistant accuracy, installed Core latency or a Main/Support recommendation.

| Original stock path | Matching reader outcomes | Median wall time | Reported cost for this group |
|---|---:|---:|---:|
| Command recognition | 7 / 8 | 140 ms | USD 0.000283962 |
| Vocabulary confirmation | 7 / 7 | 141 ms | USD 0.000119364 |
| Unnamed profile choice | 4 / 4 | 131 ms | USD 0.000075012 |
| UEX fuzzy entity choice | 3 / 3 | 107 ms | USD 0.000059640 |

One additional exact UEX match bypassed the provider. One vocabulary fixture
was skipped because the proposed heard word was absent from its sentence.
Neither is included in provider latency, cost or the 22-response denominator.

The discrepant command selected Flight Ready with confidence 1.0, but its
separate multiple-command probability was 0.91. The original reader therefore
declined preselection. This is a false decline relative to the single-command
reference, not an incorrectly executed action or low command confidence. Core
would retain its Main fallback; that later Main response was not measured.

## What actually ran

The finite Python client used the original
[decision provider](https://github.com/ShipBit/wingman-ai/blob/f0364106a9ea13eb5aee45f73cd9af86c79cd213/providers/decision_model.py),
[question builders/readers](https://github.com/ShipBit/wingman-ai/blob/f0364106a9ea13eb5aee45f73cd9af86c79cd213/services/decision_questions.py)
and isolated original UEX matching method at Wingman 3.2.14. It requested
`~typesafe/jev-latest` through the existing stored OpenRouter credential and
received `typesafe/jev-1.14-20261014`. No extra Main-model helper, Support helper,
new provider key, game action or installed-Core patch was used.

Requests were sequential, from the Windows desktop to the remote provider,
with a reused HTTP session. The first call took 735 ms; the median therefore
does not describe a cold start. Cost is provider-reported usage, not an invoice.
For this exact task mixture the mean was about USD 0.02445 per 1,000 decision
requests. That is a normalization, not another 1,000 measured tasks.

## Limits that affect interpretation

- This was not the running Core application. Actual gate scheduling, cooldowns,
  tool execution, Main acknowledgement and audio were not measured. Vocabulary
  results are marked discarded if their request exceeds the original 1.5-second
  deadline; no measured vocabulary request crossed it. Independent fixtures do
  not exercise the original 60-second failure cooldown.
- The command offer contains eight described options, not the full 65-command
  template. Toggle choices measure recognized intent, not resulting ship state.
  Declining a multi-command input is not proof of completing its requested actions.
- Vocabulary-off references were corrected from source: candidate ordinary words
  are protected. Complete sentence-level fuzzy matching was not executed because
  its dependency was unavailable. Positive examples contain explicit name context,
  so these are conditional decisions, not difficult microphone/STT benchmarks.
- UEX fuzzy candidates were supplied to the original matching stage, not captured
  from a live database/retrieval pipeline. Two positive examples are already in
  the supplier's prompt. The ambiguous variant case correctly returned guesses
  rather than a committed ship variant. The exact-hit bypass is source-equivalent
  branch simulation, not a database operation.
- The references, frozen command offer and runner hashes are retained in the
  [aggregate data](WINGMAN-STOCK-DECISION-PROBE-2026-11-09.json). Private request
  traces remain outside Git. No model was trained or prompt revised after these
  responses, and no failed call was retried.

Main and Support comparisons remain separate. Background Support cost/latency
must not be added to every foreground turn: memories/summaries run conditionally;
some explicit skill Support calls are awaited. Measure the actual path and
trigger frequency before reporting whole-session speed or cost. Keep our earlier
added-helper results separate from these stock decisions.
