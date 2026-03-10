"""CLI wrapper for the observational memory observer.

Usage:
    python3 scripts/observe_cli.py [--project-dir DIR] [--dry-run] [--session ID] [--reset]
"""

import argparse
import os
import sys
from pathlib import Path

from observer import Observer


def detect_project_dir() -> str:
    """Auto-detect Claude project dir from cwd.

    Converts /Users/x/code/pokemon -> ~/.claude/projects/-Users-x-code-pokemon/
    """
    cwd = os.getcwd()
    slug = cwd.replace("/", "-")
    if slug.startswith("-"):
        slug = slug  # keep leading dash
    return str(Path.home() / ".claude" / "projects" / slug)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Distill Claude Code tapes into observational memory"
    )
    parser.add_argument(
        "--project-dir",
        help="Override auto-detected Claude project directory",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print observations without writing to disk",
    )
    parser.add_argument(
        "--session",
        help="Process a single session ID only",
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Clear watermark and reprocess all sessions",
    )

    args = parser.parse_args(argv)

    project_dir = args.project_dir or detect_project_dir()
    memory_dir = str(Path(project_dir) / "memory")

    observer = Observer(project_dir=project_dir, memory_dir=memory_dir)

    if args.reset:
        if observer.state_path.exists():
            observer.state_path.unlink()
        print("Watermark cleared.")

    if args.session:
        session = observer.reader.read_session(args.session)
        observations = observer.observe_session(session)
    else:
        if args.dry_run:
            # In dry-run mode, get unprocessed and observe without writing
            sessions = observer.get_unprocessed_sessions()
            observations = []
            for sid in sessions:
                session = observer.reader.read_session(sid)
                observations.extend(observer.observe_session(session))
        else:
            observations = observer.run()

    if args.dry_run or args.session:
        for obs in observations:
            print(
                f"[{obs.priority}] {obs.content} "
                f"(session: {obs.source_session[:8]})"
            )
        print(f"\n{len(observations)} observation(s) found.")
    else:
        print(f"Wrote {len(observations)} observation(s) to {observer.observations_path}")


if __name__ == "__main__":
    main()
