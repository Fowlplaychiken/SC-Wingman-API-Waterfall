import copy

import pytest
from provider_fallback_skill.native_messages import (
    NativeMessagesError,
    from_native,
    to_native,
)

MODEL = "claude-example"


def native_response():
    return {
        "id": "msg-1",
        "type": "message",
        "model": MODEL,
        "stop_reason": "tool_use",
        "content": [
            {"type": "thinking", "thinking": "", "signature": "opaque-signature"},
            {"type": "text", "text": "Checking."},
            {"type": "tool_use", "id": "tool-1", "name": "lookup", "input": {"x": 2}},
        ],
        "usage": {"input_tokens": 10, "output_tokens": 4},
    }


def test_sdk_roundtrip_preserves_opaque_thinking_and_tool_result():
    ChatCompletion = pytest.importorskip("openai.types.chat").ChatCompletion
    completion = from_native(native_response(), MODEL)
    message = (
        ChatCompletion.model_validate(completion).choices[0].message.model_dump(exclude_none=True)
    )
    assert message["wingman_native_state"]["blocks"][0]["signature"] == "opaque-signature"
    assert message["content"] == "Checking."
    result = to_native(
        {
            "messages": [
                {"role": "user", "content": "Look up."},
                message,
                {"role": "tool", "tool_call_id": "tool-1", "content": "result"},
            ]
        },
        MODEL,
        {"thinking": {"type": "adaptive"}, "output_config": {"effort": "low"}},
    )
    assert result["thinking"] == {"type": "adaptive", "display": "omitted"}
    assert result["messages"][1]["content"] == native_response()["content"]
    assert result["messages"][2]["content"][0]["tool_use_id"] == "tool-1"
    assert completion["usage"]["total_tokens"] == 14


def test_rejects_changed_tool_arguments_and_nonempty_thinking():
    message = from_native(native_response(), MODEL)["choices"][0]["message"]
    message["tool_calls"][0]["function"]["arguments"] = '{"x":3}'
    with pytest.raises(NativeMessagesError, match="native_state_conflict"):
        to_native({"messages": [message]}, MODEL, {})
    response = native_response()
    response["content"][0]["thinking"] = "private thinking"
    with pytest.raises(NativeMessagesError, match="unsupported_native_block"):
        from_native(response, MODEL)


def test_tools_images_and_stream_rejection():
    body = {
        "messages": [
            {"role": "system", "content": "Be useful"},
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": "data:image/png;base64,YQ=="}}
                ],
            },
        ],
        "tools": [
            {"type": "function", "function": {"name": "lookup", "parameters": {"type": "object"}}}
        ],
    }
    converted = to_native(copy.deepcopy(body), MODEL, {})
    assert converted["tools"][0]["input_schema"] == {"type": "object"}
    assert converted["messages"][0]["content"][0]["source"]["data"] == "YQ=="
    body["stream"] = True
    with pytest.raises(NativeMessagesError, match="stream_or_request_unsupported"):
        to_native(body, MODEL, {})


def test_malformed_arguments_rejected():
    with pytest.raises(NativeMessagesError):
        to_native(
            {
                "messages": [
                    {
                        "role": "assistant",
                        "tool_calls": [
                            {
                                "id": "x",
                                "type": "function",
                                "function": {"name": "n", "arguments": "[1]"},
                            }
                        ],
                    }
                ]
            },
            MODEL,
            {},
        )


def test_cached_tokens_count_toward_total_without_guessing_cost():
    response = native_response()
    response["usage"].update(cache_read_input_tokens=7, cache_creation_input_tokens=3)
    usage = from_native(response, MODEL)["usage"]
    assert usage["prompt_tokens"] == 20 and usage["total_tokens"] == 24
    assert usage["prompt_tokens_details"]["cached_tokens"] == 7
    assert "cost" not in usage


def test_none_keeps_tools_and_samplers_require_explicit_native_override():
    payload = {
        "messages": [{"role": "user", "content": "hi"}],
        "tools": [{"type": "function", "function": {"name": "f", "parameters": {}}}],
        "tool_choice": "none",
        "temperature": 0.1,
        "top_p": 0.3,
        "stop": ["x"],
    }
    body = to_native(payload, MODEL, {})
    assert body["tools"][0]["name"] == "f"
    assert body["tool_choice"] == {"type": "none"}
    assert not {"temperature", "top_p", "stop_sequences"} & body.keys()
    body = to_native(payload, MODEL, {"temperature": 1, "max_tokens": 400})
    assert body["temperature"] == 1 and body["max_tokens"] == 400


@pytest.mark.parametrize("arguments", ['{"x":1,"x":2}', '{"x":NaN}'])
def test_tool_arguments_reject_ambiguous_json(arguments):
    payload = {
        "messages": [
            {
                "role": "assistant",
                "tool_calls": [
                    {
                        "id": "a",
                        "type": "function",
                        "function": {"name": "f", "arguments": arguments},
                    }
                ],
            }
        ]
    }
    with pytest.raises(NativeMessagesError):
        to_native(payload, MODEL, {})


def test_native_response_rejects_duplicate_tool_ids():
    response = native_response()
    response["content"].append(copy.deepcopy(response["content"][-1]))
    with pytest.raises(NativeMessagesError, match="invalid_native_tool"):
        from_native(response, MODEL)


@pytest.mark.parametrize("field,value", [("thinking", None), ("thinking", "adaptive")])
def test_invalid_native_configuration_has_a_typed_error(field, value):
    with pytest.raises(NativeMessagesError, match="invalid_thinking_configuration"):
        to_native({"messages": [{"role": "user", "content": "hi"}]}, MODEL, {field: value})


def test_invalid_native_image_and_tool_choice_have_typed_errors():
    with pytest.raises(NativeMessagesError, match="unsupported_image"):
        to_native(
            {"messages": [{"role": "user", "content": [{"type": "image_url", "image_url": 2}]}]},
            MODEL,
            {},
        )
    with pytest.raises(NativeMessagesError, match="invalid_tool_choice"):
        to_native({"messages": [], "tool_choice": {"type": "function"}}, MODEL, {})
