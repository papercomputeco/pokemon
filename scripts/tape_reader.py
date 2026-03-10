"""Reader for Claude Code JSONL tape files.

Parses session tapes into structured Python objects for analysis.
Pure stdlib — no external dependencies.
"""

import json
import glob
from dataclasses import dataclass, field
from pathlib import Path
from typing import Generator


@dataclass
class ToolUse:
    """A tool invocation from an assistant message."""

    id: str = ""
    name: str = ""
    input_summary: str = ""


@dataclass
class ToolResult:
    """A tool result from a user message (tool_result content block)."""

    tool_use_id: str = ""
    content_summary: str = ""
    is_error: bool = False


@dataclass
class TokenUsage:
    """Token counts from an assistant response."""

    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation: int = 0
    cache_read: int = 0


@dataclass
class TapeEntry:
    """Single parsed line from a JSONL tape."""

    type: str = ""
    timestamp: str = ""
    session_id: str = ""
    text_content: str = ""
    tool_uses: list[ToolUse] = field(default_factory=list)
    tool_results: list[ToolResult] = field(default_factory=list)
    token_usage: TokenUsage = field(default_factory=TokenUsage)
    raw: dict = field(default_factory=dict)


@dataclass
class SubagentSession:
    """A subagent's tape entries, grouped by tool_use_id."""

    agent_id: str = ""
    entries: list[TapeEntry] = field(default_factory=list)


@dataclass
class TapeSession:
    """A fully parsed tape session."""

    session_id: str = ""
    entries: list[TapeEntry] = field(default_factory=list)
    subagent_sessions: list[SubagentSession] = field(default_factory=list)
    start_time: str = ""
    end_time: str = ""


class TapeReader:
    """Reads and parses Claude Code JSONL tape files."""

    def __init__(self, project_dir: str):
        self.project_dir = Path(project_dir)

    def list_sessions(self) -> list[str]:
        """Return session IDs from *.jsonl files in project_dir."""
        pattern = str(self.project_dir / "*.jsonl")
        paths = glob.glob(pattern)
        return [Path(p).stem for p in sorted(paths)]

    def read_session(self, session_id: str) -> TapeSession:
        """Parse a full session file into a TapeSession."""
        entries = list(self.iter_entries(session_id))
        session = TapeSession(session_id=session_id, entries=[])

        # Separate main session entries from subagent entries
        subagent_map: dict[str, list[TapeEntry]] = {}
        for entry in entries:
            parent_tool_id = entry.raw.get("parentToolUseID")
            if parent_tool_id:
                subagent_map.setdefault(parent_tool_id, []).append(entry)
            else:
                session.entries.append(entry)

        # Build subagent sessions
        for agent_id, sub_entries in subagent_map.items():
            session.subagent_sessions.append(
                SubagentSession(agent_id=agent_id, entries=sub_entries)
            )

        # Set time bounds from entries with timestamps
        timestamped = [e for e in entries if e.timestamp]
        if timestamped:
            session.start_time = timestamped[0].timestamp
            session.end_time = timestamped[-1].timestamp

        return session

    def iter_entries(self, session_id: str) -> Generator[TapeEntry, None, None]:
        """Lazy line-by-line generator over tape entries."""
        path = self.project_dir / f"{session_id}.jsonl"
        with open(path) as f:
            for line in f:
                line = line.strip()
                if line:
                    yield self.parse_entry(line)

    @staticmethod
    def parse_entry(line: str) -> TapeEntry:
        """Parse one JSONL line into a TapeEntry."""
        raw = json.loads(line)
        entry = TapeEntry(
            type=raw.get("type", ""),
            timestamp=raw.get("timestamp", ""),
            session_id=raw.get("sessionId", ""),
            raw=raw,
        )

        msg = raw.get("message", {})
        if not isinstance(msg, dict):
            # Some user entries have string messages
            if isinstance(msg, str):
                entry.text_content = msg
            return entry

        content = msg.get("content", [])

        if raw.get("type") == "assistant":
            # Extract usage
            usage = msg.get("usage", {})
            entry.token_usage = TokenUsage(
                input_tokens=usage.get("input_tokens", 0),
                output_tokens=usage.get("output_tokens", 0),
                cache_creation=usage.get("cache_creation_input_tokens", 0),
                cache_read=usage.get("cache_read_input_tokens", 0),
            )

            # Extract text and tool_use blocks
            if isinstance(content, list):
                texts = []
                for block in content:
                    if not isinstance(block, dict):
                        continue
                    if block.get("type") == "text":
                        texts.append(block.get("text", ""))
                    elif block.get("type") == "tool_use":
                        tool_input = block.get("input", {})
                        summary = _summarize_tool_input(
                            block.get("name", ""), tool_input
                        )
                        entry.tool_uses.append(
                            ToolUse(
                                id=block.get("id", ""),
                                name=block.get("name", ""),
                                input_summary=summary,
                            )
                        )
                entry.text_content = "\n".join(texts)

        elif raw.get("type") == "user":
            # User messages can have text content or tool_result blocks
            if isinstance(content, str):
                entry.text_content = content
            elif isinstance(content, list):
                texts = []
                for block in content:
                    if not isinstance(block, dict):
                        continue
                    if block.get("type") == "text":
                        texts.append(block.get("text", ""))
                    elif block.get("type") == "tool_result":
                        result_content = block.get("content", "")
                        if isinstance(result_content, list):
                            parts = [
                                p.get("text", "")
                                for p in result_content
                                if isinstance(p, dict)
                            ]
                            result_content = "\n".join(parts)
                        entry.tool_results.append(
                            ToolResult(
                                tool_use_id=block.get("tool_use_id", ""),
                                content_summary=result_content[:500],
                                is_error=bool(block.get("is_error", False)),
                            )
                        )
                entry.text_content = "\n".join(texts)

        elif raw.get("type") == "system":
            # System messages have content at top level or in message.content
            system_content = raw.get("content", "")
            if isinstance(system_content, str) and system_content:
                entry.text_content = system_content
            elif isinstance(content, str):
                entry.text_content = content

        return entry


def _summarize_tool_input(name: str, tool_input: dict) -> str:
    """Create a short summary of a tool invocation's input."""
    if not isinstance(tool_input, dict):
        return str(tool_input)[:200]

    if name == "Read":
        return tool_input.get("file_path", "")
    elif name == "Write":
        return tool_input.get("file_path", "")
    elif name == "Edit":
        return tool_input.get("file_path", "")
    elif name == "Bash":
        cmd = tool_input.get("command", "")
        return cmd[:200]
    elif name == "Grep":
        return f"pattern={tool_input.get('pattern', '')}"
    elif name == "Glob":
        return f"pattern={tool_input.get('pattern', '')}"
    elif name == "Agent":
        return tool_input.get("description", "")[:200]
    else:
        # Generic: show first key=value
        for key in ("prompt", "query", "description", "command", "file_path"):
            if key in tool_input:
                return f"{key}={str(tool_input[key])[:200]}"
        return str(tool_input)[:200]
