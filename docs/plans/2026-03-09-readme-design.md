# README Design

## Audience

Both human developers and AI agents. The README complements SKILL.md (which targets the stereOS agent harness) by providing a human-readable intro with enough structure for agents to orient themselves.

## Approach

Architecture-first. Lead with a system diagram so every detail that follows has context. Quickstart appears early but after the overview.

## Sections

1. **Header + one-liner** -- Project name, single sentence describing what it does.
2. **Architecture overview** -- ASCII diagram: PyBoy -> MemoryReader -> Strategy -> GameController, with Tapes proxy and stereOS VM boundary.
3. **Quickstart** -- `mb up` / `mb attach` for stereOS. Local alternative with python directly.
4. **How it works** -- Short paragraphs: game loop, memory reading, battle strategy, overworld navigation. Not a full reference (SKILL.md covers that).
5. **Tapes telemetry** -- What it captures, how to inspect sessions after a run.
6. **Project structure** -- File tree with one-line descriptions.
7. **Pokedex (logs)** -- Session logs and development notes live in `pokedex/`.
