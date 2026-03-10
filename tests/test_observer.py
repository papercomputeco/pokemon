"""Tests for observer.py — 100% coverage."""

import json

import pytest

from observer import (
    Observation,
    Observer,
    _first_user_message,
    _has_traceback,
    _extract_traceback_summary,
)
from tape_reader import TapeEntry, TapeSession, SubagentSession, ToolResult, TokenUsage


# ── Observation dataclass ────────────────────────────────────────────


class TestObservation:
    def test_defaults(self):
        o = Observation()
        assert o.timestamp == ""
        assert o.referenced_time == ""
        assert o.priority == "informational"
        assert o.content == ""
        assert o.source_session == ""


# ── Helper functions ─────────────────────────────────────────────────


class TestFirstUserMessage:
    def test_finds_first_user(self):
        session = TapeSession(
            session_id="s1",
            entries=[
                TapeEntry(type="system", text_content="init"),
                TapeEntry(type="user", text_content="build a feature"),
                TapeEntry(type="user", text_content="second msg"),
            ],
        )
        assert _first_user_message(session) == "build a feature"

    def test_no_user_messages(self):
        session = TapeSession(session_id="s1", entries=[])
        assert _first_user_message(session) == ""

    def test_user_with_empty_text(self):
        session = TapeSession(
            session_id="s1",
            entries=[
                TapeEntry(type="user", text_content=""),
                TapeEntry(type="user", text_content="actual message"),
            ],
        )
        assert _first_user_message(session) == "actual message"


class TestHasTraceback:
    def test_python_traceback(self):
        assert _has_traceback("Traceback (most recent call last):\n  File...")

    def test_error_colon(self):
        assert _has_traceback("ValueError: bad value")

    def test_no_traceback(self):
        assert not _has_traceback("everything is fine")


class TestExtractTracebackSummary:
    def test_extracts_last_error_line(self):
        text = "Some context\nValueError: bad input\nmore stuff"
        assert _extract_traceback_summary(text) == "ValueError: bad input"

    def test_exception_line(self):
        text = "RuntimeException: oops"
        assert _extract_traceback_summary(text) == "RuntimeException: oops"

    def test_no_error_line_falls_back(self):
        text = "just some output"
        assert _extract_traceback_summary(text) == "just some output"


# ── Observer ─────────────────────────────────────────────────────────


class TestObserverInit:
    def test_constructor(self, tmp_path):
        obs = Observer(
            project_dir=str(tmp_path / "project"),
            memory_dir=str(tmp_path / "memory"),
        )
        assert obs.project_dir == tmp_path / "project"
        assert obs.memory_dir == tmp_path / "memory"


class TestGetUnprocessedSessions:
    def test_all_unprocessed(self, tmp_path):
        proj = tmp_path / "project"
        proj.mkdir()
        (proj / "aaa.jsonl").write_text("{}\n")
        (proj / "bbb.jsonl").write_text("{}\n")
        mem = tmp_path / "memory"

        obs = Observer(str(proj), str(mem))
        assert obs.get_unprocessed_sessions() == ["aaa", "bbb"]

    def test_some_processed(self, tmp_path):
        proj = tmp_path / "project"
        proj.mkdir()
        (proj / "aaa.jsonl").write_text("{}\n")
        (proj / "bbb.jsonl").write_text("{}\n")
        mem = tmp_path / "memory"
        mem.mkdir()
        (mem / "observer_state.json").write_text(
            json.dumps({"processed_sessions": ["aaa"]})
        )

        obs = Observer(str(proj), str(mem))
        assert obs.get_unprocessed_sessions() == ["bbb"]

    def test_all_processed(self, tmp_path):
        proj = tmp_path / "project"
        proj.mkdir()
        (proj / "aaa.jsonl").write_text("{}\n")
        mem = tmp_path / "memory"
        mem.mkdir()
        (mem / "observer_state.json").write_text(
            json.dumps({"processed_sessions": ["aaa"]})
        )

        obs = Observer(str(proj), str(mem))
        assert obs.get_unprocessed_sessions() == []

    def test_empty_project(self, tmp_path):
        proj = tmp_path / "project"
        proj.mkdir()
        mem = tmp_path / "memory"

        obs = Observer(str(proj), str(mem))
        assert obs.get_unprocessed_sessions() == []


class TestObserveSession:
    def _make_session(self, entries=None, subagent_sessions=None):
        return TapeSession(
            session_id="test-sess",
            entries=entries or [],
            subagent_sessions=subagent_sessions or [],
            start_time="2026-03-09T10:00:00Z",
            end_time="2026-03-09T10:30:00Z",
        )

    def test_extracts_session_goal(self, tmp_path):
        session = self._make_session(
            entries=[TapeEntry(type="user", text_content="fix the login bug")]
        )
        obs = Observer(str(tmp_path), str(tmp_path / "mem"))
        results = obs.observe_session(session)
        goals = [o for o in results if "Session goal" in o.content]
        assert len(goals) == 1
        assert "fix the login bug" in goals[0].content

    def test_extracts_tool_errors(self, tmp_path):
        session = self._make_session(
            entries=[
                TapeEntry(
                    type="user",
                    timestamp="2026-03-09T10:05:00Z",
                    tool_results=[
                        ToolResult(
                            tool_use_id="tu-1",
                            content_summary="command not found",
                            is_error=True,
                        )
                    ],
                )
            ]
        )
        obs = Observer(str(tmp_path), str(tmp_path / "mem"))
        results = obs.observe_session(session)
        errors = [o for o in results if "Tool error" in o.content]
        assert len(errors) == 1
        assert errors[0].priority == "important"

    def test_extracts_tracebacks(self, tmp_path):
        session = self._make_session(
            entries=[
                TapeEntry(
                    type="assistant",
                    timestamp="2026-03-09T10:05:00Z",
                    text_content="I see an error:\nValueError: bad input\nLet me fix it.",
                )
            ]
        )
        obs = Observer(str(tmp_path), str(tmp_path / "mem"))
        results = obs.observe_session(session)
        tracebacks = [o for o in results if "Exception discussed" in o.content]
        assert len(tracebacks) == 1

    def test_extracts_file_creations(self, tmp_path):
        from tape_reader import ToolUse

        session = self._make_session(
            entries=[
                TapeEntry(
                    type="assistant",
                    timestamp="2026-03-09T10:05:00Z",
                    tool_uses=[
                        ToolUse(id="tu-1", name="Write", input_summary="/new_file.py")
                    ],
                )
            ]
        )
        obs = Observer(str(tmp_path), str(tmp_path / "mem"))
        results = obs.observe_session(session)
        files = [o for o in results if "File created" in o.content]
        assert len(files) == 1
        assert "/new_file.py" in files[0].content

    def test_extracts_subagent_count(self, tmp_path):
        session = self._make_session(
            subagent_sessions=[
                SubagentSession(agent_id="tu-a1"),
                SubagentSession(agent_id="tu-a2"),
            ]
        )
        obs = Observer(str(tmp_path), str(tmp_path / "mem"))
        results = obs.observe_session(session)
        subs = [o for o in results if "subagent" in o.content]
        assert len(subs) == 1
        assert "2" in subs[0].content

    def test_extracts_token_usage(self, tmp_path):
        session = self._make_session(
            entries=[
                TapeEntry(
                    type="assistant",
                    token_usage=TokenUsage(
                        input_tokens=1000,
                        output_tokens=200,
                        cache_read=800,
                    ),
                )
            ]
        )
        obs = Observer(str(tmp_path), str(tmp_path / "mem"))
        results = obs.observe_session(session)
        usage = [o for o in results if "Token usage" in o.content]
        assert len(usage) == 1
        assert "800 cache read" in usage[0].content

    def test_no_token_usage_when_zero(self, tmp_path):
        session = self._make_session(entries=[TapeEntry(type="system")])
        obs = Observer(str(tmp_path), str(tmp_path / "mem"))
        results = obs.observe_session(session)
        usage = [o for o in results if "Token usage" in o.content]
        assert len(usage) == 0

    def test_empty_session(self, tmp_path):
        session = self._make_session()
        obs = Observer(str(tmp_path), str(tmp_path / "mem"))
        results = obs.observe_session(session)
        assert len(results) == 0

    def test_write_tool_with_empty_summary_skipped(self, tmp_path):
        from tape_reader import ToolUse

        session = self._make_session(
            entries=[
                TapeEntry(
                    type="assistant",
                    tool_uses=[ToolUse(id="tu-1", name="Write", input_summary="")],
                )
            ]
        )
        obs = Observer(str(tmp_path), str(tmp_path / "mem"))
        results = obs.observe_session(session)
        files = [o for o in results if "File created" in o.content]
        assert len(files) == 0

    def test_non_write_tools_not_tracked(self, tmp_path):
        from tape_reader import ToolUse

        session = self._make_session(
            entries=[
                TapeEntry(
                    type="assistant",
                    tool_uses=[
                        ToolUse(id="tu-1", name="Read", input_summary="/some.py")
                    ],
                )
            ]
        )
        obs = Observer(str(tmp_path), str(tmp_path / "mem"))
        results = obs.observe_session(session)
        files = [o for o in results if "File created" in o.content]
        assert len(files) == 0


class TestClassifyPriority:
    def test_important_keywords(self, tmp_path):
        obs = Observer(str(tmp_path), str(tmp_path / "mem"))
        assert obs.classify_priority("Fixed a bug in login") == "important"
        assert obs.classify_priority("Error: connection failed") == "important"
        assert obs.classify_priority("crash on startup") == "important"
        assert obs.classify_priority("security vulnerability found") == "important"

    def test_possible_keywords(self, tmp_path):
        obs = Observer(str(tmp_path), str(tmp_path / "mem"))
        assert obs.classify_priority("test coverage added") == "possible"
        assert obs.classify_priority("refactor the module") == "possible"
        assert obs.classify_priority("update dependencies") == "possible"

    def test_informational_default(self, tmp_path):
        obs = Observer(str(tmp_path), str(tmp_path / "mem"))
        assert obs.classify_priority("Session started") == "informational"

    def test_custom_default(self, tmp_path):
        obs = Observer(str(tmp_path), str(tmp_path / "mem"))
        assert obs.classify_priority("nothing special", "possible") == "possible"

    def test_important_beats_possible(self, tmp_path):
        """When both important and possible keywords match, important wins."""
        obs = Observer(str(tmp_path), str(tmp_path / "mem"))
        assert obs.classify_priority("fix the test") == "important"


class TestWriteObservations:
    def test_writes_markdown_file(self, tmp_path):
        mem = tmp_path / "memory"
        obs = Observer(str(tmp_path), str(mem))
        observations = [
            Observation(
                referenced_time="2026-03-09T10:00:00Z",
                priority="important",
                content="Found a bug",
                source_session="abcdef12-3456",
            ),
            Observation(
                referenced_time="2026-03-09T11:00:00Z",
                priority="informational",
                content="Session started",
                source_session="abcdef12-3456",
            ),
        ]
        obs.write_observations(observations)

        content = (mem / "observations.md").read_text()
        assert "## 2026-03-09" in content
        assert "[important]" in content
        assert "[informational]" in content
        assert "Found a bug" in content
        assert "(session: abcdef12)" in content

    def test_appends_to_existing(self, tmp_path):
        mem = tmp_path / "memory"
        mem.mkdir()
        (mem / "observations.md").write_text("# Existing\n\n## 2026-03-08\n- old\n")

        obs = Observer(str(tmp_path), str(mem))
        obs.write_observations(
            [
                Observation(
                    referenced_time="2026-03-09T10:00:00Z",
                    priority="possible",
                    content="New thing",
                    source_session="sess1234-5678",
                ),
            ]
        )

        content = (mem / "observations.md").read_text()
        assert "# Existing" in content
        assert "## 2026-03-09" in content
        assert "New thing" in content

    def test_no_duplicate_date_headers(self, tmp_path):
        mem = tmp_path / "memory"
        mem.mkdir()
        (mem / "observations.md").write_text("## 2026-03-09\n- existing\n")

        obs = Observer(str(tmp_path), str(mem))
        obs.write_observations(
            [
                Observation(
                    referenced_time="2026-03-09T12:00:00Z",
                    priority="informational",
                    content="More stuff",
                    source_session="sess1234-5678",
                ),
            ]
        )

        content = (mem / "observations.md").read_text()
        assert content.count("## 2026-03-09") == 1

    def test_unknown_date(self, tmp_path):
        mem = tmp_path / "memory"
        obs = Observer(str(tmp_path), str(mem))
        obs.write_observations(
            [
                Observation(
                    referenced_time="",
                    priority="informational",
                    content="No date",
                    source_session="sess1234-5678",
                ),
            ]
        )

        content = (mem / "observations.md").read_text()
        assert "## unknown" in content

    def test_multiple_dates_sorted(self, tmp_path):
        mem = tmp_path / "memory"
        obs = Observer(str(tmp_path), str(mem))
        obs.write_observations(
            [
                Observation(
                    referenced_time="2026-03-10T10:00:00Z",
                    content="later",
                    source_session="sess1234-5678",
                ),
                Observation(
                    referenced_time="2026-03-08T10:00:00Z",
                    content="earlier",
                    source_session="sess1234-5678",
                ),
            ]
        )

        content = (mem / "observations.md").read_text()
        pos_08 = content.index("2026-03-08")
        pos_10 = content.index("2026-03-10")
        assert pos_08 < pos_10

    def test_creates_memory_dir(self, tmp_path):
        mem = tmp_path / "deep" / "nested" / "memory"
        obs = Observer(str(tmp_path), str(mem))
        obs.write_observations(
            [
                Observation(
                    referenced_time="2026-01-01T00:00:00Z",
                    content="test",
                    source_session="sess1234-5678",
                ),
            ]
        )
        assert (mem / "observations.md").exists()


class TestLoadState:
    def test_missing_file_returns_empty(self, tmp_path):
        obs = Observer(str(tmp_path), str(tmp_path / "mem"))
        assert obs.load_state() == {}

    def test_reads_existing_state(self, tmp_path):
        mem = tmp_path / "mem"
        mem.mkdir()
        (mem / "observer_state.json").write_text(
            json.dumps({"processed_sessions": ["a", "b"]})
        )
        obs = Observer(str(tmp_path), str(mem))
        state = obs.load_state()
        assert state["processed_sessions"] == ["a", "b"]


class TestSaveState:
    def test_writes_json(self, tmp_path):
        mem = tmp_path / "mem"
        obs = Observer(str(tmp_path), str(mem))
        obs.save_state({"processed_sessions": ["x"]})

        data = json.loads((mem / "observer_state.json").read_text())
        assert data["processed_sessions"] == ["x"]

    def test_creates_dir(self, tmp_path):
        mem = tmp_path / "new" / "dir"
        obs = Observer(str(tmp_path), str(mem))
        obs.save_state({"key": "val"})
        assert (mem / "observer_state.json").exists()


class TestRun:
    def test_end_to_end(self, tmp_path, make_tape_entry):
        proj = tmp_path / "project"
        proj.mkdir()
        mem = tmp_path / "memory"

        # Create a tape with user message and assistant error
        lines = [
            make_tape_entry(
                entry_type="user",
                text="fix the crash",
                session_id="sess-1",
                timestamp="2026-03-09T10:00:00Z",
            ),
            make_tape_entry(
                entry_type="assistant",
                text="I see the error",
                session_id="sess-1",
                timestamp="2026-03-09T10:01:00Z",
                usage={
                    "input_tokens": 500,
                    "output_tokens": 100,
                    "cache_creation_input_tokens": 0,
                    "cache_read_input_tokens": 400,
                },
            ),
        ]
        (proj / "sess-1.jsonl").write_text("\n".join(lines) + "\n")

        obs = Observer(str(proj), str(mem))
        results = obs.run()

        assert len(results) > 0
        assert (mem / "observations.md").exists()
        assert (mem / "observer_state.json").exists()

        # Running again should produce no new observations
        results2 = obs.run()
        assert len(results2) == 0

    def test_run_with_no_sessions(self, tmp_path):
        proj = tmp_path / "project"
        proj.mkdir()
        mem = tmp_path / "memory"

        obs = Observer(str(proj), str(mem))
        results = obs.run()
        assert results == []

    def test_run_updates_watermark(self, tmp_path, make_tape_entry):
        proj = tmp_path / "project"
        proj.mkdir()
        mem = tmp_path / "memory"

        lines = [
            make_tape_entry(entry_type="user", text="hello", session_id="s1"),
        ]
        (proj / "s1.jsonl").write_text("\n".join(lines) + "\n")

        obs = Observer(str(proj), str(mem))
        obs.run()

        state = obs.load_state()
        assert "s1" in state["processed_sessions"]

    def test_run_no_observations_no_write(self, tmp_path, make_tape_entry):
        """When observe_session returns empty, observations.md shouldn't be created."""
        proj = tmp_path / "project"
        proj.mkdir()
        mem = tmp_path / "memory"

        # Progress-only entry produces no observations
        raw = json.dumps({
            "type": "progress",
            "sessionId": "s1",
            "timestamp": "2026-01-01T00:00:00Z",
            "data": {"type": "hook"},
        })
        (proj / "s1.jsonl").write_text(raw + "\n")

        obs = Observer(str(proj), str(mem))
        results = obs.run()
        assert results == []
        assert not (mem / "observations.md").exists()
