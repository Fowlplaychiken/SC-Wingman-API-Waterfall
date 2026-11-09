"""Optional first-turn tool selection through Wingman's public System One facade."""

from __future__ import annotations

import asyncio
import json
import math
import re
import threading


class NativeJevRefused(Exception):
    """A provider refusal must not be routed around."""


def _failure_kind(error):
    if not isinstance(error, str):
        return "unavailable"
    match = re.match(r"^HTTP\s+(\d{3})(?:\s*:\s*(.*))?$", error, re.DOTALL)
    if not match:
        return "unavailable"
    status = int(match[1])
    if status == 403:
        return "refused"
    if status == 402:
        return "credit_exhausted"
    if status == 400 and match[2]:
        try:
            body = json.loads(match[2])
        except (TypeError, ValueError):
            return "unavailable"
        error_obj = body.get("error") if isinstance(body, dict) else None
        if isinstance(error_obj, dict) and error_obj.get("code") in {
            "content_policy_violation",
            "content_filter",
        }:
            return "refused"
        if isinstance(error_obj, dict) and error_obj.get("code") in {
            "insufficient_quota",
            "insufficient_credits",
            "credit_balance_too_low",
        }:
            return "credit_exhausted"
    return "unavailable"


class NativeJevAssist:
    def __init__(self, system_one):
        self._system_one = system_one
        self._lock = threading.Lock()
        self._credit_exhausted = False
        self._generation = 0

    def reset(self):
        with self._lock:
            self._credit_exhausted = False
            self._generation += 1

    async def assist(self, payload, min_confidence=0.8):
        if (
            type(min_confidence) not in (int, float)
            or not math.isfinite(min_confidence)
            or not 0 <= min_confidence <= 1
        ):
            raise ValueError("Invalid confidence threshold")
        if type(payload) is not dict:
            raise ValueError("Invalid chat payload")
        messages, tools = payload.get("messages"), payload.get("tools")
        if (
            type(messages) is not list
            or not messages
            or type(messages[-1]) is not dict
            or messages[-1].get("role") != "user"
            or type(tools) is not list
            or not tools
            or payload.get("tool_choice", "auto") != "auto"
        ):
            return payload, {"status": "skipped"}
        if len(tools) > 254:
            return payload, {"status": "skipped_catalog_size"}
        current = messages[-1].get("content")
        if any(
            isinstance(m, dict)
            and isinstance(m.get("content"), list)
            and any(
                isinstance(part, dict) and part.get("type") in {"image", "image_url"}
                for part in m["content"]
            )
            for m in messages
        ):
            return payload, {"status": "skipped_nontext"}
        if type(current) is not str or not current.strip():
            return payload, {"status": "skipped_nontext"}
        criteria = {}
        for item in tools:
            if (
                type(item) is not dict
                or item.get("type") != "function"
                or type(item.get("function")) is not dict
            ):
                return payload, {"status": "skipped_tools"}
            fn = item["function"]
            name, description = fn.get("name"), fn.get("description", "")
            if (
                type(name) is not str
                or not name.strip()
                or name == "none_of_these"
                or name in criteria
                or type(description) is not str
            ):
                return payload, {"status": "skipped_tools"}
            criteria[name] = description
        previous = messages[-2] if len(messages) > 1 else None
        context = (
            previous.get("content")
            if isinstance(previous, dict) and previous.get("role") == "assistant"
            else None
        )
        if not isinstance(context, str):
            context = ""
        with self._lock:
            if self._credit_exhausted:
                return payload, {"status": "credit_exhausted"}
            generation = self._generation
        so = self._system_one
        if so is None or not getattr(so, "available", False):
            return payload, {"status": "unavailable"}
        criteria["none_of_these"] = "No single tool is clearly needed, or the request is uncertain."
        try:
            question = so.choice(
                "Which single FIRST tool is most useful for this user request? "
                "Choose none_of_these if uncertain.",
                criteria,
            )
            answer = await so.decide(
                {"user": current, "previous_assistant": context}, {"tool": question}
            )
            if not answer.ok:
                kind = _failure_kind(answer.error)
                if kind == "refused":
                    raise NativeJevRefused("native_jev_refused")
                if kind == "credit_exhausted":
                    with self._lock:
                        if self._generation == generation:
                            self._credit_exhausted = True
                return payload, {"status": kind}
            selected = answer.choice("tool", min_confidence=min_confidence)
            seconds = answer.seconds
        except asyncio.CancelledError:
            raise
        except NativeJevRefused:
            raise
        except Exception:
            return payload, {"status": "unavailable"}
        status = {"status": "unselected"}
        if type(seconds) in (int, float) and math.isfinite(seconds) and seconds >= 0:
            status["seconds"] = seconds
        if selected not in criteria or selected == "none_of_these":
            return payload, status
        narrowed = dict(payload)
        narrowed["tools"] = [item for item in tools if item["function"]["name"] == selected]
        status["status"] = "selected"
        status["tool"] = selected
        return narrowed, status
