import json

import pytest
from provider_fallback_skill.context_budget import (
    ContextBudgetError,
    apply_budget,
    validate_budget,
)


@pytest.mark.parametrize(
    "window,output,reserve",
    [
        (True, 10, 1),
        (100, 0, 1),
        (100, True, 1),
        (100, 10, -1),
        (100, 50, 50),
    ],
)
def test_validate_budget_rejects_invalid_or_exhaustive_budgets(window, output, reserve):
    with pytest.raises(ValueError):
        validate_budget(window, output, reserve)


def test_validate_budget_accepts_exact_integer_boundary():
    validate_budget(100, 60, 39)


def test_unset_route_does_not_enable_a_budget():
    validate_budget(None, 4096, 8192)


def test_native_system_and_tool_choice_are_counted_and_output_default_is_compatible():
    seen = []

    def count(text):
        seen.append(json.loads(text))
        return len(text)

    payload = {"messages": [], "system": "rules " * 200, "tool_choice": {"type": "any"}}
    with pytest.raises(ContextBudgetError):
        apply_budget(payload, 1000, 100, 100, token_counter=count)
    assert seen[0]["system"] == payload["system"]
    assert seen[0]["tool_choice"] == payload["tool_choice"]
    body, _ = apply_budget({"messages": []}, 1000, 100, 100, token_counter=lambda _: 1)
    assert body["max_tokens"] == 100
    assert "max_completion_tokens" not in body


def test_unset_window_returns_unchanged_copy_and_no_report():
    payload = {"messages": [{"role": "user", "content": "hello"}], "max_tokens": 17}
    result, report = apply_budget(payload, None, None, None)

    assert result == payload and result is not payload
    assert report is None
    assert result["messages"] is not payload["messages"]


def test_budget_clamps_output_without_mutating_or_dropping_messages_and_tools():
    payload = {
        "model": "waterfall",
        "temperature": 0.2,
        "max_tokens": 400,
        "max_completion_tokens": 90,
        "messages": [
            {"role": "system", "content": "Rules"},
            {"role": "assistant", "tool_calls": [{"id": "call-1"}]},
            {"role": "tool", "tool_call_id": "call-1", "content": "result"},
        ],
        "tools": [{"type": "function", "function": {"name": "useful", "parameters": {}}}],
    }
    original = json.loads(json.dumps(payload))

    result, report = apply_budget(payload, 1000, 200, 100)

    assert result["max_tokens"] == result["max_completion_tokens"] == 90
    assert result["messages"] == payload["messages"]
    assert result["tools"] == payload["tools"]
    assert payload == original
    assert report["output_tokens"] == 90
    assert report["tool_continuation"] is True
    assert report["input_limit"] == 910


def test_tool_reserve_is_held_for_new_user_and_released_for_tool_results():
    def counter(_text):
        return 110
    user_payload = {"messages": [{"role": "user", "content": "question"}]}
    tool_payload = {"messages": [{"role": "tool", "content": "answer"}]}
    native_payload = {
        "messages": [{"role": "user", "content": [{"type": "tool_result", "content": "answer"}]}]
    }

    with pytest.raises(ContextBudgetError) as user_error:
        apply_budget(user_payload, 300, 80, 40, token_counter=counter)
    tool_result, tool_report = apply_budget(tool_payload, 300, 80, 40, token_counter=counter)
    native_result, native_report = apply_budget(native_payload, 300, 80, 40, token_counter=counter)

    assert user_error.value.code == "context_budget_exceeded"
    assert user_error.value.report["input_limit"] == 180
    assert tool_result["messages"] == tool_payload["messages"]
    assert tool_report["input_limit"] == 220 and tool_report["tool_continuation"]
    assert native_result["messages"] == native_payload["messages"]
    assert native_report["input_limit"] == 220 and native_report["tool_continuation"]


def test_token_counter_padding_and_private_native_state_are_not_counted():
    payload = {
        "messages": [
            {
                "role": "assistant",
                "content": "visible",
                "wingman_native_state": {"blocks": [{"signature": "private-signature"}]},
            }
        ],
        "tools": [],
        "model": "excluded-model-field",
        "temperature": 0.4,
    }
    seen = []

    def count(serialized):
        seen.append(serialized)
        return 101

    result, report = apply_budget(payload, 1000, 100, 20, token_counter=count)

    assert report["input_tokens_estimate"] == 64 + 112
    assert report["estimate_method"] == "token_counter_10pct_plus_64"
    assert "visible" in seen[0]
    assert "private-signature" not in seen[0]
    assert "excluded-model-field" not in seen[0] and "temperature" not in seen[0]
    assert "wingman_native_state" in result["messages"][0]
    assert (
        payload["messages"][0]["wingman_native_state"]["blocks"][0]["signature"]
        == "private-signature"
    )


def test_utf8_byte_upper_bound_is_reported_without_text():
    payload = {"messages": [{"role": "user", "content": "café"}]}
    projection = '{"messages":[{"role":"user","content":"café"}]}'

    _, report = apply_budget(payload, 1000, 100, 20)

    assert report["estimate_method"] == "utf8_bytes_upper_bound"
    assert report["input_tokens_estimate"] == len(projection.encode("utf-8")) + 64
    assert all("café" not in str(value) for value in report.values())


def test_overflow_report_contains_only_numeric_budget_metadata():
    payload = {"messages": [{"role": "user", "content": "secret transcript"}], "tools": []}
    with pytest.raises(ContextBudgetError) as error:
        apply_budget(payload, 100, 20, 30, token_counter=lambda _text: 100)

    assert error.value.code == "context_budget_exceeded"
    assert error.value.report == {
        "input_tokens_estimate": 174,
        "context_window": 100,
        "output_tokens": 20,
        "tool_reserve_tokens": 30,
        "tool_continuation": False,
        "input_limit": 50,
        "estimate_method": "token_counter_10pct_plus_64",
    }
    assert "secret transcript" not in str(error.value.report)


def test_native_thinking_budget_must_fit_reply_cap():
    payload = {
        "messages": [{"role": "user", "content": "question"}],
        "thinking": {"budget_tokens": 100},
    }

    with pytest.raises(ContextBudgetError) as error:
        apply_budget(payload, 1000, 100, 20)

    assert error.value.code == "reply_budget_below_thinking_budget"
    assert error.value.report["output_tokens"] == 100
    assert "thinking" not in error.value.report
