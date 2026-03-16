"""
Chord mapper v3 — shift mode for full 7-degree access.

Left hand mapping:
    0 (fist/absent) = basic diatonic triad (right 1-5 = I-V)
    1 finger        = 7th chord (right 1-5 = I7-V7)
    2 fingers       = sus4 (right 1-5 = Isus4-Vsus4)
    3 fingers       = 9th chord (right 1-5 = I9-V9)
    4 fingers       = SHIFT (right 1-5 = vi, vii, I+, ii+, iii+)
    5 fingers       = SHIFT + 7th (right 1-5 = vi7, vii7, I+7, ii+7, iii+7)

The SHIFT modes remap the right hand to upper degrees:
    Right 1 = degree 6 (vi)
    Right 2 = degree 7 (vii°)
    Right 3 = degree 1 (next octave)
    Right 4 = degree 2 (next octave)
    Right 5 = degree 3 (next octave)

This means with normal + shift, you can reach ALL 7 scale degrees,
and with shift+7th you get all 7 degrees as 7th chords.

Inversions: keyboard-toggled with I key (0=root, 1=1st, 2=2nd).
"""

import logging
from dataclasses import dataclass
from typing import Optional, List
from enum import Enum, auto

from engine.music_theory import MusicTheoryEngine, ChordInfo, ChordQuality, NOTE_NAMES


logger = logging.getLogger("gesturechord.engine.chord_mapper")


class Modifier(Enum):
    NONE = auto()          # Triad
    SEVENTH = auto()       # 7th
    SUS4 = auto()          # Sus4
    NINTH = auto()         # 9th
    SHIFT = auto()         # Degree shift +5
    SHIFT_SEVENTH = auto() # Degree shift +5 with 7ths

    @staticmethod
    def from_finger_count(count: Optional[int]) -> "Modifier":
        if count is None or count == 0:
            return Modifier.NONE
        return {
            1: Modifier.SEVENTH,
            2: Modifier.SUS4,
            3: Modifier.NINTH,
            4: Modifier.SHIFT,
            5: Modifier.SHIFT_SEVENTH,
        }.get(count, Modifier.NONE)


MODIFIER_NAMES = {
    Modifier.NONE: "",
    Modifier.SEVENTH: "7th",
    Modifier.SUS4: "sus4",
    Modifier.NINTH: "9th",
    Modifier.SHIFT: "SHIFT",
    Modifier.SHIFT_SEVENTH: "SHIFT+7",
}


@dataclass
class MappedChord:
    chord_info: ChordInfo
    modifier: Modifier
    modifier_name: str
    display_name: str
    inverted: bool = False


class ChordMapper:
    def __init__(self, music_engine: MusicTheoryEngine, settle_frames: int = 4):
        self._engine = music_engine
        self._settle_frames = settle_frames
        self._active_modifier = Modifier.NONE
        self._pending_modifier = Modifier.NONE
        self._modifier_settle_counter = 0
        self._inversion = 0

        logger.info(f"ChordMapper v3: settle={settle_frames}, "
                    f"modifiers=[triad,7th,sus4,9th,SHIFT,SHIFT+7], inversions=keyboard")

    @property
    def active_modifier(self) -> Modifier:
        return self._active_modifier

    @property
    def active_modifier_name(self) -> str:
        return MODIFIER_NAMES.get(self._active_modifier, "")

    @property
    def inversion(self) -> int:
        return self._inversion

    def cycle_inversion(self) -> int:
        self._inversion = (self._inversion + 1) % 3
        return self._inversion

    def update_modifier(self, left_finger_count: Optional[int]) -> bool:
        new_mod = Modifier.from_finger_count(left_finger_count)
        if new_mod == self._active_modifier:
            self._pending_modifier = new_mod
            self._modifier_settle_counter = 0
            return False
        if new_mod == self._pending_modifier:
            self._modifier_settle_counter += 1
            if self._modifier_settle_counter >= self._settle_frames:
                old = self._active_modifier
                self._active_modifier = new_mod
                self._modifier_settle_counter = 0
                logger.info(f"Modifier: {MODIFIER_NAMES.get(old)} -> {MODIFIER_NAMES.get(new_mod)}")
                return True
        else:
            self._pending_modifier = new_mod
            self._modifier_settle_counter = 1
        return False

    def get_chord(self, right_finger_count: int) -> Optional[MappedChord]:
        """Get chord from right hand count + active modifier + inversion."""
        mod = self._active_modifier

        # ── Resolve degree ──
        if mod in (Modifier.SHIFT, Modifier.SHIFT_SEVENTH):
            # Shifted: right 1=vi(6), 2=vii(7), 3=I+(1), 4=ii+(2), 5=iii+(3)
            shift_map = {1: 6, 2: 7, 3: 1, 4: 2, 5: 3}
            degree = shift_map.get(right_finger_count, right_finger_count)
            octave_bump = 1 if right_finger_count >= 3 else 0
        else:
            degree = right_finger_count
            octave_bump = 0

        # Get base chord
        base = self._engine.get_chord_for_degree(degree)
        if base is None:
            return None

        # Apply octave bump for shifted upper degrees
        if octave_bump:
            base = ChordInfo(
                midi_notes=[min(127, n + 12) for n in base.midi_notes],
                root_name=base.root_name,
                chord_name=base.chord_name,
                roman_numeral=base.roman_numeral,
                quality=base.quality,
                degree=base.degree,
                note_names=base.note_names,
                velocity=base.velocity,
            )

        # ── Apply quality modifier ──
        if mod == Modifier.SEVENTH or mod == Modifier.SHIFT_SEVENTH:
            result = self._apply_seventh(base)
        elif mod == Modifier.SUS4:
            result = self._apply_sus4(base)
        elif mod == Modifier.NINTH:
            result = self._apply_ninth(base)
        else:
            result = MappedChord(
                chord_info=base, modifier=mod,
                modifier_name=MODIFIER_NAMES[mod],
                display_name=base.chord_name,
            )

        # ── Apply inversion ──
        for _ in range(self._inversion):
            result = self._apply_inversion(result)

        return result

    # ── Transformations ──

    def _apply_seventh(self, base: ChordInfo) -> MappedChord:
        root_midi = base.midi_notes[0]
        root_semi = root_midi % 12

        if base.quality == ChordQuality.MAJOR:
            if base.degree == 5:
                seventh, suffix = 10, "7"     # dominant
            else:
                seventh, suffix = 11, "maj7"  # major 7th
        elif base.quality == ChordQuality.MINOR:
            seventh, suffix = 10, "m7"
        elif base.quality == ChordQuality.DIMINISHED:
            seventh, suffix = 9, "dim7"
        else:
            seventh, suffix = 10, "7"

        notes = base.midi_notes + [min(127, root_midi + seventh)]
        names = base.note_names + [NOTE_NAMES[(root_semi + seventh) % 12]]

        info = ChordInfo(
            midi_notes=notes, root_name=base.root_name,
            chord_name=f"{base.root_name}{suffix}",
            roman_numeral=f"{base.roman_numeral}{suffix}",
            quality=base.quality, degree=base.degree,
            note_names=names, velocity=base.velocity,
        )
        return MappedChord(chord_info=info, modifier=Modifier.SEVENTH,
                           modifier_name="7th", display_name=info.chord_name)

    def _apply_sus4(self, base: ChordInfo) -> MappedChord:
        root_midi = base.midi_notes[0]
        root_semi = root_midi % 12
        intervals = [0, 5, 7]
        notes = [min(127, root_midi + iv) for iv in intervals]
        names = [NOTE_NAMES[(root_semi + iv) % 12] for iv in intervals]

        info = ChordInfo(
            midi_notes=notes, root_name=base.root_name,
            chord_name=f"{base.root_name}sus4",
            roman_numeral=f"{base.roman_numeral}sus4",
            quality=ChordQuality.SUS4, degree=base.degree,
            note_names=names, velocity=base.velocity,
        )
        return MappedChord(chord_info=info, modifier=Modifier.SUS4,
                           modifier_name="sus4", display_name=info.chord_name)

    def _apply_ninth(self, base: ChordInfo) -> MappedChord:
        root_midi = base.midi_notes[0]
        root_semi = root_midi % 12

        if base.quality == ChordQuality.MAJOR:
            if base.degree == 5:
                seventh, suffix = 10, "9"
            else:
                seventh, suffix = 11, "maj9"
        elif base.quality == ChordQuality.MINOR:
            seventh, suffix = 10, "m9"
        elif base.quality == ChordQuality.DIMINISHED:
            seventh, suffix = 9, "dim9"
        else:
            seventh, suffix = 10, "9"

        ninth = 14
        notes = base.midi_notes + [min(127, root_midi + seventh), min(127, root_midi + ninth)]
        names = base.note_names + [
            NOTE_NAMES[(root_semi + seventh) % 12],
            NOTE_NAMES[(root_semi + ninth) % 12],
        ]

        info = ChordInfo(
            midi_notes=notes, root_name=base.root_name,
            chord_name=f"{base.root_name}{suffix}",
            roman_numeral=f"{base.roman_numeral}{suffix}",
            quality=base.quality, degree=base.degree,
            note_names=names, velocity=base.velocity,
        )
        return MappedChord(chord_info=info, modifier=Modifier.NINTH,
                           modifier_name="9th", display_name=info.chord_name)

    def _apply_inversion(self, mapped: MappedChord) -> MappedChord:
        notes = list(mapped.chord_info.midi_notes)
        if len(notes) < 2:
            return mapped
        lowest = notes.pop(0)
        notes.append(min(127, lowest + 12))

        names = list(mapped.chord_info.note_names)
        if names:
            names.append(names.pop(0))

        display = mapped.display_name
        if "/inv" not in display:
            display += "/inv"

        info = ChordInfo(
            midi_notes=notes, root_name=mapped.chord_info.root_name,
            chord_name=display, roman_numeral=mapped.chord_info.roman_numeral,
            quality=mapped.chord_info.quality, degree=mapped.chord_info.degree,
            note_names=names, velocity=mapped.chord_info.velocity,
        )
        return MappedChord(
            chord_info=info, modifier=mapped.modifier,
            modifier_name=mapped.modifier_name,
            display_name=display, inverted=True,
        )

    def reset(self):
        self._active_modifier = Modifier.NONE
        self._pending_modifier = Modifier.NONE
        self._modifier_settle_counter = 0
        self._inversion = 0