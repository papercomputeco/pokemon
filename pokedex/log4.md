Model: GPT-5 Codex

# Log 4: 1000-Turn Sandbox Run Found a False Text-Box Signal

## Goal

Run the agent for 1000 turns in the `pokemon-agent` stereOS sandbox and check whether it could reach Professor Oak's lab and advance to starter selection.

## Environment

The run was executed inside the existing `pokemon-agent` sandbox, not in the local macOS shell.

Important environment notes:

- `pyboy`, `Pillow`, and `numpy` were installed in `~/venv`
- The ROM was present at `rom/Pokemon - Red Version (USA, Europe) (SGB Enhanced).gb`
- `LD_LIBRARY_PATH` was set to include `$HOME/.nix-profile/lib`

## Command Run

Inside the sandbox:

```bash
cd /workspace
export LD_LIBRARY_PATH=$HOME/.nix-profile/lib:$LD_LIBRARY_PATH
~/venv/bin/python3 scripts/agent.py "rom/Pokemon - Red Version (USA, Europe) (SGB Enhanced).gb" --max-turns 1000 --save-screenshots
```

## Result

The run completed all 1000 turns, but the agent never left Red's bedroom.

Final observed state:

- Map `38`
- Position `(3, 6)`
- Party count `0`
- Wins `0`

The final frame still showed the bedroom scene, matching the earlier stuck state.

## What the Logs Showed

The key signal from the runtime logs was that the agent chose `Action: a` for the entire overworld run.

Example pattern:

- `OVERWORLD | Map: 38 | Pos: (3, 6) | ... | Action: a | Stuck: 0`

That means the agent never even attempted directional movement after the intro.

## Root Cause Found

The current `text_box_active` check in `scripts/memory_reader.py` is using address `0xC4F2`.

That value stayed nonzero after the intro, even though no visible text box was on screen.

Live memory check after the intro:

```python
{
  'map': 38,
  'x': 3,
  'y': 6,
  'party': 0,
  'c4f2': 16,
  'd730': 0,
  'd736': 0,
}
```

Interpretation:

- `0xC4F2 == 16` caused the agent to think a text box was active
- `choose_overworld_action()` therefore kept returning `"a"`
- The overworld movement logic and stuck detection never had a chance to engage

So the new overworld loop from `log3` was not the immediate failure. The blocking issue is that the text-box memory signal is wrong for this context.

## Secondary Runtime Note

The run ended with the expected ROM save permission error:

- `PermissionError: ... .gb.ram`

This is the same host-mounted file permission issue seen earlier. It did not cause the movement failure during the run.

## Conclusion

The 1000-turn test did not reach Oak's lab, but it did identify the next concrete fix:

- remove or replace the `0xC4F2`-based `text_box_active` check

Until that signal is corrected, the agent will keep pressing `A` in the bedroom instead of attempting movement.

## Next Step

Update the overworld decision logic so it does not trust `0xC4F2` as a generic text-box flag, then rerun the sandbox test and verify that the first post-intro action is directional movement instead of `A`.
