# Configuration reference

For quick setup, see [README.md](README.md).

## Role settings

| Property | Default | Meaning |
| --- | --- | --- |
| `main_enabled` | `false` | Enables the Main Waterfall listener and route chain. |
| `listen_port` | `4141` | Main loopback port. |
| `bridge_secret_name` | empty | Existing Wingman secret reference for the Main bridge bearer. |
| `routes_json` | `[]` | Ordered Main routes. |
| `fallback_mode` | `credits_only` | Main fallback policy. Legacy profiles omitting it keep `availability`. |
| `notify_switches` | `true` | Shows role/provider switch and recovery notices. |
| `support_enabled` | `false` | Enables the independent Support listener and route chain. |
| `support_listen_port` | `4142` | Support loopback port, separate from Main. |
| `support_routes_json` | `[]` | Ordered Support routes. |
| `support_fallback_mode` | `credits_only` | Independent Support fallback policy. |

A new install has no active Main or Support route. Configure an enabled role
with a nonempty, valid route list. Invalid enabled-role settings produce a
visible configuration error; they do not silently select a provider. Existing
Main profiles with routes and a bridge secret remain opted in if their old
configuration has no `main_enabled` field. During an upgrade, Core may merge the
new `main_enabled: false` default into an existing profile. Check **Enable Main
fallback** and re-enable it if you want to keep Main active.

## Main bridge credentials

The Main listener uses loopback port 4141. In Wingman's Provider fallback
settings, set `bridge_secret_name` to `local_llm`, then store a strong random
*local bridge password* in Wingman Secrets under that name. In Core's
conversation provider, choose the local/OpenAI-compatible provider with
`http://127.0.0.1:4141/v1` and model `waterfall`, using that same bridge
password. It is a local password, not an upstream provider API key. Wingman's
`local_llm` secret is shared across Wingmen; check other profiles before
changing it.

## Support listener and Core embedding setup

The Support listener is an infer-only OpenAI-compatible endpoint at
`http://127.0.0.1:4142/v1`. Its fixed local auth value is `not-needed`, which
Core supplies. Do not reuse the Main bearer or enter a Support key. It accepts
only local model-list and text completion requests; tools, streaming, browser
origins and LAN callers are rejected. Status/reset/admin paths are not available
through this listener.

In Core's Support Model settings, choose **Remote**. Set **Support Model host**
to `http://127.0.0.1` and **Port** to `4142`. Core adds the API path, so do not
append `/v1` to the host. Core supplies the `local-model` alias returned by this
listener; it is not a Support Model field to type into Core.

Remote Support mode also uses Core's separate remote embedding settings. Keep
the existing compatible embedding host, port and model. Do not point embeddings
at the Support listener. If no compatible embedding endpoint is reachable, leave
Waterfall Support disabled. This Core coupling does not affect Main; Waterfall
does not supply an embedding model.

## Route format

Each route requires `route_id`, a full `endpoint` URL and `model`. Optional
fields include `secret_name`, `request_overrides`, `final_local`,
`wire_protocol`, `jev_assist`, `jev_min_confidence`, `omit_request_fields` and
`fallback_on`. Keep credential references in route JSON and credential values
only in Wingman Secrets. Generic defaults contain no personal endpoint, model or
secret reference.

```json
[
  {"route_id":"preferred","endpoint":"https://api.anthropic.com/v1/messages","wire_protocol":"anthropic_messages","model":"YOUR_MODEL_ID","secret_name":"YOUR_SECRET_REFERENCE"},
  {"route_id":"backup","endpoint":"https://api.openai.com/v1/chat/completions","model":"YOUR_MODEL_ID","secret_name":"YOUR_SECRET_REFERENCE"},
  {"route_id":"local","endpoint":"http://127.0.0.1:11434/v1/chat/completions","model":"YOUR_LOCAL_MODEL","final_local":true}
]
```

Replace each placeholder with a supported endpoint, exact model ID and existing
secret reference. Local or remote OpenAI-compatible servers may be used. The
final local route is optional and, when marked `final_local`, must be last. A
configured endpoint does not prove provider compatibility or supported
image/tool/parameter behavior.

For stock Wingman Support completions, use `wire_protocol: "wingman_support"`
and a provider-documented full endpoint ending in `/support/completions`. Other
routes must use a supported protocol such as `openai_chat` or
`anthropic_messages`. `omit_request_fields` can remove selected optional
inherited sampling fields unsupported by a route. It cannot remove required
messages, the model or the completion contract. Unsupported protocols and
malformed route settings fail explicitly.

`request_overrides` cannot replace messages, tools, tool choice, model or
streaming mode. Anthropic's OpenAI-compatible endpoint, for example, does not
honor OpenAI `reasoning_effort`.

## Fallback and billing

Enabling Support is consent for its ordered routes to make provider calls; API
charges may apply at any attempted route. Waterfall does not infer that a route
is free from a provider name, key reference or missing key. Notices identify
role, route/model and reason when available, and warn that cost or capability
may change. Waterfall does not read or report provider usage or billing totals;
treat route costs as unknown. Errors stay visible; failure logs use
bounded request IDs and redacted reasons, never raw prompts, keys or provider
error bodies.

New Main and Support routes default to `credits_only`. `availability` and
`manual` are optional policies. A route's explicit `fallback_on` list overrides
the role's preset. Legacy Main profiles without a preset keep
`availability`. Gateway retries are separate and cannot be observed by
Waterfall.

| Failure | Behavior when eligible under the selected policy |
| --- | --- |
| Definite credit exhaustion / HTTP 402 | Mark the account exhausted for configured routes until reset, credential update or restart. |
| HTTP 429, eligible 500/502/503, Claude 529 | Temporary cooldown, not sticky credit exhaustion. |
| Missing key, HTTP 401 or 404 | Skip the affected route pending reset, configuration or credential repair. |
| Connection failure | Temporary cooldown. |
| Policy refusal / HTTP 403 | Return the refusal; never route around it. |
| Ambiguous timeout / HTTP 504 / partial response or stream | Stop; do not replay through another model. |

Only eligible failures before successful response headers can fall through.
An interrupted stream is never replayed. Exhaustion/cooldown state is in memory,
not a billing ledger. Different routes may share a provider account and credit
pool; separate keys or model names do not prove independent quota. Report
account-level exhaustion only when the provider makes that scope clear.

After restoring credits, reset Main with `provider_fallback_reset()` or Support
with `provider_fallback_reset(role="support")`. The next request tries the
preferred route and announces recovery when successful. Replacing the relevant
key or restarting the module also clears exhaustion. Reset does not add credits;
failure log entries remain available. The Support listener itself exposes no
status/reset endpoint. `provider_fallback_status()` reads Main route status;
`provider_fallback_models(route_id="...")` lists advertised route models.

## Optional Jev assistance on Main

`jev_assist` is per Main route and defaults to `false`. It uses Wingman's
existing Decision Model/System One interface to narrow the first tool choice on
a text turn. It is unavailable to Support. Configure Wingman's Decision Model
first; `jev_min_confidence` defaults to `0.8`. Uncertain decisions keep the full
tool list; tool-result turns get the full catalogue. A selected tool is not
argument validation or proof of successful execution. Jev exhaustion falls
back to the same Main model until reset; a provider refusal remains a refusal.

## Anthropic Messages routes

Native `anthropic_messages` routes accept nonstreaming requests. Base64
JPEG/PNG/GIF/WebP input is supported; remote image URLs and unsupported content
fail explicitly. Thinking and effort settings are forwarded only from explicit
route overrides. Opaque signed thinking stays with its original endpoint,
secret reference and model; a fallback receives visible conversation and tool
results without those blocks. If a secret is changed to a different provider
account, start a new conversation because signatures may be account-specific.
