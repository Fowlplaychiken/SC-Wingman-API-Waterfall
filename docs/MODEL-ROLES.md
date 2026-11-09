# Wingman model roles

Wingman Core is the coordinating software, not a fourth AI model. It calls the
configured Main, Support, decision and embedding interfaces when needed.

![Simplified stock Wingman conversation flow](diagrams/WINGMAN-STOCK-FLOW.png)

This stock-only view follows Wingman 3.2.14 at the source revision below.
Support returns facts/summaries to Core, which includes relevant context in
Main requests. Jev returns bounded decisions to Core's routing/action code;
it does not rewrite the Support model. A preselected command can still require
Main to generate its acknowledgement. The tools/Main arrows represent Core's
interfaces, not direct model execution of operations. Not every turn invokes
every role. Support's stored context is conditional/background, not a required
round-trip on every request. The decision provider/model is configurable;
Jev is not the only allowed decision model. [Editable flow](diagrams/WINGMAN-STOCK-FLOW.mmd).

See [model placement and hosted baselines](research/WINGMAN-MODEL-PLACEMENT-2026-11-09.md)
for role-specific recommendations and the remaining comparison gap. The vision
box describes an image-capable Main model, not an additional stock model.

**Waterfall can route Main and, optionally, Core's existing Support role.** Main
handles conversation, reasoning, tool choices and tool arguments. The optional
Support chain serves only requests Core already schedules; Core retains its
scheduler and memory policy. Support does not change Main's provider, and neither
role replaces Jev.

![Wingman model roles](diagrams/WINGMAN-MODEL-ROLES.png)

The stock map is unchanged. This older extension map illustrates Main-only Waterfall; it does not show the optional Support chain:

![Main-only Waterfall extension; Support remains separate](diagrams/WINGMAN-MODEL-ROLES-EXTENSIONS.png)

| Role | Job | Waterfall scope |
|---|---|---|
| Main conversation model | Understand requests, reason, choose tools, interpret results and answer. Skills can explicitly make main-model side calls too. | The configured Main provider can point at Waterfall; its ordered Main routes select the preferred model or eligible backup. Optional Jev assistance applies to Main routes only. |
| Support model | Background memory extraction, conversation summaries and inexpensive, explicitly requested skill work. | Optional Waterfall Support routes use a separate local Support endpoint and independent provider/model chain. Core still schedules Support and owns memory policy. |
| Decision model / Jev | Fast, bounded choices through Wingman's decision interface. Enabled uses depend on the task/settings. | Wingman's own Jev route remains separate. Optional per-route assistance reuses that interface before a main-model tool choice; it does not replace the main model. |
| Embedding model | Turn text into vectors for memory/retrieval. | Separate from the conversation route and generative reasoning. |
| Speech recognition / synthesis | Convert microphone audio to text and response text to audio. | Separate STT/TTS settings and services. |
| Image-capable main model | Read screenshots supplied by skills. | These main-model calls follow the configured main provider. |

Skills generally use these shared roles rather than each having an independent
AI agent. A skill chooses an interface for a particular job. In the reviewed
Core, Proactive Comments and SC Game Events have main-model calls and configured
local/support branches; UEX and Spotify also use typed System One decisions.
Those examples do not imply every request passes through every model.

## What Waterfall changes

Waterfall can be selected independently for Wingman Main and Support. Its Main routes affect main-model calls, including explicit skill main-model side calls; the optional Support chain handles only Support requests Core has already scheduled. Core retains scheduling and memory policy. Main and Support have separate routes and provider/model credential references. Optional Jev assistance reuses the configured public decision interface on Main routes only.

The accompanying 24-task comparison is a main-model/tool benchmark. It does not rank Support memory extraction or background summarization. No benchmark rerun or Support setting change was needed for this role clarification.

## Source trace

Reviewed official Core commit: `f0364106a9ea13eb5aee45f73cd9af86c79cd213`.
The current main ref was rechecked on 2026-11-09. Simon's explanation prompted
this audit; the source paths independently establish the distinctions.

- [`ProviderFactory.create_llm`](https://github.com/ShipBit/wingman-ai/blob/f0364106a9ea13eb5aee45f73cd9af86c79cd213/services/provider_factory.py) selects the configured conversation provider.
- [`Wingman.actual_llm_call`](https://github.com/ShipBit/wingman-ai/blob/f0364106a9ea13eb5aee45f73cd9af86c79cd213/wingmen/wingman.py#L849) calls that main provider.
- [`SkillAi`](https://github.com/ShipBit/wingman-ai/blob/f0364106a9ea13eb5aee45f73cd9af86c79cd213/wingmen/facade.py#L315) exposes main-model generation, including image input.
- [`SkillLocalAiView`](https://github.com/ShipBit/wingman-ai/blob/f0364106a9ea13eb5aee45f73cd9af86c79cd213/wingmen/facade.py#L514) exposes the separate support, summary and embedding interfaces.
- [`SkillSystemOne`](https://github.com/ShipBit/wingman-ai/blob/f0364106a9ea13eb5aee45f73cd9af86c79cd213/wingmen/facade.py#L1871) exposes typed decisions.

Engineering modules are deliberately omitted from these shareable diagrams.
The stock map remains unchanged. The older extension map shows Waterfall on Main
only; it does not depict the optional Support chain. Jev remains separate.

The [stock diagram source](diagrams/WINGMAN-MODEL-ROLES.mmd) and
[extension diagram source](diagrams/WINGMAN-MODEL-ROLES-EXTENSIONS.mmd) are role maps, not a
claim that all skills, providers or runtime combinations are independently tested.
