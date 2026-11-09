import asyncio
from types import SimpleNamespace

import pytest
from provider_fallback_skill.jev_assist import NativeJevAssist, NativeJevRefused


def payload():
    return {
        "messages": [{"role": "user", "content": "hello"}],
        "tools": [
            {"type": "function", "function": {"name": "one", "description": "first"}},
            {"type": "function", "function": {"name": "two", "description": "second"}},
        ],
    }


class Facade:
    available = True

    def __init__(self, selected="one", confidence=0.9, error=None):
        self.selected, self.confidence, self.error, self.calls = selected, confidence, error, 0

    def choice(self, instruction, criteria):
        assert "none_of_these" in criteria
        return {"instruction": instruction, "criteria": criteria}

    async def decide(self, state, questions):
        self.calls += 1
        self.state = state
        return SimpleNamespace(
            ok=self.error is None,
            error=self.error,
            seconds=0.12,
            choice=lambda key, min_confidence: self.selected
            if self.confidence >= min_confidence
            else None,
        )


def test_selected_uncertain_and_off():
    async def run():
        original = payload()
        facade = Facade()
        assist = NativeJevAssist(facade)
        narrowed, status = await assist.assist(original)
        assert status["status"] == "selected" and len(narrowed["tools"]) == 1
        assert len(original["tools"]) == 2 and facade.state == {
            "user": "hello",
            "previous_assistant": "",
        }
        facade.confidence = 0.4
        assert (await assist.assist(original))[0] is original
        facade.available = False
        assert (await assist.assist(original))[1]["status"] == "unavailable"

    asyncio.run(run())


def test_followup_forced_and_image_skip():
    async def run():
        f = Facade()
        assist = NativeJevAssist(f)
        p = payload()
        p["tool_choice"] = "none"
        assert (await assist.assist(p))[0] is p
        p = payload()
        p["messages"].append({"role": "tool", "content": "done"})
        assert (await assist.assist(p))[0] is p
        p = payload()
        p["messages"].insert(0, {"role": "user", "content": [{"type": "image_url"}]})
        assert (await assist.assist(p))[0] is p
        assert f.calls == 0

    asyncio.run(run())


def test_new_user_turn_after_completed_tool_history_is_eligible():
    async def run():
        f = Facade()
        p = payload()
        p["messages"] = [
            {"role": "assistant", "content": None, "tool_calls": [{"id": "old"}]},
            {"role": "tool", "content": "done", "tool_call_id": "old"},
            {"role": "assistant", "content": "Finished."},
            {"role": "user", "content": "Another request"},
        ]
        _, status = await NativeJevAssist(f).assist(p)
        assert status["status"] == "selected" and f.calls == 1
        assert f.state["previous_assistant"] == "Finished."

    asyncio.run(run())


def test_credit_sticky_reset_and_refusal_terminal():
    async def run():
        f = Facade(error="HTTP 402: unavailable")
        assist = NativeJevAssist(f)
        p = payload()
        assert (await assist.assist(p))[1]["status"] == "credit_exhausted"
        f.error = None
        assert (await assist.assist(p))[0] is p and f.calls == 1
        assist.reset()
        assert (await assist.assist(p))[1]["status"] == "selected"
        f.error = "HTTP 403: denied"
        with pytest.raises(NativeJevRefused):
            await assist.assist(p)

    asyncio.run(run())


def test_cancellation_propagates():
    class Cancel(Facade):
        async def decide(self, state, questions):
            raise asyncio.CancelledError

    async def run():
        with pytest.raises(asyncio.CancelledError):
            await NativeJevAssist(Cancel()).assist(payload())

    asyncio.run(run())
