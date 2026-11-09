# Wingman assistant models — 9 November 2026

The current selection target is **Claude Haiku 5.5, adaptive thinking, low
effort**, with **LFM3 1.7B Q4_K_M on Legacy** as the best local fallback in this
matched development cohort. Selection is separate from deployment. The installed
Waterfall currently uses Haiku through Anthropic's compatible endpoint and the
original local Qwen service; the exact winning thinking controls and replacement
fallback have not yet been activated.

## What was measured

Twenty-four frozen tasks per model/settings arm, eleven arms, 264 original
attempts. These are ordinary Wingman assistant tasks, not engineering-image
tests. The 24 competing source-shaped tools cover SC Radio and chatter,
keybindings, game-log context, distances and waypoints, UEX trading data,
Accountant, document summarization, provider status and remembered tablet
indicators. Eight tasks require multiple steps or a later user choice. Proposed
actions never execute. The game is closed.

The corpus, source-shaped schemas, canned read responses and answer requirements
were frozen before the calls. A separate blinded AI review judges material
outcomes; the parent checks failures and random passing samples. This is a small
development sample, not human ground truth or a general model accuracy score.
Native supplier implementations, speech and gameplay are not being requalified.

The first collector wrongly treated some valid query forms as missing results.
Its original exploratory calls are retained privately and excluded, rather than
counted as model failures. The corrected environment permits independently
ordered read calls and source-documented default equivalences. Its residual
uncovered reads remain **unknown**, never invented negative search results.
Two reversed geometric-distance queries can reuse the same frozen distance;
their bounded corrected-fixture follow-up is recorded separately. No prompt or
reference answer is tuned to a model response.

## Timing, accuracy and money

The maintained aggregate and chart publish correct, partial, failed and unknown
counts. Accuracy is fully correct divided by scorable cases; correct/24 is also
shown so unknown cases do not create an apparent perfect full-cohort score.
Each arm's timing view uses its 24 task outcomes, substituting the two corrected
distance follow-ups only where applicable. Original attempts remain retained,
and all-call cost accounting includes follow-ups. Medians and p90
describe this run, with no claim of statistical superiority from a one-case gap.

Timing covers the whole nonstreaming model/tool-response loop, not first-token
latency, speech completion or a player's end-to-end experience. API prices sum
input and output tokens over **every** recorded turn. Haiku's list estimate uses
$0.10/$0.50 per million input/output tokens for these short requests; Sonnet uses
$2/$10. They are estimates, not invoice charges. OpenRouter rows use reported
`usage.cost`, including its recorded caching. Local API fee is zero; hardware
and electricity are excluded. Sources: [Haiku 5.5 pricing](https://platform.claude.com/docs/en/models/haiku-5-5/overview),
[Sonnet 5.5 pricing](https://platform.claude.com/docs/en/models/sonnet-5-5/overview).

## Local hardware and candidate limits

Legacy has an **RTX 2080 Ti, 11 GiB VRAM; Core i7-10700K; 32 GiB RAM**.
The LFM3 and Granite candidates used official GGUF Q4_K_M artifacts, the existing
llama.cpp b12177 runtime, 16,384 context and four CPU threads. The stock Qwen
service remained resident; these are co-resident measurements, not a maximum
dedicated-GPU comparison. Cold requests are retained. Exact artifact hashes and
finite process settlement receipts stay with the private experiment custody.

LFM3's weight SHA-256 is
`3793e1e6e2266919d6e015287221b41100175a98eddaba92e111aed3b05c93c4`.
Granite's is
`662b0626cd58f443baea23559b469df6576a81d349649c59413b36a9fb32eb29`.
Their licenses remain Liquid's LFM license and Apache-2.0 respectively. Stock
4B serving identity and quantization should not be inferred from a neighboring
Ollama alias. Poor Granite results describe this exact serving setup, not every
variant or quantization.

The earlier candidate survey
is historical research, not a current recommendation. Prior 9B/27B trials used
different task cohorts and are not pooled with these results. NVIDIA Nemotron
3.5 Bolt E4B's reviewed Q4 candidate needs about 6.18 GB for weights; Granite 8B
about 5.35 GB, before KV/runtime memory. Those do not establish a fit beside the
roughly 5.6 GB resident stock load. Gated or third-party conversions need their
actual provenance, rights and serving checks. No claim is made that every
possible local model has been exhausted. These results identify the best
**tested** setup, not the best possible local model on all hardware.

## Optional Jev experiment

The completed [paired comparison](WINGMAN-JEV-PAIRED-COMPARISON.png) now covers
all eleven model/settings arms on the same eight single-tool tasks, with
[aggregate data](WINGMAN-JEV-PAIRED-COMPARISON.json) and a
[copyable table](WINGMAN-JEV-PAIRED-COMPARISON.csv). The eight measured Jev
selections/times/charges are reused across the separately measured main-model
runs; they are not eleven new batches of native Jev measurements. The earlier
two-arm result below remains part of that cohort.

Observed tradeoffs: every Claude variant became cheaper but slower. LFM3 kept
7/8 correct and its median decreased from 1.713 to 1.512 seconds (about 12%),
at an added $0.0000459/task. Granite gained one fully correct answer, from 5/8
to 6/8, while becoming slower and paid. Neither Luna variant gained accuracy
or speed, and both cost more. Qwen showed no demonstrated benefit; its
assisted 6/8 confirmed outcomes include one unresolved fixture. These are
small, sequential development measurements, not statistically established
improvements or evidence that every request should be narrowed to one tool.

The owner's target is Haiku adaptive-low without an extra Jev preselector,
then LFM3 with optional native Jev assistance only if a real integration
preserves the measured advantage; LFM3 must still run directly when Jev is
unavailable or exhausted. Native provider controls and optional native Jev assistance are not included in release 0.1.1; their integration remains unfinished.

Chart choice: aligned paired plots put each model on one row and distinguish
direct versus assisted time, accuracy and total cost without a crowded
22-point label cloud. A scatter plot is useful for overall tradeoffs; marker
area, rather than diameter, represents cost, and zero-fee local models need
an explicit symbol. Source guidance: [Plotly bubble charts](https://plotly.com/python/bubble-charts/)
and [Observable dot marks](https://observablehq.com/plot/marks/dot). Exact dollar
values and counts accompany every encoded mark; the paired cost axis is
explicitly nonlinear because the prices span several orders of magnitude.

An eight-task subset separately tested Jev selecting a single tool before
Haiku with thinking disabled, or stock Qwen. It did not test the winning
adaptive-low Haiku or LFM3. Jev selected the expected tool in all eight cases,
but selecting a tool does not guarantee correct arguments or reasoning.

Haiku remained 8/8 correct; median total completion increased from 1.204 to
1.517 seconds, including the measured Jev call. Qwen did not gain demonstrated
accuracy and its median increased from 0.742 to 0.972 seconds. This small
experiment reduced Haiku's estimated average cost from $0.000637 to $0.000174
per task, including Jev: about 73% less, with the latency tradeoff above. Qwen's
Jev-assisted forward-binding query has an uncovered fixture and is not counted
as a demonstrated accuracy loss. These results do not justify a second
production preselector for the owner's speed-first choice. They leave
Wingman's existing native Jev command, correction and decision functions
enabled. Other task-specific Jev uses remain hypotheses to measure.

The experiment used the recorded OpenRouter Jev 1.14 client before its
retirement. It remains valid evidence for those exact requests, not proof of
the native 3.2.14 integration. The frozen source patch and original revision
are retained privately for reproducibility; the old adapter is not restored.
Future product Jev work uses Wingman's public native facade.

## Applying the result

Use the fastest fully correct eligible arm, with local fallback and the existing
sticky exhaustion behavior. OpenRouter Luna was benchmark-only; personal
permanent OpenRouter use remains Jev-only. The fallback's weaker accuracy must
remain visible rather than represented as equivalent to Haiku.

Anthropic's [compatible endpoint](https://platform.claude.com/docs/en/cli-sdks-libraries/libraries/openai-sdk)
ignores `reasoning_effort` and rejects `thinking: adaptive`. The native Messages
winner cannot be claimed deployed by setting an ignored parameter. A correct
native transport must preserve signed thinking blocks through tool turns;
source-present protocol translation alone is insufficient. The current working
compatible route remains until a supported, tested integration preserves the
original tool lifecycle, cancellation, concurrency and fallback behavior.

No hosted account, model, raw transcript, private profile or credential is
included in the public module or this report.
