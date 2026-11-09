"""Stateless OpenAI-chat/Anthropic Messages codec for the optional Waterfall route.

Messages shapes follow Anthropic's public Messages API and the project-owned
assistant_tool_benchmark native Messages client. Signed omitted-thinking blocks
remain opaque provider data; this module never executes tools.
"""

from __future__ import annotations

import copy
import json
import re
import time


class NativeMessagesError(ValueError):
    """Unsupported or inconsistent input; never includes private message data."""


def _object(text):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate member")
            result[key] = value
        return result

    try:
        value = json.loads(
            text,
            object_pairs_hook=unique,
            parse_constant=lambda _: (_ for _ in ()).throw(ValueError("non-finite")),
        )
    except (TypeError, ValueError):
        raise NativeMessagesError("invalid_tool_arguments") from None
    if type(value) is not dict:
        raise NativeMessagesError("invalid_tool_arguments")
    return value


def _content(value):
    if value is None:
        return []
    if isinstance(value, str):
        return [{"type": "text", "text": value}] if value else []
    if type(value) is not list:
        raise NativeMessagesError("unsupported_content")
    result = []
    for item in value:
        if type(item) is not dict:
            raise NativeMessagesError("unsupported_content")
        if item.get("type") == "text" and type(item.get("text")) is str:
            result.append({"type": "text", "text": item["text"]})
        elif item.get("type") == "image_url":
            image = item.get("image_url")
            url = image.get("url") if type(image) is dict else image
            if type(url) is not str:
                raise NativeMessagesError("unsupported_image")
            match = re.fullmatch(
                r"data:(image/(?:jpeg|png|gif|webp));base64,([A-Za-z0-9+/]+={0,2})", url or ""
            )
            if not match:
                raise NativeMessagesError("unsupported_image")
            result.append(
                {
                    "type": "image",
                    "source": {"type": "base64", "media_type": match[1], "data": match[2]},
                }
            )
        else:
            raise NativeMessagesError("unsupported_content")
    return result


def _visible(blocks):
    texts, calls = [], []
    ids = set()
    for block in blocks:
        if type(block) is not dict:
            raise NativeMessagesError("invalid_native_state")
        kind = block.get("type")
        if kind == "text" and type(block.get("text")) is str:
            texts.append(block["text"])
        elif (
            kind == "tool_use"
            and type(block.get("id")) is str
            and type(block.get("name")) is str
            and type(block.get("input")) is dict
        ):
            if not block["id"].strip() or not block["name"].strip() or block["id"] in ids:
                raise NativeMessagesError("invalid_native_tool")
            try:
                json.dumps(block["input"], allow_nan=False)
            except (TypeError, ValueError):
                raise NativeMessagesError("invalid_tool_arguments") from None
            ids.add(block["id"])
            calls.append(
                {
                    "id": block["id"],
                    "type": "function",
                    "function": {
                        "name": block["name"],
                        "arguments": json.dumps(
                            block["input"], separators=(",", ":"), ensure_ascii=False
                        ),
                    },
                }
            )
        elif (
            kind == "thinking"
            and block.get("thinking") == ""
            and type(block.get("signature")) is str
            and block["signature"]
        ):
            continue
        elif kind == "redacted_thinking" and type(block.get("data")) is str and block["data"]:
            continue
        else:
            raise NativeMessagesError("unsupported_native_block")
    return "".join(texts), calls


def _calls_equal(left, right):
    if type(left) is not list or len(left) != len(right):
        return False
    for x, y in zip(left, right, strict=True):
        try:
            if (
                x["id"] != y["id"]
                or x["type"] != "function"
                or x["function"]["name"] != y["function"]["name"]
                or _object(x["function"]["arguments"]) != _object(y["function"]["arguments"])
            ):
                return False
        except (KeyError, TypeError, NativeMessagesError):
            return False
    return True


def to_native(payload, model, overrides):
    if (
        type(payload) is not dict
        or type(payload.get("messages")) is not list
        or payload.get("stream", False) is not False
    ):
        raise NativeMessagesError("stream_or_request_unsupported")
    if type(model) is not str or not model or type(overrides) is not dict:
        raise NativeMessagesError("invalid_configuration")
    messages, system = [], []
    for msg in payload["messages"]:
        if type(msg) is not dict:
            raise NativeMessagesError("invalid_message")
        role = msg.get("role")
        if role in ("system", "developer"):
            if type(msg.get("content")) is not str:
                raise NativeMessagesError("unsupported_system")
            system.append(msg["content"])
        elif role == "tool":
            if type(msg.get("tool_call_id")) is not str or type(msg.get("content")) is not str:
                raise NativeMessagesError("invalid_tool_result")
            block = {
                "type": "tool_result",
                "tool_use_id": msg["tool_call_id"],
                "content": msg["content"],
            }
            if (
                messages
                and messages[-1]["role"] == "user"
                and all(x.get("type") == "tool_result" for x in messages[-1]["content"])
            ):
                messages[-1]["content"].append(block)
            else:
                messages.append({"role": "user", "content": [block]})
        elif role == "user":
            messages.append({"role": "user", "content": _content(msg.get("content"))})
        elif role == "assistant":
            state = msg.get("wingman_native_state")
            if state is not None:
                if (
                    type(state) is not dict
                    or set(state) != {"version", "model", "blocks"}
                    or state["version"] != 1
                    or state["model"] != model
                    or type(state["blocks"]) is not list
                ):
                    raise NativeMessagesError("invalid_native_state")
                text, calls = _visible(state["blocks"])
                if text != (msg.get("content") or "") or not _calls_equal(
                    msg.get("tool_calls") or [], calls
                ):
                    raise NativeMessagesError("native_state_conflict")
                blocks = copy.deepcopy(state["blocks"])
            else:
                blocks = _content(msg.get("content"))
                for call in msg.get("tool_calls") or []:
                    try:
                        if (
                            call["type"] != "function"
                            or not call["id"]
                            or not call["function"]["name"]
                        ):
                            raise ValueError
                        blocks.append(
                            {
                                "type": "tool_use",
                                "id": call["id"],
                                "name": call["function"]["name"],
                                "input": _object(call["function"]["arguments"]),
                            }
                        )
                    except (KeyError, TypeError, ValueError):
                        raise NativeMessagesError("invalid_tool_call") from None
                _visible(blocks)
            messages.append({"role": "assistant", "content": blocks})
        else:
            raise NativeMessagesError("unsupported_role")
    body = {
        "model": model,
        "messages": messages,
        "max_tokens": payload.get("max_completion_tokens") or payload.get("max_tokens") or 2048,
    }
    if system:
        body["system"] = "\n\n".join(system)
    if payload.get("tools"):
        try:
            body["tools"] = [
                {
                    "name": t["function"]["name"],
                    "description": t["function"].get("description", ""),
                    "input_schema": t["function"]["parameters"],
                }
                for t in payload["tools"]
                if t["type"] == "function"
            ]
            if len(body["tools"]) != len(payload["tools"]):
                raise ValueError
        except (KeyError, TypeError, ValueError):
            raise NativeMessagesError("invalid_tools") from None
    if "tool_choice" in payload:
        v = payload["tool_choice"]
        if v == "auto":
            body["tool_choice"] = {"type": "auto"}
        elif v == "required":
            body["tool_choice"] = {"type": "any"}
        elif v == "none":
            body["tool_choice"] = {"type": "none"}
        elif type(v) is dict and v.get("type") == "function":
            if type(v.get("function")) is not dict or type(v["function"].get("name")) is not str:
                raise NativeMessagesError("invalid_tool_choice")
            body["tool_choice"] = {"type": "tool", "name": v["function"]["name"]}
        else:
            raise NativeMessagesError("invalid_tool_choice")
    if set(overrides) - {
        "thinking",
        "output_config",
        "temperature",
        "top_p",
        "stop_sequences",
        "max_tokens",
    }:
        raise NativeMessagesError("unsupported_native_override")
    body.update(copy.deepcopy(overrides))
    if "thinking" in body and type(body["thinking"]) is not dict:
        raise NativeMessagesError("invalid_thinking_configuration")
    if body.get("thinking", {}).get("type") == "adaptive":
        body["thinking"] = {**body["thinking"], "display": "omitted"}
    return body


def from_native(response, model):
    if (
        type(response) is not dict
        or response.get("type") != "message"
        or type(response.get("content")) is not list
    ):
        raise NativeMessagesError("invalid_native_response")
    blocks = response["content"]
    text, calls = _visible(blocks)
    reason = {
        "end_turn": "stop",
        "stop_sequence": "stop",
        "tool_use": "tool_calls",
        "max_tokens": "length",
    }.get(response.get("stop_reason"))
    if reason is None:
        raise NativeMessagesError("unsupported_stop_reason")
    usage = response.get("usage")
    if (
        type(usage) is not dict
        or type(usage.get("input_tokens")) is not int
        or type(usage.get("output_tokens")) is not int
        or min(usage["input_tokens"], usage["output_tokens"]) < 0
    ):
        raise NativeMessagesError("invalid_usage")
    cached = usage.get("cache_read_input_tokens", 0)
    created = usage.get("cache_creation_input_tokens", 0)
    if type(cached) is not int or type(created) is not int or min(cached, created) < 0:
        raise NativeMessagesError("invalid_usage")
    prompt_tokens = usage["input_tokens"] + cached + created
    message = {
        "role": "assistant",
        "content": text or None,
        "wingman_native_state": {"version": 1, "model": model, "blocks": copy.deepcopy(blocks)},
    }
    if calls:
        message["tool_calls"] = calls
    return {
        "id": response.get("id", "native-message"),
        "object": "chat.completion",
        "created": int(time.time()),
        "model": response.get("model", model),
        "choices": [{"index": 0, "message": message, "finish_reason": reason}],
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": usage["output_tokens"],
            "total_tokens": prompt_tokens + usage["output_tokens"],
            "prompt_tokens_details": {"cached_tokens": cached},
        },
    }
