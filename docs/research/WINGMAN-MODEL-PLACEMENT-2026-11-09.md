# Main, Support and hosted baselines — 9 November 2026

**Prioritize Main quality for conversation and tool reasoning. Keep Support
accurate enough to preserve memory and context, then optimize its cost and
latency separately.** This is a source-based recommendation, not a measured
claim that our setup outperforms Wingman's hosted services. No account plan or
runtime setting was changed for this analysis.

| Role | Consequence of weak performance | Placement to evaluate |
|---|---|---|
| Main | Wrong reasoning, tool selection, arguments or answers; direct response delay. | The tested Haiku compatible defaults, with an explicitly configured local fallback. |
| Support | Lost details in summaries, incorrect extracted memories, or incomplete compressed tool results. Some skill calls wait for this work. | Stock hosted Support as a baseline; compare local/remote Support separately before choosing. |
| Jev | Incorrect bounded decisions in enabled paths. It does not replace Main or Support. | Keep native decision settings separate; optional assistance must demonstrate benefit on the selected task/model. |
| Embeddings | Retrieval quality or incompatible stored vectors. | Retain the established embedding model; changes require compatible indexing/migration. |
| Speech | Recognition errors and playback delay are independent of Main intelligence. | Keep separately qualified local/remote speech; measure complete voice latency separately. |

## Why Support still matters

Core schedules conversation condensation in the background. If Support is
unavailable, it can retain older messages until Main's context limit; that is
not a successful summary. A bad summary can lose information even when Main is
strong. Some skills explicitly await Support summarization and compression, so
Support can also affect foreground latency. Failed compression may retain a
truncated head of the input. These effects depend on the particular path, not
every conversation turn. See official
[conversation condensation](https://github.com/ShipBit/wingman-ai/blob/f0364106a9ea13eb5aee45f73cd9af86c79cd213/services/conversation_condenser.py),
[skill support calls](https://github.com/ShipBit/wingman-ai/blob/f0364106a9ea13eb5aee45f73cd9af86c79cd213/services/skill_local_ai.py)
and [tool-result compression](https://github.com/ShipBit/wingman-ai/blob/f0364106a9ea13eb5aee45f73cd9af86c79cd213/services/tool_response_cache.py).

An API Main plus local/remote Support can reduce paid usage, but only if Support
preserves the required facts. Hosted Support can instead spare GPU memory and
avoid contention with a local Main fallback. The best placement depends on
measured workload, privacy preferences, available hardware and allowance.

## What the Free plan and Vercel establish

Wingman's current [pricing page](https://www.wingman-ai.com/pricing) describes a
limited monthly hosted allowance shared by the hosted AI models, voices and
images. BYOK and local use do not consume that allowance. Simon's statement
that support-only usage can fit the Free plan is a usage recommendation, not a
promise of unlimited Main reasoning or a particular model.

The [Support client](https://github.com/ShipBit/wingman-ai/blob/f0364106a9ea13eb5aee45f73cd9af86c79cd213/providers/wingman_support.py)
sends requests to Wingman's backend, which resolves the model from the plan's
support lane and can substitute its current default. Source comments mention
measurements on Gemini Flash Lite and Qwen Flash; they do not prove the current
Free-plan model. The exact available and served models remain unverified here.

[Vercel AI Gateway](https://vercel.com/docs/ai-gateway/models-and-providers/provider-options)
routes model requests; it is not itself a reasoning model. Same-model provider
failover and explicitly configured model fallback are distinct. Vercel's
capabilities alone do not establish Wingman's actual backend configuration.

## Required baseline comparison

The existing 24-task Main comparison supports Haiku over the tested local
options. It did not include Wingman's stock hosted Main or a separate Support
cohort. It cannot establish parity with those services.

Compare stock hosted Main using the same fixed 24 requests, tools and scoring;
record available/served model identity, quota consumption, accuracy, unknowns
and end-to-end latency. Evaluate Support on memory precision/recall, summary
fact retention and contradiction, compression answerability, latency and quota
use, including concurrent local fallback load where relevant. Do not use Main
scores to rank Support or change a user's plan/configuration to collect a
baseline. Account-specific access and model identity must be observed through
the supported existing service rather than inferred from a gateway name.

Until those results exist, Haiku Main with a local fallback is a measured
candidate, and hosted Support is a sensible baseline to evaluate; neither is
claimed globally optimal. Waterfall's native 0.2 activation remains a separate
unfinished delivery step. See the [role map](../MODEL-ROLES.md).
