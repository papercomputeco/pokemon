Model: Claude Opus 4.6

# Log 5: Text-Box Fix, Button API Discovery, and First Pallet Town Navigation

## Goal

Fix the false text-box signal identified in log4 so the agent attempts directional movement after the intro, then get the agent from Red's bedroom to Pallet Town and toward Professor Oak.

## Fixes Applied

### 1. Replaced `0xC4F2` text-box detection with `wd730` flags

**Root cause from log4:** Address `0xC4F2` is a position in the Game Boy's background tile map, not a game state flag. It held value `16` (a background tile index) after the intro, causing the agent to think a text box was permanently active.

**Fix in `memory_reader.py`:**

- Removed `ADDR_TEXT_BOX = 0xC4F2`
- Added `ADDR_WD730 = 0xD730` — pokered's game state register
- Added `_is_text_or_script_active()` method checking three `wd730` bits:
  - bit 1 (`0x02`): d-pad input disabled (text box or menu active)
  - bit 5 (`0x20`): simulated joypad (scripted NPC movement, e.g. Oak walking)
  - bit 6 (`0x40`): text/script display in progress (set by `DisplayTextID`)

**Result:** Agent now correctly detects text-box state. `wd730 == 0` after intro means no text active, and the agent attempts directional movement instead of pressing A.

### 2. Discovered PyBoy button API behavior

**Problem:** After fixing the text-box detection, the agent tried to move but position never changed.

**Root cause:** The `GameController.press()` method used `pyboy.button(button)` which internally calls `button_press → tick(1) → button_release` — a single-frame press. This is enough for A-button text advancement but not for tile walking, which requires sustained d-pad hold across multiple frames.

**PyBoy 2.x API:**
- `button(input, delay=1)` — press and auto-release after `delay` ticks (convenience method)
- `button_press(input)` — press and hold until `button_release()`
- `button_release(input)` — release a held button

**Key finding:** `button_press()`/`button_release()` do NOT work reliably in headless mode. Only `button(input, delay=N)` produces consistent results.

**Fix in `agent.py`:**

```python
def press(self, button, hold_frames=20, release_frames=10):
    self.pyboy.button(button, delay=hold_frames)
    for _ in range(release_frames):
        self.pyboy.tick()

def move(self, direction):
    self.press(direction, hold_frames=20, release_frames=8)
    self.wait(30)
```

### 3. Added oscillation detection for stuck navigation

**Problem:** The original stuck detection compared only consecutive positions. When the agent oscillated between two positions (e.g. bouncing off a table), the stuck counter reset every time position changed, so fallback directions never triggered.

**Fix:** Track last 8 positions. If the current position appears in recent history, increment the stuck counter instead of resetting it. This lets the direction rotation engage when the agent is oscillating.

### 4. Adjusted early-game navigation targets

**Problem:** Initial map 37 target `(3, 7)` with axis `"x"` sent the agent straight into the center table. Map 0 target `(8, 1)` with axis `"x"` sent the agent too far right into fences.

**Adjustments:**
- Map 37 (Red's house 1F): changed to `(2, 7)` with axis `"y"` — routes along the left wall to avoid the center table
- Map 0 (Pallet Town): changed to `(5, 1)` with axis `"x"` — centers the north approach on the Route 1 corridor

## Results

### Successful

- Agent exits the intro and attempts directional movement (not A-mashing)
- Agent walks from Red's bedroom (map 38) to house 1F (map 37) to Pallet Town (map 0)
- Agent navigates around Pallet Town, reaching positions across the map including near the north edge (y=2)

### Remaining Issues

1. **Door loop between map 37 and map 0:** The agent exits the house into Pallet Town at position `(3, 7)`, which is directly on the house doorstep. Navigation toward `(5, 1)` initially moves right (axis "x"), but on some runs the agent re-enters the house immediately before clearing the doorway. A "last map" cooldown or doorstep avoidance is needed.

2. **Intermittent movement failures:** With `button(delay=20)`, movement works ~80% of the time but occasionally fails for extended periods at certain positions. The agent can get permanently stuck at a position where no direction registers. This may be related to frame-alignment of the input with the game's movement polling.

3. **Oak interception not yet triggered:** The agent reaches Pallet Town and roams but hasn't walked into the specific trigger zone (tall grass at the north exit to Route 1). The navigation gets close (y=2) but the corridor at x=4-5 is narrow and the agent overshoots to x=8-10 where fences block.

4. **`wd730 == 0xa4` observed:** On some map 0 reads, `wd730` shows `0xa4` (bits 2, 5, 7) which was not expected. This may indicate a scripted event is running (bit 5 = simulated joypad) combined with other flags. Needs investigation.

## Next Steps

- Investigate the `wd730 = 0xa4` state — may indicate Oak's interception script has partially triggered
- Add door re-entry prevention (don't walk back into the building you just exited)
- Consider reading the pokered disassembly for `PalletTown.asm` script triggers to understand the exact Oak interception coordinates
- May need to handle Mom's dialog on first exit from the house (she speaks to you before you leave)
