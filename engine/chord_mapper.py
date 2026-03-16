"""
Chord mapper v2 — expanded harmony with inversions.

Left hand modifier mapping (UPDATED):
    0 (fist/absent) = basic diatonic triad
    1 finger        = 7th chord (diatonic 7th)
    2 fingers       = sus4 (replace 3rd with 4th)
    3 fingers       = 9th chord (7th + 9th — rich extension)
    4 fingers       = vi chord (override degree to 6)
    5 fingers       = vii° chord (override degree to 7)

Right hand thumb = inversion toggle:
    Thumb tucked (down) = root position
    Thumb extended (up) = first inversion (lowest note moves up an octave)

    This works because finger count uses index-through-pinky only in practice:
    1 finger = index, 2 = index+middle, etc. The thumb is an independent
    signal that doesn't conflict with finger counting.

    For second inversion: hold left hand modifier + thumb extended.
    (Future consideration — keeping it simple for now with one inversion level.)

9th chord construction:
    A 9th chord = triad + 7th + 9th (which is the 2nd scale degree up an octave).
    For C major: C E G B D = Cmaj9
    For D minor: D F A C E = Dm9
    These are the lush chords used extensively in neo-soul, R&B, lo-fi, and jazz.

Inversion mechanics:
    Root position: C E G (root on bottom)
    First inversion: E G C (move root up an octave)
    The bass note changes, which changes the harmonic feel without changing
    the chord identity. Inversions are essential for smooth voice leading.
"""

import logging
from dataclasses import dataclass
from typing import Optional, List
from enum import Enum, auto

from engine.music_theory import MusicTheoryEngine, ChordInfo, ChordQuality, NOTE_NAMES


logger = logging.getLogger("gesturechord.engine.chord_mapper")


class Modifier(Enum):
    """Left hand chord modifiers."""
    NONE = auto()        # Basic diatonic triad
    SEVENTH = auto()     # Add 7th
    SUS4 = auto()        # Suspended 4th
    NINTH = auto()       # 9th chord (7th + 9th)
    DEGREE_VI = auto()   # Override to degree 6
    DEGREE_VII = auto()  # Override to degree 7 (diminished)

    @staticmethod
    def from_finger_count(count: Optional[int]) -> "Modifier":
        if count is None or count == 0:
            return Modifier.NONE
        mapping = {
            1: Modifier.SEVENTH,
            2: Modifier.SUS4,
            3: Modifier.NINTH,
            4: Modifier.DEGREE_VI,
            5: Modifier.DEGREE_VII,
        }
        return mapping.get(count, Modifier.NONE)


MODIFIER_NAMES = {
    Modifier.NONE: "",
    Modifier.SEVENTH: "7th",
    Modifier.SUS4: "sus4",
    Modifier.NINTH: "9th",
    Modifier.DEGREE_VI: "vi",
    Modifier.DEGREE_VII: "vii",
}


@dataclass
class MappedChord:
    """Final chord with modifier and inversion metadata."""
    chord_info: ChordInfo
    modifier: Modifier
    modifier_name: str
    display_name: str
    inverted: bool = False


class ChordMapper:
    """
    Combines right hand (degree + thumb inversion) + left hand (modifier).

    Args:
        music_engine: Music theory engine for chord generation.
        settle_frames: Frames for left hand modifier debounce.
    """

    def __init__(self, music_engine: MusicTheoryEngine, settle_frames: int = 4):
        self._engine = music_engine
        self._settle_frames = settle_frames

        self._active_modifier = Modifier.NONE
        self._pending_modifier = Modifier.NONE
        self._modifier_settle_counter = 0
        self._current_degree = 0
        self._inversion = 0  # 0=root, 1=first, 2=second

        logger.info(f"ChordMapper v2: settle={settle_frames}, "
                    f"modifiers=[triad,7th,sus4,9th,vi,vii], inversions=keyboard")

    @property
    def active_modifier(self) -> Modifier:
        return self._active_modifier

    @property
    def active_modifier_name(self) -> str:
        return MODIFIER_NAMES.get(self._active_modifier, "")

    @property
    def inversion(self) -> int:
        """Current inversion level: 0=root, 1=first, 2=second."""
        return self._inversion

    def cycle_inversion(self) -> int:
        """Cycle through inversions: 0 → 1 → 2 → 0. Returns new level."""
        self._inversion = (self._inversion + 1) % 3
        return self._inversion

    def update_modifier(self, left_finger_count: Optional[int]) -> bool:
        """Update left hand modifier. Returns True if modifier changed."""
        new_modifier = Modifier.from_finger_count(left_finger_count)

        if new_modifier == self._active_modifier:
            self._pending_modifier = new_modifier
            self._modifier_settle_counter = 0
            return False

        if new_modifier == self._pending_modifier:
            self._modifier_settle_counter += 1
            if self._modifier_settle_counter >= self._settle_frames:
                old = self._active_modifier
                self._active_modifier = new_modifier
                self._modifier_settle_counter = 0
                logger.info(f"Modifier: {MODIFIER_NAMES.get(old, '?')} -> "
                            f"{MODIFIER_NAMES.get(new_modifier, '?')}")
                return True
        else:
            self._pending_modifier = new_modifier
            self._modifier_settle_counter = 1

        return False

    def get_chord(self, right_finger_count: int) -> Optional[MappedChord]:
        """
        Get the final chord for right hand count + modifier + inversion.

        Args:
            right_finger_count: Right hand finger count (1-5).

        Returns:
            MappedChord with full info.
        """
        self._current_degree = right_finger_count
        modifier = self._active_modifier

        # Handle degree overrides
        degree = right_finger_count
        if modifier == Modifier.DEGREE_VI:
            degree = 6
        elif modifier == Modifier.DEGREE_VII:
            degree = 7

        # Get base diatonic chord
        base = self._engine.get_chord_for_degree(degree)
        if base is None:
            return None

        # Apply modifier transformation
        if modifier == Modifier.SEVENTH:
            result = self._apply_seventh(base)
        elif modifier == Modifier.SUS4:
            result = self._apply_sus4(base)
        elif modifier == Modifier.NINTH:
            result = self._apply_ninth(base)
        else:
            # NONE, DEGREE_VI, DEGREE_VII — use base chord as-is
            result = MappedChord(
                chord_info=base, modifier=modifier,
                modifier_name=MODIFIER_NAMES[modifier],
                display_name=base.chord_name,
            )

        # Apply inversion (keyboard-toggled, can apply multiple times)
        for _ in range(self._inversion):
            result = self._apply_inversion(result)

        return result

    # ── Chord transformations ──

    def _apply_seventh(self, base: ChordInfo) -> MappedChord:
        """Add diatonic 7th."""
        root_midi = base.midi_notes[0]
        root_semi = root_midi % 12

        # Determine 7th type from chord quality
        if base.quality == ChordQuality.MAJOR:
            if base.degree == 5:
                # V chord = dominant 7th (major triad + minor 7th)
                seventh = 10
                suffix = "7"
            else:
                # Other major chords = major 7th
                seventh = 11
                suffix = "maj7"
        elif base.quality == ChordQuality.MINOR:
            seventh = 10
            suffix = "m7"
        elif base.quality == ChordQuality.DIMINISHED:
            seventh = 9  # Fully diminished 7th = 9 semitones
            suffix = "dim7"
        else:
            seventh = 10
            suffix = "7"

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
        """Replace 3rd with 4th."""
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
        """
        Build a 9th chord: triad + 7th + 9th.

        The 9th is the 2nd degree of the scale placed an octave above the root.
        9th chords are the bread and butter of neo-soul, R&B, lo-fi beats.
        """
        root_midi = base.midi_notes[0]
        root_semi = root_midi % 12

        # Get the 7th interval (same logic as _apply_seventh)
        if base.quality == ChordQuality.MAJOR:
            if base.degree == 5:
                seventh = 10  # Dominant 9th
                suffix = "9"
            else:
                seventh = 11  # Major 9th
                suffix = "maj9"
        elif base.quality == ChordQuality.MINOR:
            seventh = 10  # Minor 9th
            suffix = "m9"
        elif base.quality == ChordQuality.DIMINISHED:
            seventh = 9
            suffix = "dim9"
        else:
            seventh = 10
            suffix = "9"

        # The 9th interval = major 2nd up an octave = 14 semitones
        # (For minor 9th chords this is still a major 9th — the standard voicing)
        ninth = 14

        notes = base.midi_notes + [
            min(127, root_midi + seventh),
            min(127, root_midi + ninth),
        ]
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
        """
        Apply first inversion: move the lowest note up one octave.

        First inversion changes the bass note, which changes the harmonic feel:
        C major root position: C4 E4 G4 (strong, grounded)
        C major 1st inversion: E4 G4 C5 (lighter, smoother for voice leading)
        """
        notes = list(mapped.chord_info.midi_notes)
        if len(notes) < 2:
            return mapped

        # Move lowest note up an octave
        lowest = notes.pop(0)
        inverted_note = min(127, lowest + 12)
        notes.append(inverted_note)

        # Rotate note names to match
        names = list(mapped.chord_info.note_names)
        if names:
            first_name = names.pop(0)
            names.append(first_name)

        display = mapped.display_name + "/inv"
        roman = mapped.chord_info.roman_numeral

        info = ChordInfo(
            midi_notes=notes, root_name=mapped.chord_info.root_name,
            chord_name=display,
            roman_numeral=roman,
            quality=mapped.chord_info.quality,
            degree=mapped.chord_info.degree,
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
        self._current_degree = 0
        self._inversion = 0