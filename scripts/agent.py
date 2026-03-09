#!/usr/bin/env python3
"""
Pokemon Agent — Autonomous turn-based RPG player via PyBoy.

Runs headless. Reads game state from memory. Makes decisions.
Sends inputs. Logs everything. Designed for stereOS + Tapes.

Usage:
    python3 agent.py path/to/pokemon_red.gb [--strategy heuristic|llm]
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import os
from pathlib import Path

try:
    from pyboy import PyBoy
except ImportError:
    print("PyBoy not installed. Run: pip install pyboy")
    sys.exit(1)

try:
    from PIL import Image
except ImportError:
    Image = None

from memory_reader import MemoryReader, BattleState, OverworldState

# ---------------------------------------------------------------------------
# Type chart (simplified — super effective multipliers)
# ---------------------------------------------------------------------------

SCRIPT_DIR = Path(__file__).parent
TYPE_CHART_PATH = SCRIPT_DIR.parent / "references" / "type_chart.json"
ROUTES_PATH = SCRIPT_DIR.parent / "references" / "routes.json"

# Early-game scripted targets to get from Red's room to Oak's lab.
# Coords are taken from pret/pokered map object data.
EARLY_GAME_TARGETS = {
    # Map 38 handled by special exploration pattern below.
    37: {"name": "Red's house 1F", "target": (2, 7), "axis": "y"},
    0: {"name": "Pallet Town", "target": (11, 0), "axis": "x"},
}

# Systematic walk pattern for Red's bedroom (map 38).
# The staircase warp tile position varies with intro timing, so we
# sweep the room methodically: down the left wall, across the bottom,
# up the right wall, etc.
BEDROOM_PATTERN = [
    "down", "down", "left", "left", "left",  # sweep to bottom-left
    "down", "right", "right", "right", "right", "right",  # sweep bottom
    "up", "up", "up", "up",  # up the right wall
    "left", "left", "left", "left",  # across the top
    "down", "down", "right", "right",  # back center
]

# Interior maps (buildings). Used for door re-entry prevention.
INTERIOR_MAPS = {37, 38, 39, 40, 41}  # houses, lab, etc.

# Move ID → (name, type, power, accuracy)
# Subset of Gen 1 moves for demonstration
MOVE_DATA = {
    0x01: ("Pound", "normal", 40, 100),
    0x0A: ("Scratch", "normal", 40, 100),
    0x21: ("Tackle", "normal", 35, 95),
    0x2D: ("Ember", "fire", 40, 100),
    0x37: ("Water Gun", "water", 40, 100),
    0x49: ("Vine Whip", "grass", 35, 100),
    0x55: ("Thunderbolt", "electric", 95, 100),
    0x56: ("Thunder Wave", "electric", 0, 100),
    0x59: ("Thunder", "electric", 120, 70),
    0x3A: ("Ice Beam", "ice", 95, 100),
    0x3F: ("Flamethrower", "fire", 95, 100),
    0x39: ("Surf", "water", 95, 100),
    0x16: ("Razor Leaf", "grass", 55, 95),
    0x5D: ("Psychic", "psychic", 90, 100),
    0x1A: ("Body Slam", "normal", 85, 100),
    0x26: ("Earthquake", "ground", 100, 100),
    0x00: ("(No move)", "none", 0, 0),
}


def load_type_chart():
    """Load type effectiveness chart from JSON."""
    if TYPE_CHART_PATH.exists():
        with open(TYPE_CHART_PATH) as f:
            return json.load(f)
    # Fallback: minimal chart
    return {
        "fire": {"grass": 2.0, "water": 0.5, "fire": 0.5, "ice": 2.0},
        "water": {"fire": 2.0, "grass": 0.5, "water": 0.5, "ground": 2.0, "rock": 2.0},
        "grass": {"water": 2.0, "fire": 0.5, "grass": 0.5, "ground": 2.0, "rock": 2.0},
        "electric": {"water": 2.0, "grass": 0.5, "electric": 0.5, "ground": 0.0, "flying": 2.0},
        "ground": {"fire": 2.0, "electric": 2.0, "grass": 0.5, "flying": 0.0, "rock": 2.0},
        "ice": {"grass": 2.0, "ground": 2.0, "flying": 2.0, "dragon": 2.0, "fire": 0.5},
        "psychic": {"fighting": 2.0, "poison": 2.0, "psychic": 0.5},
        "normal": {"rock": 0.5, "ghost": 0.0},
    }


# ---------------------------------------------------------------------------
# Input helpers
# ---------------------------------------------------------------------------

class GameController:
    """Send inputs to PyBoy with proper frame timing."""

    def __init__(self, pyboy: PyBoy):
        self.pyboy = pyboy

    def press(self, button: str, hold_frames: int = 20, release_frames: int = 10):
        """Press and release a button with frame advance.

        Uses pyboy.button() which handles press+hold+release internally.
        button_press()/button_release() do not work reliably in headless mode.
        """
        self.pyboy.button(button, delay=hold_frames)
        for _ in range(release_frames):
            self.pyboy.tick()

    def wait(self, frames: int = 30):
        """Advance N frames without input."""
        for _ in range(frames):
            self.pyboy.tick()

    def move(self, direction: str):
        """Move a single tile in the overworld."""
        self.press(direction, hold_frames=20, release_frames=8)
        self.wait(30)

    def mash_a(self, times: int = 5, delay: int = 20):
        """Mash A to advance text boxes."""
        for _ in range(times):
            self.press("a")
            self.wait(delay)

    def navigate_menu(self, target_index: int, current_index: int = 0):
        """Move cursor to a menu item (assumes vertical menu)."""
        diff = target_index - current_index
        direction = "down" if diff > 0 else "up"
        for _ in range(abs(diff)):
            self.press(direction)
            self.wait(8)
        self.press("a")
        self.wait(20)


# ---------------------------------------------------------------------------
# Battle strategy
# ---------------------------------------------------------------------------

class BattleStrategy:
    """Heuristic-based battle decision engine."""

    def __init__(self, type_chart: dict):
        self.type_chart = type_chart

    def score_move(self, move_id: int, move_pp: int, enemy_type: str = "normal") -> float:
        """Score a move based on power, PP, and type effectiveness."""
        if move_pp <= 0:
            return -1.0
        if move_id not in MOVE_DATA:
            return 10.0  # Unknown move, give it a baseline

        name, move_type, power, accuracy = MOVE_DATA[move_id]
        if power == 0:
            return 1.0  # Status move — low priority for grinding

        effectiveness = 1.0
        if move_type in self.type_chart:
            effectiveness = self.type_chart[move_type].get(enemy_type, 1.0)

        return power * (accuracy / 100.0) * effectiveness

    def choose_action(self, battle: BattleState) -> dict:
        """
        Decide what to do in battle.

        Returns:
            {"action": "fight", "move_index": 0-3}
            {"action": "item", "item": "potion"}
            {"action": "switch", "slot": 1-5}
            {"action": "run"}
        """
        # Low HP — try to run only if critically low in wild battles.
        # Otherwise keep fighting — running wastes turns and can fail.
        hp_ratio = battle.player_hp / max(battle.player_max_hp, 1)
        if hp_ratio < 0.1 and battle.battle_type == 1:  # Wild, critical
            return {"action": "run"}

        # Score all moves and pick the best
        moves = [
            (i, self.score_move(battle.moves[i], battle.move_pp[i]))
            for i in range(4)
            if battle.moves[i] != 0x00
        ]

        if not moves or all(score < 0 for _, score in moves):
            # No PP left — Struggle will auto-trigger, just press FIGHT
            return {"action": "fight", "move_index": 0}

        best_index, best_score = max(moves, key=lambda x: x[1])
        return {"action": "fight", "move_index": best_index}


# ---------------------------------------------------------------------------
# Overworld navigation
# ---------------------------------------------------------------------------

class Navigator:
    """Simple overworld movement."""

    def __init__(self, routes: dict):
        self.routes = routes
        self.current_waypoint = 0
        self.current_map = None

    def _add_direction(self, directions: list[str], direction: str | None):
        """Append a direction once while preserving order."""
        if direction and direction not in directions:
            directions.append(direction)

    def _direction_toward_target(
        self,
        state: OverworldState,
        target_x: int,
        target_y: int,
        axis_preference: str = "x",
        stuck_turns: int = 0,
    ) -> str | None:
        """Choose a movement direction and rotate alternatives when blocked."""
        horizontal = None
        vertical = None

        if state.x < target_x:
            horizontal = "right"
        elif state.x > target_x:
            horizontal = "left"

        if state.y < target_y:
            vertical = "down"
        elif state.y > target_y:
            vertical = "up"

        ordered: list[str] = []
        primary = [horizontal, vertical] if axis_preference == "x" else [vertical, horizontal]
        secondary = [vertical, horizontal] if axis_preference == "x" else [horizontal, vertical]

        for direction in primary:
            self._add_direction(ordered, direction)
        for direction in secondary:
            self._add_direction(ordered, direction)
        for direction in ("up", "right", "down", "left"):
            self._add_direction(ordered, direction)

        if not ordered:
            return None
        return ordered[stuck_turns % len(ordered)]

    def next_direction(self, state: OverworldState, turn: int = 0, stuck_turns: int = 0) -> str | None:
        """Get the next direction to move based on current position and route plan."""
        map_key = str(state.map_id)

        # Reset waypoint index on map change
        if map_key != self.current_map:
            self.current_map = map_key
            self.current_waypoint = 0

        special_target = EARLY_GAME_TARGETS.get(state.map_id)
        if special_target:
            target_x, target_y = special_target["target"]
            return self._direction_toward_target(
                state,
                target_x,
                target_y,
                axis_preference=special_target.get("axis", "x"),
                stuck_turns=stuck_turns,
            )

        if map_key not in self.routes:
            # No route data — cycle directions to explore and find exits
            directions = ["down", "right", "down", "left", "up", "down"]
            return directions[turn % len(directions)]

        route = self.routes[map_key]
        waypoints = route["waypoints"] if isinstance(route, dict) and "waypoints" in route else route
        if self.current_waypoint >= len(waypoints):
            return None  # Route complete

        target = waypoints[self.current_waypoint]
        tx, ty = target["x"], target["y"]

        if state.x == tx and state.y == ty:
            self.current_waypoint += 1
            return self.next_direction(state, turn=turn, stuck_turns=stuck_turns)

        return self._direction_toward_target(state, tx, ty, stuck_turns=stuck_turns)


# ---------------------------------------------------------------------------
# Main agent loop
# ---------------------------------------------------------------------------

class PokemonAgent:
    """Autonomous Pokemon player."""

    def __init__(self, rom_path: str, strategy: str = "heuristic", screenshots: bool = False):
        self.rom_path = rom_path
        self.pyboy = PyBoy(rom_path, window="null")
        self.controller = GameController(self.pyboy)
        self.memory = MemoryReader(self.pyboy)
        self.type_chart = load_type_chart()
        self.battle_strategy = BattleStrategy(self.type_chart)
        self.turn_count = 0
        self.battles_won = 0
        self.screenshots = screenshots
        self.last_overworld_state: OverworldState | None = None
        self.last_overworld_action: str | None = None
        self.stuck_turns = 0
        self.recent_positions: list[tuple[int, int, int]] = []
        self.maps_visited: set[int] = set()
        self.events: list[str] = []

        # Dialogue tracking
        self.dialogue_history: list[dict] = []
        self.last_read_text: str = ""

        # Door re-entry prevention: after exiting a building, walk
        # away from the door for a few turns before resuming navigation.
        self.door_cooldown: int = 0

        # Screenshot output directory — one folder per session so previous
        # runs are never overwritten (useful as training data).
        session_ts = time.strftime("%Y-%m-%d_%H%M%S")
        self.frames_dir = SCRIPT_DIR.parent / "frames" / session_ts
        if self.screenshots:
            self.frames_dir.mkdir(parents=True, exist_ok=True)

        # Pokedex log directory
        self.pokedex_dir = SCRIPT_DIR.parent / "pokedex"
        self.pokedex_dir.mkdir(parents=True, exist_ok=True)

        # Load routes
        routes = {}
        if ROUTES_PATH.exists():
            with open(ROUTES_PATH) as f:
                routes = json.load(f)
        self.navigator = Navigator(routes)

        print(f"[agent] Loaded ROM: {rom_path}")
        print(f"[agent] Strategy: {strategy}")
        print(f"[agent] Running headless — no display")

    def update_overworld_progress(self, state: OverworldState):
        """Track whether the last overworld action moved the player."""
        pos = (state.map_id, state.x, state.y)

        self.maps_visited.add(state.map_id)

        if self.last_overworld_state is None:
            self.recent_positions.append(pos)
            return

        if state.map_id != self.last_overworld_state.map_id:
            old_map = self.last_overworld_state.map_id
            self.stuck_turns = 0
            self.recent_positions.clear()
            self.recent_positions.append(pos)
            self.log(
                f"MAP CHANGE | {old_map} -> {state.map_id} | "
                f"Pos: ({state.x}, {state.y})"
            )
            # After exiting a building, walk away from the door to avoid
            # re-entering immediately.
            if old_map in INTERIOR_MAPS and state.map_id not in INTERIOR_MAPS:
                self.door_cooldown = 3
            return

        # Detect oscillation: if current position was visited recently,
        # increment stuck counter so the navigator tries alternate directions.
        if pos in self.recent_positions:
            self.stuck_turns += 1
        else:
            self.stuck_turns = 0

        self.recent_positions.append(pos)
        if len(self.recent_positions) > 8:
            self.recent_positions.pop(0)

        if self.stuck_turns in {2, 5, 10}:
            self.log(
                f"STUCK | Map: {state.map_id} | Pos: ({state.x}, {state.y}) | "
                f"Last move: {self.last_overworld_action} | Streak: {self.stuck_turns}"
            )

    def choose_overworld_action(self, state: OverworldState) -> str:
        """Pick the next overworld action."""
        if state.text_box_active:
            return "a"

        # Oak's Lab post-starter: dismiss dialogues with B (selects "No"
        # on nickname prompt) and walk south to exit via the center aisle.
        # Bookshelves line the walls, so the walkable aisle is ~x=4-5.
        if state.map_id == 40 and state.party_count > 0:
            phase = self.turn_count % 4
            if phase == 0:
                return "b"  # dismiss prompts / select No
            if phase == 1:
                return "a"  # advance dialogue
            # Navigate to center aisle, then south to exit.
            if state.x > 5:
                return "left"
            if state.y < 11:
                return "down"
            return "down"

        # Oak's Lab pre-starter: walk south of the Pokeball table, face up, press A.
        # The table is around (6-8, 3). Approach from y=4 facing north.
        if state.map_id == 40 and state.party_count == 0:
            phase = self.turn_count % 4
            if phase == 0:
                return "a"  # advance dialogue / confirm selection
            # Step 1: get south of the table (y >= 4).
            if state.y < 4:
                return "down"
            # Step 2: get in front of a Pokeball (x around 6-8).
            if state.x < 6:
                return "right"
            # Step 3: face the table and interact.
            if phase == 1:
                return "up"  # face the table
            return "a"  # press A on the Pokeball

        # Walk away from building doors after exiting to prevent re-entry.
        if self.door_cooldown > 0:
            self.door_cooldown -= 1
            return "down"

        # Red's bedroom: use a systematic sweep pattern since we don't
        # know the exact warp tile position.
        if state.map_id == 38:
            return BEDROOM_PATTERN[self.turn_count % len(BEDROOM_PATTERN)]

        # Blue's House (map 39): exit immediately by walking south.
        if state.map_id == 39:
            return "down"

        # Pallet Town: two-phase navigation to reach Route 1.
        # The tree line blocks most of the north edge; the gap is around
        # x=10-11 (where Oak intercepts at the tall grass).
        # Blue's House door is at x=13, so stay at x<=11 to avoid it.
        if state.map_id == 0:
            # Periodically press A to talk to nearby NPCs.
            if self.turn_count % 7 == 0:
                return "a"
            # If too far east (past the target area), come back left
            # to avoid Blue's House door at x=13.
            if state.x > 11:
                return "left"
            # If very stuck, try cycling directions (favor up and left).
            if self.stuck_turns >= 15:
                fallback = ["up", "left", "up", "right"]
                return fallback[self.turn_count % 4]
            # Phase 1: head east to the gap area (x=10-11).
            if state.x < 10:
                if self.stuck_turns >= 5:
                    return "up" if self.turn_count % 2 == 0 else "right"
                return "right"
            # Phase 2: head north through the gap to Route 1.
            if state.y > 0:
                if self.stuck_turns >= 5:
                    # Alternate left/up to avoid getting stuck on fences.
                    return "left" if self.turn_count % 2 == 0 else "up"
                return "up"
            return "up"

        # Periodically press A to talk to nearby NPCs (every 7 turns in
        # outdoor maps only — no point in interior rooms).
        if (self.turn_count % 7 == 0
                and state.map_id not in INTERIOR_MAPS
                and state.map_id in EARLY_GAME_TARGETS):
            return "a"

        # If stuck for 20+ turns, cycle through all 4 directions.
        # Use only turn_count (not stuck_turns) since both increment
        # together and their sum stays even-only, skipping left/right.
        if self.stuck_turns >= 20:
            fallback = ["down", "left", "up", "right"]
            return fallback[self.turn_count % 4]

        direction = self.navigator.next_direction(
            state,
            turn=self.turn_count,
            stuck_turns=self.stuck_turns,
        )
        return direction or "a"

    def log(self, msg: str):
        """Structured log line for Tapes to capture."""
        timestamp = time.strftime("%H:%M:%S")
        line = f"[{timestamp}] {msg}"
        print(line, flush=True)
        self.events.append(line)

    # Map ID → human-readable name
    MAP_NAMES = {
        0: "Pallet Town",
        12: "Route 1",
        1: "Viridian City",
        13: "Route 2",
        51: "Viridian Forest",
        2: "Pewter City",
        37: "Red's House 1F",
        38: "Red's Bedroom",
        39: "Blue's House",
        40: "Oak's Lab",
        41: "Agatha's House",
    }

    def _map_name(self, map_id: int) -> str:
        return self.MAP_NAMES.get(map_id, f"Unknown Area (Map {map_id})")

    def write_pokedex_entry(self):
        """Write a narrative session log to the pokedex directory."""
        final_state = self.memory.read_overworld_state()
        timestamp = time.strftime("%Y-%m-%d_%H%M%S")

        existing = list(self.pokedex_dir.glob("log*.md"))
        next_num = len(existing) + 1
        path = self.pokedex_dir / f"log{next_num}.md"

        # Gather event counts
        map_changes = [e for e in self.events if "MAP CHANGE" in e]
        stuck_events = [e for e in self.events if "STUCK" in e]
        battle_events = [e for e in self.events if "BATTLE" in e]
        dialogue_events = [e for e in self.events if "DIALOGUE" in e]

        # Build the narrative journey: which maps were visited in order
        journey = []
        for e in map_changes:
            # Extract "old -> new" from "MAP CHANGE | old -> new | Pos: (x, y)"
            try:
                parts = e.split("MAP CHANGE | ")[1]
                ids = parts.split(" | ")[0]
                old_id, new_id = [int(x.strip()) for x in ids.split("->")]
                journey.append(new_id)
            except (IndexError, ValueError):
                pass

        # Collect unique dialogue snippets (deduplicated, full lines only)
        key_dialogues = []
        seen_texts = set()
        for entry in self.dialogue_history:
            text = entry["text"]
            clean = text.replace("<PLAYER>", "RED").replace("<RIVAL>", "RIVAL").replace("POKe", "POKE")
            # Keep only substantial, complete-looking lines
            if len(clean) >= 15 and clean not in seen_texts and not clean.startswith("9"):
                seen_texts.add(clean)
                key_dialogues.append({
                    "text": clean,
                    "map": entry["map_id"],
                    "turn": entry["turn"],
                })

        # Determine what milestones were hit
        got_starter = any("received a CHARMANDER" in e or "received a SQUIRTLE" in e
                         or "received a BULBASAUR" in e for e in self.events)
        won_rival = self.battles_won > 0
        reached_route1 = 12 in self.maps_visited
        reached_viridian = 1 in self.maps_visited
        met_oak = any("OAK:" in e for e in self.events)

        # Identify where the agent got stuck (most common stuck position)
        stuck_positions = {}
        for e in stuck_events:
            try:
                pos_part = e.split("Pos: ")[1].split(" |")[0]
                stuck_positions[pos_part] = stuck_positions.get(pos_part, 0) + 1
            except IndexError:
                pass
        worst_stuck = max(stuck_positions.items(), key=lambda x: x[1]) if stuck_positions else None

        # --- Write the narrative ---
        lines = []
        lines.append(f"Model: Claude Opus 4.6")
        lines.append("")
        lines.append(f"# Log {next_num}: {self._describe_session_title(final_state, got_starter, reached_route1)}")
        lines.append("")

        # Goal section
        lines.append("## Goal")
        lines.append("")
        lines.append(self._describe_goal(got_starter, reached_route1))
        lines.append("")

        # Journey section
        lines.append("## What Happened")
        lines.append("")
        lines.extend(self._describe_journey(journey, final_state, got_starter, won_rival,
                                            met_oak, reached_route1))
        lines.append("")

        # Key dialogues
        if key_dialogues:
            lines.append("## Key NPC Dialogue")
            lines.append("")
            for d in key_dialogues[:15]:
                map_name = self._map_name(d["map"])
                lines.append(f"- **Turn {d['turn']}** ({map_name}): \"{d['text']}\"")
            lines.append("")

        # What the agent learned
        lines.append("## What the Agent Learned")
        lines.append("")
        lines.extend(self._describe_learnings(stuck_events, worst_stuck, final_state,
                                               got_starter, reached_route1))
        lines.append("")

        # Session stats (compact)
        lines.append("## Session Stats")
        lines.append("")
        lines.append(f"- **Turns:** {self.turn_count}")
        lines.append(f"- **Maps visited:** {', '.join(self._map_name(m) for m in sorted(self.maps_visited))}")
        lines.append(f"- **Battles won:** {self.battles_won}")
        lines.append(f"- **Final position:** {self._map_name(final_state.map_id)} ({final_state.x}, {final_state.y})")
        lines.append(f"- **Badges:** {final_state.badges} | **Party:** {final_state.party_count}")
        lines.append(f"- **Map transitions:** {len(map_changes)} | **Stuck events:** {len(stuck_events)} | **Dialogues:** {len(self.dialogue_history)}")
        lines.append("")

        # Next steps
        lines.append("## Next Steps")
        lines.append("")
        lines.extend(self._describe_next_steps(final_state, got_starter, reached_route1,
                                                worst_stuck))
        lines.append("")

        path.write_text("\n".join(lines))
        self.log(f"POKEDEX | Wrote {path}")

    def _describe_session_title(self, state: OverworldState, got_starter: bool,
                                 reached_route1: bool) -> str:
        if reached_route1:
            return "First Steps on Route 1"
        if got_starter:
            return "Starter Obtained, Searching for Route 1"
        if 40 in self.maps_visited:
            return "Reached Oak's Lab"
        if 0 in self.maps_visited:
            return "Exploring Pallet Town"
        return "Navigating the Early Game"

    def _describe_goal(self, got_starter: bool, reached_route1: bool) -> str:
        if got_starter and not reached_route1:
            return ("With a starter Pokemon in hand, the goal this session was to exit "
                    "Pallet Town north through the tree line gap and reach Route 1.")
        if not got_starter:
            return ("Navigate from Red's bedroom through the opening sequence: exit the house, "
                    "reach the tall grass to trigger Professor Oak, get escorted to his lab, "
                    "and receive a starter Pokemon.")
        return "Continue north through Route 1 toward Viridian City."

    def _describe_journey(self, journey: list, state: OverworldState, got_starter: bool,
                          won_rival: bool, met_oak: bool, reached_route1: bool) -> list:
        lines = []
        if 38 in self.maps_visited:
            lines.append("The agent woke up in Red's bedroom and navigated downstairs using "
                         "a systematic sweep pattern to find the staircase warp tile.")
        if 37 in self.maps_visited and 0 in self.maps_visited:
            lines.append("It exited the house into Pallet Town and began heading north.")
        if met_oak and not got_starter:
            lines.append("Professor Oak intercepted the agent near the tall grass at the "
                         "north edge of town, warning about wild Pokemon, and escorted it "
                         "to his lab.")
        if met_oak and got_starter:
            lines.append("Professor Oak intercepted the agent near the tall grass and "
                         "escorted it to his lab. Oak offered three starter Pokemon, "
                         "and the agent chose Charmander.")
        if won_rival:
            lines.append("The rival challenged the agent to a battle and won, "
                         "then exited the lab.")
        if got_starter and not reached_route1:
            lines.append(f"Back in Pallet Town, the agent attempted to find the exit "
                         f"north to Route 1. It ended at position ({state.x}, {state.y}) "
                         f"in {self._map_name(state.map_id)}.")
        if reached_route1:
            lines.append("The agent successfully exited Pallet Town and reached Route 1.")
            wild_wins = self.battles_won - (1 if won_rival else 0)
            if wild_wins > 0:
                lines.append(f"It battled {wild_wins} wild Pokemon on Route 1, "
                             f"gaining experience and leveling up.")
            if 1 in self.maps_visited:
                lines.append("It continued north and reached Viridian City.")
        if not lines:
            lines.append(f"The agent explored {len(self.maps_visited)} areas over "
                         f"{self.turn_count} turns, ending at {self._map_name(state.map_id)}.")
        return lines

    def _describe_learnings(self, stuck_events: list, worst_stuck: tuple | None,
                            state: OverworldState, got_starter: bool,
                            reached_route1: bool) -> list:
        lines = []
        if len(stuck_events) > 50:
            lines.append(f"Navigation remains a challenge: the agent triggered "
                         f"{len(stuck_events)} stuck events this session.")
        elif len(stuck_events) > 10:
            lines.append(f"The agent hit {len(stuck_events)} stuck events but recovered "
                         f"from most using direction rotation fallbacks.")
        else:
            lines.append("Navigation was smooth with minimal stuck events.")

        if worst_stuck:
            pos, count = worst_stuck
            lines.append(f"The worst bottleneck was at position {pos} with "
                         f"{count} stuck events — likely a collision with trees or fences.")

        if got_starter and not reached_route1:
            lines.append("The tree line at the north edge of Pallet Town blocks most "
                         "direct paths. The passable gap to Route 1 appears to be on the "
                         "east side of town around x=10-11.")

        if self.dialogue_history:
            lines.append(f"The agent captured {len(self.dialogue_history)} dialogue "
                         f"fragments from NPCs, building context about the game world.")

        return lines

    def _describe_next_steps(self, state: OverworldState, got_starter: bool,
                             reached_route1: bool, worst_stuck: tuple | None) -> list:
        lines = []
        if not got_starter:
            lines.append("- Complete the opening sequence: trigger Oak, get a starter Pokemon")
        elif not reached_route1:
            lines.append("- Find the gap in the Pallet Town tree line to exit north to Route 1")
            if worst_stuck:
                lines.append(f"- Address navigation bottleneck at {worst_stuck[0]}")
        else:
            lines.append("- Navigate Route 1 north toward Viridian City")
            lines.append("- Battle wild Pokemon for experience along the way")
            lines.append("- Reach Viridian City Pokemon Center to heal")
        lines.append("- Continue capturing NPC dialogue for game world context")
        return lines

    def take_screenshot(self):
        """Save current frame as turn{N}.png."""
        if not self.screenshots or Image is None:
            return
        path = self.frames_dir / f"turn{self.turn_count}.png"
        img = Image.fromarray(self.pyboy.screen.ndarray)
        img.save(path)
        self.log(f"SCREENSHOT | {path}")

    def run_battle_turn(self):
        """Execute one battle turn."""
        battle = self.memory.read_battle_state()
        action = self.battle_strategy.choose_action(battle)

        self.log(
            f"BATTLE | Player HP: {battle.player_hp}/{battle.player_max_hp} | "
            f"Enemy HP: {battle.enemy_hp}/{battle.enemy_max_hp} | "
            f"Action: {action}"
        )

        if action["action"] == "fight":
            # Navigate to FIGHT menu
            self.controller.press("a")  # Select FIGHT
            self.controller.wait(20)
            # Select move
            self.controller.navigate_menu(action["move_index"])
            self.controller.wait(60)  # Wait for attack animation
            self.controller.mash_a(3)  # Clear text boxes

        elif action["action"] == "run":
            # Battle menu is 2x2: FIGHT BAG / PKMN RUN
            # RUN is bottom-right: press down once, right once, then A.
            self.controller.press("down")
            self.controller.wait(8)
            self.controller.press("right")
            self.controller.wait(8)
            self.controller.press("a")
            self.controller.wait(40)
            self.controller.mash_a(3)

        elif action["action"] == "item":
            # Navigate to BAG (index 1 in battle menu)
            self.controller.navigate_menu(1)
            self.controller.wait(20)
            # Select first healing item (simplified)
            self.controller.press("a")
            self.controller.wait(40)
            self.controller.mash_a(3)

        elif action["action"] == "switch":
            # Navigate to POKEMON (index 2 in battle menu)
            self.controller.navigate_menu(2)
            self.controller.wait(20)
            self.controller.navigate_menu(action.get("slot", 1))
            self.controller.wait(40)
            self.controller.mash_a(3)

        self.turn_count += 1

    def run_overworld(self):
        """Move in the overworld."""
        state = self.memory.read_overworld_state()
        self.update_overworld_progress(state)

        # Read dialogue text.  Always attempt reading the screen — wd730
        # doesn't catch every text state (e.g. Oak's lab scripted scenes).
        text = self.memory.read_screen_text()
        # Filter out garbage: name-plate artefacts and HUD noise that
        # appear when reading the tile map without a text-box gate.
        clean = text.replace("<PLAYER>", "").replace("<RIVAL>", "").replace("POKe", "").strip()
        is_real_dialogue = len(clean) >= 4
        if text and text != self.last_read_text and is_real_dialogue:
            self.last_read_text = text
            self.dialogue_history.append({
                "text": text,
                "map_id": state.map_id,
                "pos": (state.x, state.y),
                "turn": self.turn_count,
            })
            self.log(f"DIALOGUE | Map {state.map_id} | {text}")
            self._process_dialogue_hints(text, state)
        elif not text:
            self.last_read_text = ""

        action = self.choose_overworld_action(state)

        if action in {"up", "down", "left", "right"}:
            # Use longer holds when stuck — helps with frame-alignment issues.
            if self.stuck_turns > 10:
                self.controller.press(action, hold_frames=30, release_frames=10)
                self.controller.wait(40)
            else:
                self.controller.move(action)
        elif action == "b":
            self.controller.press("b", hold_frames=20, release_frames=12)
            self.controller.wait(24)
        else:
            self.controller.press("a", hold_frames=20, release_frames=12)
            self.controller.wait(24)

        # Log position every 50 steps (more frequent for debugging).
        if self.turn_count % 50 == 0:
            d730 = self.memory._read(0xD730)
            self.log(
                f"OVERWORLD | Map: {state.map_id} | "
                f"Pos: ({state.x}, {state.y}) | "
                f"Badges: {state.badges} | "
                f"Party: {state.party_count} | "
                f"Action: {action} | "
                f"Stuck: {self.stuck_turns} | "
                f"wd730=0x{d730:02x}"
            )

        self.last_overworld_state = state
        self.last_overworld_action = action

    def _process_dialogue_hints(self, text: str, state: OverworldState):
        """Extract navigation hints from NPC dialogue."""
        lower = text.lower()

        # Hints about Oak's lab location
        if any(kw in lower for kw in ("lab", "laboratory", "professor", "oak")):
            self.log(f"HINT | Dialogue mentions Oak/lab — prioritizing lab approach")
            # Oak's lab is at the south end of Pallet Town; but the trigger
            # to meet Oak is at the north exit (tall grass).  Keep heading
            # north so Oak intercepts us.

        if any(kw in lower for kw in ("grass", "pokemon", "dangerous", "wild")):
            self.log(f"HINT | Dialogue mentions grass/pokemon — heading north to trigger Oak")

    def run(self, max_turns: int = 100_000):
        """Main agent loop."""
        self.log("Agent starting. Advancing through intro...")

        # Advance through title screen (needs ~1500 frames to reach "Press Start")
        self.controller.wait(1500)
        self.controller.press("start")
        self.controller.wait(60)

        # Mash through Oak's entire intro, name selection, rival naming.
        # Need long frame waits — the game has slow text scroll and animations.
        for i in range(700):
            self.controller.press("a")
            self.controller.wait(30)

        # Extra: press Start then B to clear any lingering menu state,
        # then wait for the game to settle.
        self.controller.press("start")
        self.controller.wait(30)
        self.controller.press("b")
        self.controller.wait(60)

        d730 = self.memory._read(0xD730)
        self.log(f"Intro complete. wd730=0x{d730:02x}. Entering game loop.")

        for _ in range(max_turns):
            battle = self.memory.read_battle_state()

            if battle.battle_type > 0:
                self.run_battle_turn()

                # Check if battle ended
                self.controller.wait(10)
                new_battle = self.memory.read_battle_state()
                if new_battle.battle_type == 0:
                    self.battles_won += 1
                    self.log(f"Battle ended. Total wins: {self.battles_won}")
            else:
                self.run_overworld()
                self.turn_count += 1

            if self.turn_count % 50 == 0:
                self.take_screenshot()

        self.log(f"Session complete. Turns: {self.turn_count} | Wins: {self.battles_won}")
        self.write_pokedex_entry()
        try:
            self.pyboy.stop()
        except PermissionError:
            pass  # ROM save file write fails on read-only mounts


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Pokemon Agent — autonomous RPG player")
    parser.add_argument("rom", help="Path to ROM file (.gb or .gbc)")
    parser.add_argument(
        "--strategy",
        choices=["heuristic", "llm"],
        default="heuristic",
        help="Decision strategy (default: heuristic)",
    )
    parser.add_argument(
        "--max-turns",
        type=int,
        default=100_000,
        help="Maximum turns before stopping (default: 100000)",
    )
    parser.add_argument(
        "--save-screenshots",
        action="store_true",
        help="Save periodic screenshots to ./frames/",
    )
    args = parser.parse_args()

    if not Path(args.rom).exists():
        print(f"ROM not found: {args.rom}")
        sys.exit(1)

    agent = PokemonAgent(args.rom, strategy=args.strategy, screenshots=args.save_screenshots)
    agent.run(max_turns=args.max_turns)


if __name__ == "__main__":
    main()
