"""
Memory Reader — Extract game state from PyBoy emulator memory.

Addresses are for Pokemon Red/Blue (US release).
Swap out the address maps for other games.
"""

from dataclasses import dataclass, field
from typing import List


# Pokemon Red/Blue character encoding (tile index → character).
# Font tiles are arranged so the tile index matches the character code.
POKEMON_CHAR_MAP = {
    0x7F: ' ',
    # Uppercase A-Z (0x80-0x99)
    **{0x80 + i: chr(ord('A') + i) for i in range(26)},
    0x9A: '(', 0x9B: ')', 0x9C: ':', 0x9D: ';', 0x9E: '[', 0x9F: ']',
    # Lowercase a-z (0xA0-0xB9)
    **{0xA0 + i: chr(ord('a') + i) for i in range(26)},
    0xBA: 'e',  # é
    0xBB: "'d", 0xBC: "'l", 0xBD: "'s", 0xBE: "'t", 0xBF: "'v",
    0xE0: "'", 0xE3: '-', 0xE6: '?', 0xE7: '!', 0xE8: '.',
    0xF3: '/', 0xF4: ',',
    # Digits 0-9 (0xF6-0xFF)
    **{0xF6 + i: str(i) for i in range(10)},
    # Special tokens
    0x54: 'POKe',
    0x52: '<PLAYER>',
    0x53: '<RIVAL>',
}


@dataclass
class BattleState:
    """Current battle context."""
    battle_type: int = 0        # 0=none, 1=wild, 2=trainer
    enemy_hp: int = 0
    enemy_max_hp: int = 0
    enemy_level: int = 0
    enemy_species: int = 0
    player_hp: int = 0
    player_max_hp: int = 0
    player_level: int = 0
    player_species: int = 0
    moves: List[int] = field(default_factory=lambda: [0, 0, 0, 0])
    move_pp: List[int] = field(default_factory=lambda: [0, 0, 0, 0])
    party_count: int = 0
    party_hp: List[int] = field(default_factory=list)


@dataclass
class OverworldState:
    """Current overworld context."""
    map_id: int = 0
    x: int = 0
    y: int = 0
    badges: int = 0
    party_count: int = 0
    party_hp: List[int] = field(default_factory=list)
    money: int = 0
    text_box_active: bool = False


class MemoryReader:
    """
    Read game state from PyBoy memory.

    Pokemon Red/Blue memory map:
    https://datacrystal.romhacking.net/wiki/Pok%C3%A9mon_Red/Blue:RAM_map
    """

    # --- Address constants (Pokemon Red/Blue US) ---

    # Battle
    ADDR_BATTLE_TYPE       = 0xD057
    ADDR_ENEMY_HP_HI       = 0xCFE6
    ADDR_ENEMY_HP_LO       = 0xCFE7
    ADDR_ENEMY_MAX_HP_HI   = 0xCFF4
    ADDR_ENEMY_MAX_HP_LO   = 0xCFF5
    ADDR_ENEMY_LEVEL       = 0xCFF3
    ADDR_ENEMY_SPECIES     = 0xCFE5

    # Player party (lead pokemon)
    ADDR_PLAYER_HP_HI      = 0xD015
    ADDR_PLAYER_HP_LO      = 0xD016
    ADDR_PLAYER_MAX_HP_HI  = 0xD023
    ADDR_PLAYER_MAX_HP_LO  = 0xD024
    ADDR_PLAYER_LEVEL      = 0xD022
    ADDR_PLAYER_SPECIES    = 0xD014

    # Moves (lead pokemon)
    ADDR_MOVE_1            = 0xD01C
    ADDR_MOVE_2            = 0xD01D
    ADDR_MOVE_3            = 0xD01E
    ADDR_MOVE_4            = 0xD01F

    # Move PP (lead pokemon)
    ADDR_PP_1              = 0xD02C
    ADDR_PP_2              = 0xD02D
    ADDR_PP_3              = 0xD02E
    ADDR_PP_4              = 0xD02F

    # Party
    ADDR_PARTY_COUNT       = 0xD163

    # Party pokemon HP addresses (6 pokemon, 44 bytes apart)
    PARTY_BASE             = 0xD16B
    PARTY_STRUCT_SIZE      = 44
    PARTY_HP_OFFSET        = 1   # Offset to current HP within party struct

    # Overworld
    ADDR_MAP_ID            = 0xD35E
    ADDR_PLAYER_X          = 0xD362
    ADDR_PLAYER_Y          = 0xD361
    ADDR_BADGES            = 0xD356

    # Money (BCD encoded, 3 bytes)
    ADDR_MONEY_1           = 0xD347
    ADDR_MONEY_2           = 0xD348
    ADDR_MONEY_3           = 0xD349

    # Game state flags (pokered wd730)
    # bit 1: d-pad input disabled (text boxes, menus)
    # bit 5: simulated joypad active (scripted movement, e.g. Oak walking)
    # bit 6: text/script display active (set by DisplayTextID)
    ADDR_WD730             = 0xD730

    # Screen tile map (wTileMap) — 20×18 tile buffer in WRAM.
    # The game writes character tile indices here before copying to VRAM.
    ADDR_TILE_MAP          = 0xC3A0
    SCREEN_WIDTH           = 20
    # Standard NPC text box: rows 14 and 16 (0-indexed), columns 1-18.
    TEXT_ROW_1             = 14
    TEXT_ROW_2             = 16
    TEXT_COL_START         = 1
    TEXT_COL_END           = 19  # exclusive

    def __init__(self, pyboy):
        self.pyboy = pyboy

    def _read(self, addr: int) -> int:
        """Read a single byte from memory."""
        return self.pyboy.memory[addr]

    def _read_16(self, addr_hi: int, addr_lo: int) -> int:
        """Read a 16-bit big-endian value from two addresses."""
        return (self._read(addr_hi) << 8) | self._read(addr_lo)

    def _read_bcd(self, *addrs) -> int:
        """Read BCD-encoded value across multiple bytes."""
        result = 0
        for addr in addrs:
            byte = self._read(addr)
            high = (byte >> 4) & 0x0F
            low = byte & 0x0F
            result = result * 100 + high * 10 + low
        return result

    def read_battle_state(self) -> BattleState:
        """Read full battle context from memory."""
        battle_type = self._read(self.ADDR_BATTLE_TYPE)

        state = BattleState(battle_type=battle_type)

        if battle_type == 0:
            return state

        # Enemy
        state.enemy_hp = self._read_16(self.ADDR_ENEMY_HP_HI, self.ADDR_ENEMY_HP_LO)
        state.enemy_max_hp = self._read_16(self.ADDR_ENEMY_MAX_HP_HI, self.ADDR_ENEMY_MAX_HP_LO)
        state.enemy_level = self._read(self.ADDR_ENEMY_LEVEL)
        state.enemy_species = self._read(self.ADDR_ENEMY_SPECIES)

        # Player lead
        state.player_hp = self._read_16(self.ADDR_PLAYER_HP_HI, self.ADDR_PLAYER_HP_LO)
        state.player_max_hp = self._read_16(self.ADDR_PLAYER_MAX_HP_HI, self.ADDR_PLAYER_MAX_HP_LO)
        state.player_level = self._read(self.ADDR_PLAYER_LEVEL)
        state.player_species = self._read(self.ADDR_PLAYER_SPECIES)

        # Moves
        state.moves = [
            self._read(self.ADDR_MOVE_1),
            self._read(self.ADDR_MOVE_2),
            self._read(self.ADDR_MOVE_3),
            self._read(self.ADDR_MOVE_4),
        ]

        # PP
        state.move_pp = [
            self._read(self.ADDR_PP_1),
            self._read(self.ADDR_PP_2),
            self._read(self.ADDR_PP_3),
            self._read(self.ADDR_PP_4),
        ]

        # Party
        state.party_count = self._read(self.ADDR_PARTY_COUNT)
        state.party_hp = self._read_party_hp(state.party_count)

        return state

    def read_overworld_state(self) -> OverworldState:
        """Read overworld navigation context from memory."""
        party_count = self._read(self.ADDR_PARTY_COUNT)

        return OverworldState(
            map_id=self._read(self.ADDR_MAP_ID),
            x=self._read(self.ADDR_PLAYER_X),
            y=self._read(self.ADDR_PLAYER_Y),
            badges=self._read(self.ADDR_BADGES),
            party_count=party_count,
            party_hp=self._read_party_hp(party_count),
            money=self._read_bcd(self.ADDR_MONEY_1, self.ADDR_MONEY_2, self.ADDR_MONEY_3),
            text_box_active=self._is_text_or_script_active(),
        )

    def _is_text_or_script_active(self) -> bool:
        """Detect text box / menu / scripted movement via wd730 flags."""
        d730 = self._read(self.ADDR_WD730)
        # bit 1 (0x02): d-pad disabled (text/menu active)
        # bit 5 (0x20): simulated joypad (scripted NPC movement)
        # bit 6 (0x40): text/script display in progress
        return bool(d730 & 0x62)

    def read_screen_text(self) -> str:
        """Read text displayed in the on-screen text box.

        Pokemon Red renders NPC dialogue to wTileMap using the same tile
        indices as its character encoding.  The standard text box places
        two lines of text at rows 14 and 16 (columns 1-18).

        Always reads the tile map — the caller decides whether to use
        the result.  Returns empty string when no recognisable text is
        found in the text-box area.
        """
        lines = []
        for row in (self.TEXT_ROW_1, self.TEXT_ROW_2):
            base = self.ADDR_TILE_MAP + row * self.SCREEN_WIDTH
            chars = []
            for col in range(self.TEXT_COL_START, self.TEXT_COL_END):
                byte = self._read(base + col)
                if byte == 0x50:  # string terminator
                    break
                ch = POKEMON_CHAR_MAP.get(byte, '')
                if ch:
                    chars.append(ch)
            line = ''.join(chars).strip()
            if line:
                lines.append(line)

        return ' '.join(lines)

    def _read_party_hp(self, count: int) -> list[int]:
        """Read HP for each party member."""
        hp_list = []
        for i in range(min(count, 6)):
            base = self.PARTY_BASE + (i * self.PARTY_STRUCT_SIZE)
            hp = self._read_16(base + self.PARTY_HP_OFFSET, base + self.PARTY_HP_OFFSET + 1)
            hp_list.append(hp)
        return hp_list

    def is_in_battle(self) -> bool:
        """Quick check: are we in a battle?"""
        return self._read(self.ADDR_BATTLE_TYPE) != 0

    def player_whited_out(self) -> bool:
        """Check if all party pokemon have fainted."""
        count = self._read(self.ADDR_PARTY_COUNT)
        for hp in self._read_party_hp(count):
            if hp > 0:
                return False
        return True
