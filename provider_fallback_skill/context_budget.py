"""Bound request input and replies for an OpenAI-compatible context window.

Input estimates describe the serialized messages and tool schemas sent by
Waterfall. A provider's tokenizer may count them differently; callers should
prefer Wingman's token counter when available. The UTF-8-byte fallback is a
conservative size bound, not an exact provider token count.
"""

from __future__ import annotations

import copy
import json
from collections.abc import Callable

_PRIVATE_MESSAGE_FIELDS = {"wingman_native_state", "wingman_native_route"}
_STRUCTURAL_TOKENS = 64


class ContextBudgetError(ValueError):
    """A request cannot fit the configured context and reply budget."""

    def __init__(self, code: str, report: dict[str, int | str | bool]):
        self.code = code
        self.report = dict(report)
        super().__init__(code)


def validate_budget(context_window, max_output_tokens, tool_reserve_tokens):
    """Validate positive context/reply budgets and a nonnegative tool reserve."""
    if context_window is None:
        return
    if type(context_window) is not int or context_window <= 0:
        raise ValueError("context_window must be a positive integer")
    if type(max_output_tokens) is not int or max_output_tokens <= 0:
        raise ValueError("max_output_tokens must be a positive integer")
    if type(tool_reserve_tokens) is not int or tool_reserve_tokens < 0:
        raise ValueError("tool_reserve_tokens must be a nonnegative integer")
    if max_output_tokens + tool_reserve_tokens >= context_window:
        raise ValueError("reply and tool reserve must be smaller than context_window")


def _public_messages(messages):
    if not isinstance(messages, list):
        return messages
    return [
        {key: value for key, value in message.items() if key not in _PRIVATE_MESSAGE_FIELDS}
        if isinstance(message, dict)
        else message
        for message in messages
    ]


def _serialized_input(payload):
    projection = {"messages": _public_messages(payload.get("messages", []))}
    for key in ("system", "tools", "tool_choice"):
        if key in payload:
            projection[key] = payload[key]
    return json.dumps(projection, ensure_ascii=False, separators=(",", ":"))


def _is_tool_continuation(messages):
    if not isinstance(messages, list) or not messages or not isinstance(messages[-1], dict):
        return False
    last = messages[-1]
    if last.get("role") == "tool":
        return True
    content = last.get("content")
    return isinstance(content, list) and any(
        isinstance(block, dict) and block.get("type") == "tool_result" for block in content
    )


def _estimate_input(serialized, token_counter):
    if token_counter is None:
        return len(serialized.encode("utf-8")) + _STRUCTURAL_TOKENS, "utf8_bytes_upper_bound"
    if not callable(token_counter):
        raise ValueError("token_counter must be callable")
    counted = token_counter(serialized)
    if type(counted) is not int or counted < 0:
        raise ValueError("token_counter must return a nonnegative integer")
    return (counted * 11 + 9) // 10 + _STRUCTURAL_TOKENS, "token_counter_10pct_plus_64"


def _effective_output(payload, configured_cap):
    keys = [key for key in ("max_completion_tokens", "max_tokens") if key in payload]
    existing = []
    for key in keys:
        value = payload[key]
        if type(value) is not int or value <= 0:
            raise ValueError(f"{key} must be a positive integer when supplied")
        existing.append(value)
    return min([configured_cap, *existing])


def apply_budget(
    payload,
    context_window,
    max_output_tokens,
    tool_reserve_tokens,
    *,
    token_counter: Callable[[str], int] | None = None,
):
    """Return a deep-copied request with a capped reply and numeric budget report.

    The tool reserve is held back on new user requests and made available again
    when the last message is a tool result or a native Messages tool_result.
    """
    copied = copy.deepcopy(payload)
    if context_window is None:
        return copied, None
    validate_budget(context_window, max_output_tokens, tool_reserve_tokens)
    if not isinstance(copied, dict) or not isinstance(copied.get("messages"), list):
        raise ValueError("payload must contain a messages list")

    output_tokens = _effective_output(copied, max_output_tokens)
    tool_continuation = _is_tool_continuation(copied["messages"])
    reserve = 0 if tool_continuation else tool_reserve_tokens
    input_limit = context_window - output_tokens - reserve
    estimate, method = _estimate_input(_serialized_input(copied), token_counter)
    report = {
        "input_tokens_estimate": estimate,
        "context_window": context_window,
        "output_tokens": output_tokens,
        "tool_reserve_tokens": tool_reserve_tokens,
        "tool_continuation": tool_continuation,
        "input_limit": input_limit,
        "estimate_method": method,
    }

    thinking = copied.get("thinking")
    thinking_budget = thinking.get("budget_tokens") if isinstance(thinking, dict) else None
    if thinking_budget is not None:
        if type(thinking_budget) is not int or thinking_budget < 0:
            raise ValueError("thinking.budget_tokens must be a nonnegative integer")
        if thinking_budget >= output_tokens:
            raise ContextBudgetError("reply_budget_below_thinking_budget", report)

    if estimate > input_limit:
        raise ContextBudgetError("context_budget_exceeded", report)

    if "max_completion_tokens" in copied:
        copied["max_completion_tokens"] = output_tokens
    if "max_tokens" in copied or "max_completion_tokens" not in copied:
        copied["max_tokens"] = output_tokens
    return copied, report
