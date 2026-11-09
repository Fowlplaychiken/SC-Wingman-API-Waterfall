# Wingman API Waterfall

**Targets the Main conversation model**: reasoning, tool choices and answers. Support/background tasks, Jev, embeddings and speech have separate settings. [See the stock flow and optional Waterfall flow](docs/MODEL-ROLES.md).

![Wingman v3 skill](https://img.shields.io/badge/Wingman-v3_skill-41b9c7)
![Python 3.11 or newer](https://img.shields.io/badge/Python-3.11%2B-3776ab)
![GPL 3.0 or later](https://img.shields.io/badge/License-GPL--3.0--or--later-a6c76d)

**Use your preferred AI first. Keep another provider or a local model ready as fallback.**

```text
Your voice or text → Wingman → API Waterfall → your first available model
                                1. Preferred API
                                2. Another API (optional, repeat as needed)
                                3. Local / second-computer model (optional)
```

This optional Wingman v3 skill runs a bearer-protected endpoint on your own computer. It forwards conversations, images and tool calls through the providers **you configure, in your order**. Wingman continues to execute its tools. There are no Core patches, model downloads, subscriptions, bundled credentials or other-project services.

Version 0.2 source has focused routing and owned-listener checks; native 0.2 installation and activation are not established. Earlier routing and owned-listener checks pass. Actual Claude-compatible completion, model discovery and a controlled credit failure followed by real local inference have been checked. On 9 November 2026, the installed skill served an original Wingman Starbuck conversation and a read-only status-tool round trip through direct Claude Haiku. A separate controlled first-route credit failure selected a real second OpenAI API. These checks do not establish every provider/model combination or in-game behavior.

## What it does

- Accepts any number of ordered compatible provider/model entries; there is no fixed station-style count limit.
- Skips a provider after a definite credit-exhaustion response, including on later requests. It stays skipped until you reset it, update that credential, reconfigure or restart the skill.
- Provides three fallback modes: `credits_only` (recommended for new profiles), `availability`, and `manual`. Existing profiles without this setting retain `availability`; a route’s explicit `fallback_on` list overrides the preset. Rate limits and service unavailability are temporary rather than automatically called spent credits.
- Reports provider switches and recoveries with a visible warning when capability or cost may change. Redacted failure facts go to Wingman logs; no prompt, key or raw provider error body is needed. `notify_switches` controls visible transition notices.
- Offers model IDs from each configured provider so you can copy an exact ID. You choose the model; the module does not benchmark or choose it for you.
- Can end with a local OpenAI-compatible server on your computer or another Windows/Linux machine. The module runs alongside Wingman; remote inference remains the server's job.
- Owns its loopback listener and closes it on skill unload. It creates no scheduled task or permanent background daemon.
- Supports native Anthropic Messages with explicit thinking/effort settings, alongside existing OpenAI-compatible routes. Native Messages currently accepts nonstreaming conversation requests; compatible routes retain streaming.
- Optionally asks Wingman's existing decision model to narrow the first tool choice on a text turn. This is off by default: it can increase latency, cost or mistakes. An uncertain or unavailable decision keeps the full tool list; exhausted Jev credits disable assistance until reset. A provider refusal stays a refusal.

## Install and configure

[Download the skill ZIP](https://github.com/Fowlplaychiken/SC-Wingman-API-Waterfall/releases/latest) · [Source](https://github.com/Fowlplaychiken/SC-Wingman-API-Waterfall)

1. Extract the release ZIP so `provider_fallback/default_config.yaml` is directly inside Wingman's `custom_skills` directory. On Windows this is normally `%APPDATA%\ShipBit\WingmanAI\custom_skills`. Avoid an extra nested ZIP folder.
2. Fully restart Wingman after installing a new skill, so its startup compatibility catalogue sees it.
3. Add a strong random **local bridge bearer** to Wingman's Secrets as `waterfall_bridge`. Add your provider keys under separate secret names. Never put key values in the route JSON.
4. Open **Provider fallback** settings for your Wingman. Set its port (default `4141`), bridge secret name (`waterfall_bridge`) and ordered routes JSON below, then enable the skill. Use a different free port if you run a second instance. Apply settings and re-enable the skill after route/port changes.
5. In that Wingman's conversation provider, choose the existing **local / OpenAI-compatible** provider. Set endpoint `http://127.0.0.1:4141/v1` and model `waterfall`. Wingman uses its `local_llm` secret for this connection: set that secret to the same bridge bearer. This name describes the interface; the selected upstream model may be cloud-hosted.
6. Ask for a short text reply, then a read-only skill lookup. Use `provider_fallback_status` to see which route was selected. Keep a copy of your original endpoint/model so you can switch back.

Wingman's `local_llm` secret is shared. If other Wingmen use a gateway requiring a different key, keep that limitation in mind before replacing it. Keyless local servers commonly ignore the bearer; authenticated gateways may not. The module does not rewrite other Wingmen.

Example routes (replace model IDs and secret **names**, not key values):

Configure `routes_json` as an ordered JSON array. Each object has `route_id`, full `endpoint`, `model`, optional Wingman `secret_name`, optional `request_overrides`, and optional `final_local`. `wire_protocol` defaults to `openai_chat`; use `anthropic_messages` for Anthropic's native `/v1/messages`. Copy exact available model IDs from your provider; availability, capabilities and pricing change. Compatible examples include `https://api.openai.com/v1/chat/completions` and `https://openrouter.ai/api/v1/chat/completions`. A local or remote compatible server can be another route. Model/tool/image and parameter support differ by provider; a configured endpoint is not proof of compatibility.

```json
[
  {"route_id":"preferred","endpoint":"https://api.anthropic.com/v1/messages","wire_protocol":"anthropic_messages","model":"YOUR_MODEL_ID","secret_name":"YOUR_CLAUDE_SECRET_NAME","request_overrides":{"thinking":{"type":"adaptive","display":"omitted"},"output_config":{"effort":"low"}}},
  {"route_id":"backup","endpoint":"https://api.openai.com/v1/chat/completions","model":"YOUR_MODEL_ID","secret_name":"YOUR_OPENAI_SECRET_NAME"},
  {"route_id":"local","endpoint":"http://127.0.0.1:11434/v1/chat/completions","model":"YOUR_LOCAL_MODEL","final_local":true}
]
```

Delete unwanted entries; the local entry is optional and, when marked `final_local`, must be last. `request_overrides` can hold supported provider parameters, but cannot replace conversation messages, tools, model or streaming mode. Do not assume another provider's reasoning settings are honored: Anthropic's compatibility endpoint, for example, does not honor OpenAI `reasoning_effort`.

### Optional decision assistance

Set `"jev_assist": true` on an individual route only if its tasks benefit. Configure Wingman's own **Decision Model** provider/key first; this module uses its public `system_one` interface, not a second Jev provider. The optional `jev_min_confidence` defaults to `0.8`. Below that threshold, disabled decisions, nontext/image requests and tool-result follow-ups retain the original request. A fresh user turn may select again. An exhausted Jev account falls back to the same main model directly, with no repeated credit probe until reset. Missing public support also leaves normal inference working.

Selecting one first tool does not validate its arguments or answer. Subsequent tool-result turns receive the original full catalogue, so a multi-step request can still use other tools. A 24-task paired development comparison is available below. Assistance is task and model dependent; leave it off for an unmeasured setup.

### Native Messages limits

Native routes preserve opaque signed thinking in assistant history without exposing reasoning as spoken text. Keep that history unchanged for a tool follow-up. Native Messages currently rejects `stream: true` before sending; use Wingman's nonstreaming main conversation path or a compatible route. Base64 JPEG/PNG/GIF/WebP image blocks are supported; remote image URLs and unsupported content fail explicitly. Thinking/effort controls are forwarded only from explicit route overrides, not guessed from compatible defaults. Follow your selected model's supported settings.

Signed blocks stay with their original endpoint, secret reference and model. A different fallback receives the visible conversation and tool results without those opaque blocks. If you replace a key with one belonging to a different account under the same secret name, start a new conversation; provider signatures can be account-specific.

### Useful requests

- “Show provider fallback status.” → `provider_fallback_status`
- “List models for route preferred.” → `provider_fallback_models(route_id="preferred")`
- “Reset exhausted provider routes.” → `provider_fallback_reset`

These tools are also available directly in Wingman's tools interface. A model listing is not a benchmark or a guarantee of tool/image compatibility. Some providers paginate their model listings; the current tool returns the first advertised page only.

## Fallback choices and diagnostics

Choose `credits_only` to fall back on definite exhaustion, `availability` to include configured authentication/model and temporary availability failures, or `manual` to disable automatic fallback. The route-specific `fallback_on` list can specify `credit_exhausted`, `authentication_or_model_unavailable`, `temporary_unavailable`, and `connection_unavailable`, including an empty list. This list overrides the preset for that route. Policy refusals and uncertain or partial responses remain terminal. A gateway may independently select or retry providers for the same model; Waterfall does not control or observe those internal hops.

`provider_fallback_status` reports route states and the last selected route. Failure log entries can include a generated request ID, bounded route/model identifiers, a fixed reason and safe HTTP status, rather than secrets or message content. Successful responses expose the request ID in `x-wingman-waterfall-request-id`; routing failures carry a generated ID where reported. Switch notices can be disabled with `notify_switches`; failure diagnostics remain enabled. Notification delivery failure is reported in status when observable. These are local diagnostic records, not a billing ledger or a claim about hidden gateway attempts.

### Development comparison

![Paired Jev comparison](benchmarks/WINGMAN-JEV-PAIRED-COMPARISON.png)

[Benchmark report](benchmarks/REPORT.md) · [CSV data](benchmarks/WINGMAN-JEV-PAIRED-COMPARISON.csv)

The [24-task paired comparison JSON](benchmarks/WINGMAN-JEV-PAIRED-COMPARISON.json) and [chart](benchmarks/WINGMAN-JEV-PAIRED-COMPARISON.png) contain 480 attempted case-runs across ten settings, with 96 prior direct controls reused. The references and grading are AI-reviewed development fixtures, not a human-reviewed held-out benchmark. Unknown outcomes are shown separately, not counted as wrong or silently removed from the total of 24.

| Selected configuration | Fully correct / 24 | Unknown | All-attempt median | Estimated API cost / 1,000 tasks |
| --- | ---: | ---: | ---: | ---: |
| Haiku compatible, direct | 22 | 2 | 2.85 s | $1.048* |
| Qwen 3.5 4B, with Jev | 11 | 5 | 2.02 s | $0.048 |
| Qwen 3.5 4B, direct | 9 | 4 | 1.48 s | $0 API fee |

*Hypothetical uncached token list-price estimate, not a billed amount or verified upper bound; cache detail was unavailable. Jev cost is included where used. Local API fees exclude hardware and electricity. Medians include all attempts, including partial and unsuccessful outcomes. Local measurements used an RTX 2080 Ti 11 GiB, Intel i7-10700K, 32 GiB RAM, Q4 models and llama.cpp b12177 with a 16,384-token context. These limited results do not establish universal accuracy, native end-to-end latency or a causal Jev benefit.

## Failure behavior

The following table describes eligible outcomes **when allowed by the selected preset or route override**. In `credits_only` and `manual`, noneligible failures return rather than automatically choosing another route.

| Response | Behavior |
| --- | --- |
| Definite credit exhaustion / HTTP 402 | Skip that account for later requests until reset or relevant credential change |
| HTTP 429 or 500/502/503; Claude 529 overload | Try the next route; temporary cooldown, not sticky credit exhaustion |
| Missing key, HTTP 401 or 404 | Skip the affected route until reset/configuration or credential repair |
| Connection cannot be established | Try the next route; temporary cooldown |
| Content/policy refusal or HTTP 403 | Return the refusal; do not route around it |
| HTTP 504, ambiguous timeout, broken response or partial stream | Stop this request; do not silently replay it through another model |

Only eligible failures before successful response headers can fall through. If a stream fails after starting, the module cannot truthfully know whether the provider finished processing; it does not duplicate that work elsewhere. If every route is unavailable, Wingman gets a redacted error rather than an invented answer. Exhaustion/cooldown state lives in memory; it is not a durable billing ledger.

Version 0.1.1 also recognizes Claude's specific HTTP 400 low-credit response;
other HTTP 400 errors remain terminal. See [Claude's error reference](https://platform.claude.com/docs/en/api/errors).

Unrelated secret saves do not clear exhausted routes. Updating a configured provider's key clears the relevant route state; changing the bridge bearer restarts its owned listener. Provider billing still applies to API calls. There is no promise of free inference or universal protocol support.

## For Wingman maintainers

The reusable routing core is `routing.py`; the optional HTTP adapter is `server.py`; `main.py` binds only public v3 Skill lifecycle and secret interfaces. The installed entry uses a small dependencies wrapper for Wingman's loader. A Core integration can call the router behind the existing LLM interface and omit the loopback adapter, retaining Core's credential and tool-execution ownership.

The owned listener lifecycle adapts the project's existing speech bridge. Public integration seams are `Skill.prepare`, `unload`, `secret_changed` and `wingman.secrets.retrieve`. The configured skill auto-activates at startup so it does not need an LLM request to start its own inference endpoint. All runtime dependencies already belong to Wingman; see `NOTICE.md` and `LICENSE`.

For development in the standalone repository, use Python 3.11 or newer with the
dependencies in `pyproject.toml`, run `python -m pytest`, then
`python build.py --out ../waterfall-release` with a fresh output directory.
The builder verifies ZIP bytes and writes a per-file SHA-256 manifest. Wingman
users need the ZIP, not a separate pip installation or this development setup.
