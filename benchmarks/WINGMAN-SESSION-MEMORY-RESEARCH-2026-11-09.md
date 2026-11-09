# Session-end and context-pressure compaction: source review

**Recommendation for evaluation, not an implemented policy:** preserve full history while it fits; optionally prepare a durable session-end checkpoint, and retain pressure-triggered background condensation for long sessions. Session-end-only processing can lose recent work on crashes and cannot manage a single session that outgrows the context. A generated summary can omit or misattribute facts, so retain recoverable source history and version checkpoints rather than irreversibly overwriting the only record. Separate durable memory extraction from prompt-context shortening.

## Published API guidance

- [OpenAI Responses compaction](https://developers.openai.com/api/docs/guides/compaction) supports server-side compaction when a configured token threshold is crossed and a standalone compact endpoint. It carries a compacted item into subsequent requests; cost, latency and retention are tradeoffs. These are API capabilities, not evidence of ChatGPT or Codex private session internals.
- [Claude context editing](https://platform.claude.com/docs/en/build-with-claude/context-editing) can clear older tool content as context grows; [Claude compaction](https://platform.claude.com/docs/en/build-with-claude/compaction) uses a summary to replace earlier context. These are Messages API mechanisms and their model/feature constraints must be checked before use; they do not establish Claude consumer-product internals. Clearing tool output and summarizing are different operations.
- Experimental [Min and Chen, arXiv:2610.20927](https://arxiv.org/abs/2610.20927) examines unsupported claims introduced by summaries and a source-judging method on AppWorld and OfficeBench. The paper's reported results do not establish benefit or calibrated behavior for Wingman or Star Citizen; no judge is proposed as a prerequisite here.

## Existing Wingman source

Pinned Core `f0364106a9ea13eb5aee45f73cd9af86c79cd213`, `services/context_budget.py::ContextBudget`, sets the history limit to `min(64,000, main_window / 2)` tokens, retaining a quarter of that limit and at least four turns. `services/conversation_condenser.py::maybe_condense` first checks the feature flag, pending tool calls, and pressure from the larger of last prompt tokens and estimated history. Once over the limit it clears old tool responses; it schedules background summarization only if still above 75% of the limit and enough older history exists. The limit depends on Main's window, not Support's; Support chunks its input. This is a source finding, not evidence that a particular live session crossed the threshold.

`wingmen/wingman.py::_memory_after_turn`, `_memory_idle` and `_memory_checkpoint` already schedule independent memory work: after 25 turns or 20 minutes following a turn, and final checkpoint after 30 minutes idle, reset or unload. These are different from context condensation. Session-end work can be missed on abnormal termination; actual crash recovery and atomic preservation need verification before any policy change.

## Practical comparison

The current eight-case ablation uses short histories and can compare immediate retention and measured processing/cost for those fixtures. It cannot show whether compaction is unnecessary for long sessions or determine an optimal trigger. A later change should reuse the stock pressure logic and checkpoint facilities, measure retention and foreground cost separately, preserve pending tool-call boundaries, and report failures without losing the source. No settings, models or memory policy were changed for this note.
