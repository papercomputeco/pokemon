"""Tests for tape_reader.py — 100% coverage."""

import json

import pytest

from tape_reader import (
    TapeEntry,
    TapeReader,
    TapeSession,
    SubagentSession,
    ToolUse,
    ToolResult,
    TokenUsage,
    _summarize_tool_input,
)


# ── Dataclass defaults ──────────────────────────────────────────────


class TestTapeEntry:
    def test_defaults(self):
        e = TapeEntry()
        assert e.type == ""
        assert e.timestamp == ""
        assert e.session_id == ""
        assert e.text_content == ""
        assert e.tool_uses == []
        assert e.tool_results == []
        assert e.token_usage == TokenUsage()
        assert e.raw == {}

    def test_mutable_defaults_independent(self):
        a = TapeEntry()
        b = TapeEntry()
        a.tool_uses.append(ToolUse(id="x"))
        assert b.tool_uses == []


class TestToolUse:
    def test_defaults(self):
        t = ToolUse()
        assert t.id == ""
        assert t.name == ""
        assert t.input_summary == ""


class TestToolResult:
    def test_defaults(self):
        r = ToolResult()
        assert r.tool_use_id == ""
        assert r.content_summary == ""
        assert r.is_error is False


class TestTokenUsage:
    def test_defaults(self):
        u = TokenUsage()
        assert u.input_tokens == 0
        assert u.output_tokens == 0
        assert u.cache_creation == 0
        assert u.cache_read == 0


class TestSubagentSession:
    def test_defaults(self):
        s = SubagentSession()
        assert s.agent_id == ""
        assert s.entries == []


class TestTapeSession:
    def test_defaults(self):
        s = TapeSession()
        assert s.session_id == ""
        assert s.entries == []
        assert s.subagent_sessions == []
        assert s.start_time == ""
        assert s.end_time == ""


# ── parse_entry ──────────────────────────────────────────────────────


class TestParseEntry:
    def test_user_text_message(self, make_tape_entry):
        line = make_tape_entry(entry_type="user", text="do something")
        entry = TapeReader.parse_entry(line)
        assert entry.type == "user"
        assert entry.text_content == "do something"
        assert entry.session_id == "test-session-001"
        assert entry.timestamp == "2026-03-09T10:00:00.000Z"

    def test_user_with_tool_results(self, make_tape_entry):
        line = make_tape_entry(
            entry_type="user",
            text="",
            tool_results=[
                {
                    "tool_use_id": "tu-abc",
                    "content": "file contents here",
                    "is_error": False,
                },
            ],
        )
        entry = TapeReader.parse_entry(line)
        assert len(entry.tool_results) == 1
        assert entry.tool_results[0].tool_use_id == "tu-abc"
        assert entry.tool_results[0].content_summary == "file contents here"
        assert entry.tool_results[0].is_error is False

    def test_user_with_error_tool_result(self, make_tape_entry):
        line = make_tape_entry(
            entry_type="user",
            text="",
            tool_results=[
                {
                    "tool_use_id": "tu-err",
                    "content": "command failed",
                    "is_error": True,
                },
            ],
        )
        entry = TapeReader.parse_entry(line)
        assert entry.tool_results[0].is_error is True

    def test_user_tool_result_with_list_content(self):
        """Tool result content can be a list of text blocks."""
        raw = {
            "type": "user",
            "sessionId": "s1",
            "timestamp": "2026-01-01T00:00:00Z",
            "message": {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "tu-1",
                        "content": [
                            {"type": "text", "text": "line 1"},
                            {"type": "text", "text": "line 2"},
                        ],
                    }
                ],
            },
        }
        entry = TapeReader.parse_entry(json.dumps(raw))
        assert entry.tool_results[0].content_summary == "line 1\nline 2"

    def test_system_entry(self, make_tape_entry):
        line = make_tape_entry(
            entry_type="system", system_content="session started"
        )
        entry = TapeReader.parse_entry(line)
        assert entry.type == "system"
        assert entry.text_content == "session started"

    def test_system_entry_with_message_content(self):
        """System entry where content comes from message.content."""
        raw = {
            "type": "system",
            "sessionId": "s1",
            "timestamp": "2026-01-01T00:00:00Z",
            "message": {"content": "from message"},
        }
        entry = TapeReader.parse_entry(json.dumps(raw))
        assert entry.text_content == "from message"

    def test_progress_entry(self, make_tape_entry):
        line = make_tape_entry(entry_type="progress")
        entry = TapeReader.parse_entry(line)
        assert entry.type == "progress"
        assert entry.text_content == ""

    def test_unknown_type(self):
        raw = {
            "type": "file-history-snapshot",
            "snapshot": {},
        }
        entry = TapeReader.parse_entry(json.dumps(raw))
        assert entry.type == "file-history-snapshot"

    def test_string_message(self):
        """Some entries have message as a plain string."""
        raw = {
            "type": "user",
            "sessionId": "s1",
            "timestamp": "2026-01-01T00:00:00Z",
            "message": "plain text message",
        }
        entry = TapeReader.parse_entry(json.dumps(raw))
        assert entry.text_content == "plain text message"

    def test_user_with_string_content(self):
        """User message where content is a string, not list."""
        raw = {
            "type": "user",
            "sessionId": "s1",
            "timestamp": "2026-01-01T00:00:00Z",
            "message": {"role": "user", "content": "just a string"},
        }
        entry = TapeReader.parse_entry(json.dumps(raw))
        assert entry.text_content == "just a string"

    def test_no_message_field(self):
        raw = {"type": "progress", "timestamp": "2026-01-01T00:00:00Z"}
        entry = TapeReader.parse_entry(json.dumps(raw))
        assert entry.text_content == ""

    def test_content_list_with_non_dict_items(self):
        """Content list with non-dict items should be skipped."""
        raw = {
            "type": "user",
            "sessionId": "s1",
            "timestamp": "2026-01-01T00:00:00Z",
            "message": {"role": "user", "content": ["string item", 42]},
        }
        entry = TapeReader.parse_entry(json.dumps(raw))
        assert entry.text_content == ""


class TestParseEntryAssistant:
    def test_text_block(self, make_tape_entry):
        line = make_tape_entry(entry_type="assistant", text="I'll help you")
        entry = TapeReader.parse_entry(line)
        assert entry.type == "assistant"
        assert entry.text_content == "I'll help you"

    def test_tool_use_block(self, make_tape_entry):
        line = make_tape_entry(
            entry_type="assistant",
            text="Let me read that file.",
            tool_uses=[
                {"id": "tu-1", "name": "Read", "input": {"file_path": "/foo.py"}},
            ],
        )
        entry = TapeReader.parse_entry(line)
        assert len(entry.tool_uses) == 1
        assert entry.tool_uses[0].name == "Read"
        assert entry.tool_uses[0].input_summary == "/foo.py"

    def test_multiple_tool_uses(self, make_tape_entry):
        line = make_tape_entry(
            entry_type="assistant",
            text="",
            tool_uses=[
                {"id": "tu-1", "name": "Bash", "input": {"command": "ls"}},
                {"id": "tu-2", "name": "Grep", "input": {"pattern": "TODO"}},
            ],
        )
        entry = TapeReader.parse_entry(line)
        assert len(entry.tool_uses) == 2
        assert entry.tool_uses[0].input_summary == "ls"
        assert entry.tool_uses[1].input_summary == "pattern=TODO"

    def test_usage_extraction(self, make_tape_entry):
        line = make_tape_entry(
            entry_type="assistant",
            text="done",
            usage={
                "input_tokens": 1000,
                "output_tokens": 200,
                "cache_creation_input_tokens": 50,
                "cache_read_input_tokens": 800,
            },
        )
        entry = TapeReader.parse_entry(line)
        assert entry.token_usage.input_tokens == 1000
        assert entry.token_usage.output_tokens == 200
        assert entry.token_usage.cache_creation == 50
        assert entry.token_usage.cache_read == 800

    def test_no_usage(self, make_tape_entry):
        line = make_tape_entry(entry_type="assistant", text="ok")
        entry = TapeReader.parse_entry(line)
        assert entry.token_usage.input_tokens == 0

    def test_content_with_non_dict_items(self):
        """Assistant content list with non-dict items should be skipped."""
        raw = {
            "type": "assistant",
            "sessionId": "s1",
            "timestamp": "2026-01-01T00:00:00Z",
            "message": {
                "role": "assistant",
                "content": ["string item", {"type": "text", "text": "real text"}],
            },
        }
        entry = TapeReader.parse_entry(json.dumps(raw))
        assert entry.text_content == "real text"


# ── _summarize_tool_input ────────────────────────────────────────────


class TestSummarizeToolInput:
    def test_read(self):
        assert _summarize_tool_input("Read", {"file_path": "/a.py"}) == "/a.py"

    def test_write(self):
        assert _summarize_tool_input("Write", {"file_path": "/b.py"}) == "/b.py"

    def test_edit(self):
        assert _summarize_tool_input("Edit", {"file_path": "/c.py"}) == "/c.py"

    def test_bash(self):
        assert _summarize_tool_input("Bash", {"command": "ls -la"}) == "ls -la"

    def test_grep(self):
        assert _summarize_tool_input("Grep", {"pattern": "foo"}) == "pattern=foo"

    def test_glob(self):
        assert _summarize_tool_input("Glob", {"pattern": "*.py"}) == "pattern=*.py"

    def test_agent(self):
        assert (
            _summarize_tool_input("Agent", {"description": "explore code"})
            == "explore code"
        )

    def test_generic_with_known_key(self):
        result = _summarize_tool_input("WebSearch", {"query": "python docs"})
        assert result == "query=python docs"

    def test_generic_fallback(self):
        result = _summarize_tool_input("Unknown", {"some_key": "val"})
        assert "some_key" in result

    def test_non_dict_input(self):
        result = _summarize_tool_input("Foo", "just a string")
        assert result == "just a string"

    def test_generic_key_priority(self):
        """Generic summary checks keys in order: prompt, query, description..."""
        result = _summarize_tool_input(
            "Custom", {"description": "desc", "prompt": "p"}
        )
        assert result == "prompt=p"


# ── TapeReader ───────────────────────────────────────────────────────


class TestTapeReaderListSessions:
    def test_empty_dir(self, tmp_path):
        reader = TapeReader(str(tmp_path))
        assert reader.list_sessions() == []

    def test_finds_jsonl_files(self, tmp_path):
        (tmp_path / "abc-123.jsonl").write_text("{}\n")
        (tmp_path / "def-456.jsonl").write_text("{}\n")
        (tmp_path / "not-jsonl.txt").write_text("x")
        reader = TapeReader(str(tmp_path))
        sessions = reader.list_sessions()
        assert len(sessions) == 2
        assert "abc-123" in sessions
        assert "def-456" in sessions

    def test_sorted_order(self, tmp_path):
        (tmp_path / "bbb.jsonl").write_text("{}\n")
        (tmp_path / "aaa.jsonl").write_text("{}\n")
        reader = TapeReader(str(tmp_path))
        assert reader.list_sessions() == ["aaa", "bbb"]


class TestTapeReaderReadSession:
    def test_basic_session(self, tmp_path, make_tape_entry):
        lines = [
            make_tape_entry(entry_type="user", text="hi", timestamp="2026-01-01T00:00:00Z"),
            make_tape_entry(entry_type="assistant", text="hello", timestamp="2026-01-01T00:01:00Z"),
        ]
        (tmp_path / "sess1.jsonl").write_text("\n".join(lines) + "\n")

        reader = TapeReader(str(tmp_path))
        session = reader.read_session("sess1")
        assert session.session_id == "sess1"
        assert len(session.entries) == 2
        assert session.start_time == "2026-01-01T00:00:00Z"
        assert session.end_time == "2026-01-01T00:01:00Z"

    def test_subagent_separation(self, tmp_path, make_tape_entry):
        lines = [
            make_tape_entry(entry_type="user", text="hi"),
            make_tape_entry(entry_type="assistant", text="main reply"),
            make_tape_entry(
                entry_type="assistant",
                text="subagent reply",
                parent_tool_use_id="tu-agent-1",
            ),
            make_tape_entry(
                entry_type="user",
                text="subagent input",
                parent_tool_use_id="tu-agent-1",
            ),
        ]
        (tmp_path / "s2.jsonl").write_text("\n".join(lines) + "\n")

        reader = TapeReader(str(tmp_path))
        session = reader.read_session("s2")
        assert len(session.entries) == 2  # main entries only
        assert len(session.subagent_sessions) == 1
        assert session.subagent_sessions[0].agent_id == "tu-agent-1"
        assert len(session.subagent_sessions[0].entries) == 2

    def test_empty_session(self, tmp_path):
        (tmp_path / "empty.jsonl").write_text("")
        reader = TapeReader(str(tmp_path))
        session = reader.read_session("empty")
        assert session.entries == []
        assert session.start_time == ""
        assert session.end_time == ""

    def test_entries_without_timestamps(self, tmp_path):
        """Entries without timestamps shouldn't set time bounds."""
        raw = json.dumps({"type": "file-history-snapshot", "snapshot": {}})
        (tmp_path / "no-ts.jsonl").write_text(raw + "\n")
        reader = TapeReader(str(tmp_path))
        session = reader.read_session("no-ts")
        assert session.start_time == ""
        assert session.end_time == ""


class TestTapeReaderIterEntries:
    def test_generator_behavior(self, tmp_path, make_tape_entry):
        lines = [
            make_tape_entry(entry_type="user", text="line1"),
            make_tape_entry(entry_type="assistant", text="line2"),
        ]
        (tmp_path / "gen.jsonl").write_text("\n".join(lines) + "\n")

        reader = TapeReader(str(tmp_path))
        gen = reader.iter_entries("gen")
        first = next(gen)
        assert first.text_content == "line1"
        second = next(gen)
        assert second.text_content == "line2"
        with pytest.raises(StopIteration):
            next(gen)

    def test_skips_blank_lines(self, tmp_path, make_tape_entry):
        content = make_tape_entry(entry_type="user", text="only") + "\n\n\n"
        (tmp_path / "blanks.jsonl").write_text(content)
        reader = TapeReader(str(tmp_path))
        entries = list(reader.iter_entries("blanks"))
        assert len(entries) == 1
