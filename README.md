# Wingman API Waterfall

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

The routing and owned-listener checks pass. Actual Claude-compatible completion, model discovery and a controlled credit failure followed by real local inference have been checked. On 9 November 2026, the installed skill served an original Wingman Starbuck conversation and a read-only status-tool round trip through direct Claude Haiku. A separate controlled first-route credit failure selected a real second OpenAI API. These checks do not establish every provider/model combination or in-game behavior.

## What it does

- Accepts any number of ordered compatible provider/model entries; there is no fixed station-style count limit.
- Skips a provider after a definite credit-exhaustion response, including on later requests. It stays skipped until you reset it, update that credential, reconfigure or restart the skill.
- Treats rate limiting and service unavailability as temporary cooldowns, rather than calling them spent credits.
- Offers model IDs from each configured provider so you can copy an exact ID. You choose the model; the module does not benchmark or choose it for you.
- Can end with a local OpenAI-compatible server on your computer or another Windows/Linux machine. The module runs alongside Wingman; remote inference remains the server's job.
- Owns its loopback listener and closes it on skill unload. It creates no scheduled task or permanent background daemon.

## Install and configure

[Download the skill ZIP](https://github.com/Fowlplaychiken/SC-Wingman-API-Waterfall/releases/latest/download/provider_fallback-0.1.1.zip) · [Source](https://github.com/Fowlplaychiken/SC-Wingman-API-Waterfall)

1. Extract the release ZIP so `provider_fallback/default_config.yaml` is directly inside Wingman's `custom_skills` directory. On Windows this is normally `%APPDATA%\ShipBit\WingmanAI\custom_skills`. Avoid an extra nested ZIP folder.
2. Fully restart Wingman after installing a new skill, so its startup compatibility catalogue sees it.
3. Add a strong random **local bridge bearer** to Wingman's Secrets as `waterfall_bridge`. Add your provider keys under separate secret names. Never put key values in the route JSON.
4. Open **Provider fallback** settings for your Wingman. Set its port (default `4141`), bridge secret name (`waterfall_bridge`) and ordered routes JSON below, then enable the skill. Use a different free port if you run a second instance. Apply settings and re-enable the skill after route/port changes.
5. In that Wingman's conversation provider, choose the existing **local / OpenAI-compatible** provider. Set endpoint `http://127.0.0.1:4141/v1` and model `waterfall`. Wingman uses its `local_llm` secret for this connection: set that secret to the same bridge bearer. This name describes the interface; the selected upstream model may be cloud-hosted.
6. Ask for a short text reply, then a read-only skill lookup. Use `provider_fallback_status` to see which route was selected. Keep a copy of your original endpoint/model so you can switch back.

Wingman's `local_llm` secret is shared. If other Wingmen use a gateway requiring a different key, keep that limitation in mind before replacing it. Keyless local servers commonly ignore the bearer; authenticated gateways may not. The module does not rewrite other Wingmen.

Example routes (replace model IDs and secret **names**, not key values):

Configure `routes_json` as an ordered JSON array. Each object has `route_id`, full OpenAI-compatible chat-completions `endpoint`, `model`, optional Wingman `secret_name`, optional `request_overrides`, and optional `final_local`. Copy exact available model IDs from your provider; availability, capabilities and pricing change. Example endpoints include `https://api.openai.com/v1/chat/completions`, `https://api.anthropic.com/v1/chat/completions` (Anthropic's compatibility API, not the native Messages API), and `https://openrouter.ai/api/v1/chat/completions`. A compatible remote or local server can be another route. Model/tool/image and parameter support differ by provider; a configured endpoint is not proof of compatibility.

```json
[
  {"route_id":"preferred","endpoint":"https://api.anthropic.com/v1/chat/completions","model":"YOUR_MODEL_ID","secret_name":"YOUR_CLAUDE_SECRET_NAME"},
  {"route_id":"backup","endpoint":"https://api.openai.com/v1/chat/completions","model":"YOUR_MODEL_ID","secret_name":"YOUR_OPENAI_SECRET_NAME"},
  {"route_id":"local","endpoint":"http://127.0.0.1:11434/v1/chat/completions","model":"YOUR_LOCAL_MODEL","final_local":true}
]
```

Delete unwanted entries; the local entry is optional and, when marked `final_local`, must be last. `request_overrides` can hold supported provider parameters, but cannot replace conversation messages, tools, model or streaming mode. Do not assume another provider's reasoning settings are honored: Anthropic's compatibility endpoint, for example, does not honor OpenAI `reasoning_effort`.

### Useful requests

- “Show provider fallback status.” → `provider_fallback_status`
- “List models for route preferred.” → `provider_fallback_models(route_id="preferred")`
- “Reset exhausted provider routes.” → `provider_fallback_reset`

These tools are also available directly in Wingman's tools interface. A model listing is not a benchmark or a guarantee of tool/image compatibility. Some providers paginate their model listings; the current tool returns the first advertised page only.

## Failure behavior

| Response | Behavior |
| --- | --- |
| Definite credit exhaustion / HTTP 402 | Skip that account for later requests until reset or relevant credential change |
| HTTP 429 or 500/502/503/504; Claude 529 overload | Try the next route; temporary cooldown, not sticky credit exhaustion |
| Missing key, HTTP 401 or 404 | Skip the affected route until reset/configuration or credential repair |
| Connection cannot be established | Try the next route; temporary cooldown |
| Content/policy refusal or HTTP 403 | Return the refusal; do not route around it |
| Ambiguous timeout, broken response or partial stream | Stop this request; do not silently replay it through another model |

Only rejected requests before successful response headers can fall through. If a stream fails after starting, the module cannot truthfully know whether the provider finished processing; it does not duplicate that work elsewhere. If every route is unavailable, Wingman gets a redacted error rather than an invented answer. Exhaustion/cooldown state lives in memory; it is not a durable billing ledger.

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
