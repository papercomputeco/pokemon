"""Shared fixtures for Pokemon agent tests."""

import json

import pytest
from unittest.mock import MagicMock


class FakeMemory:
    """Dict-backed memory that mimics pyboy.memory[addr] access."""

    def __init__(self):
        self._data: dict[int, int] = {}

    def __getitem__(self, addr: int) -> int:
        return self._data.get(addr, 0)

    def __setitem__(self, addr: int, value: int):
        self._data[addr] = value & 0xFF


@pytest.fixture
def fake_memory():
    return FakeMemory()


@pytest.fixture
def mock_pyboy(fake_memory):
    """PyBoy mock with dict-backed memory."""
    pyboy = MagicMock()
    pyboy.memory = fake_memory
    return pyboy


@pytest.fixture
def make_tape_entry():
    """Factory for creating synthetic JSONL tape lines."""

    def _make(
        entry_type="user",
        session_id="test-session-001",
        timestamp="2026-03-09T10:00:00.000Z",
        text="hello",
        tool_uses=None,
        tool_results=None,
        usage=None,
        parent_tool_use_id=None,
        system_content=None,
    ):
        entry = {
            "type": entry_type,
            "sessionId": session_id,
            "timestamp": timestamp,
            "uuid": "uuid-001",
            "parentUuid": None,
        }

        if parent_tool_use_id:
            entry["parentToolUseID"] = parent_tool_use_id

        if entry_type == "user":
            content = []
            if text:
                content.append({"type": "text", "text": text})
            if tool_results:
                for tr in tool_results:
                    content.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": tr.get("tool_use_id", "tu-001"),
                            "content": tr.get("content", "ok"),
                            "is_error": tr.get("is_error", False),
                        }
                    )
            entry["message"] = {"role": "user", "content": content}

        elif entry_type == "assistant":
            content = []
            if text:
                content.append({"type": "text", "text": text})
            if tool_uses:
                for tu in tool_uses:
                    content.append(
                        {
                            "type": "tool_use",
                            "id": tu.get("id", "tu-001"),
                            "name": tu.get("name", "Bash"),
                            "input": tu.get("input", {}),
                        }
                    )
            msg = {
                "role": "assistant",
                "content": content,
                "model": "claude-opus-4-6",
                "type": "message",
            }
            if usage:
                msg["usage"] = usage
            entry["message"] = msg

        elif entry_type == "system":
            entry["content"] = system_content or text or ""

        elif entry_type == "progress":
            entry["data"] = {"type": "hook_progress"}

        return json.dumps(entry)

    return _make
