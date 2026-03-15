"""
Chord mapper — combines right hand (degree) + left hand (modifier) into chords.

This module sits between gesture recognition and MIDI output. It takes:
    - Right hand finger count → scale degree (1-5)
    - Left hand finger count → chord modifier

And produces the final chord to play.

Left hand modifier mapping:
    0 (fist/absent) = no modifier → basic diatonic triad
    1 finger        = add 7th → seventh chord
    2 fingers       = sus4 → replace 3rd with 4th
    3 fingers       = power chord → root + 5th only
    4 fingers       = vi chord (override degree to 6)
    5 fingers       = octave up (+12 to all notes)

The modifier system has its own debouncing: the left hand modifier must be
stable for a settle period before it takes effect. This prevents flickering
between chord qualities during transitions.

When the modifier changes while a chord is sustaining, the chord is
re-triggered with the new quality. This is musically correct — if you're
holding a C major and add the 7th, you want to hear Cmaj7 immediately.
"""

import logging
from dataclasses import dataclass
from typing import Optional, List
from enum import Enum, auto

from engine.music_theory import MusicTheoryEngine, ChordInfo, ChordQuality, CHORD_INTERVALS, NOTE_NAMES


logger = logging.getLogger("gesturechord.engine.chord_mapper")


class Modifier(Enum):
    """Left hand chord modifiers."""
    NONE = auto()       # Basic diatonic triad
    SEVENTH = auto()    # Add 7th
    SUS4 = auto()       # Suspended 4th
    POWER = auto()      # Root + 5th only
    DEGREE_VI = auto()  # Override to degree 6
    OCTAVE_UP = auto()  # +12 semitones

    @staticmethod
    def from_finger_count(count: Optional[int]) -> "Modifier":
        """Map left hand finger count to modifier."""
        if count is None or count == 0:
            return Modifier.NONE
        mapping = {
            1: Modifier.SEVENTH,
            2: Modifier.SUS4,
            3: Modifier.POWER,
            4: Modifier.DEGREE_VI,
            5: Modifier.OCTAVE_UP,
        }
        return mapping.get(count, Modifier.NONE)


# Display names for each modifier
MODIFIER_NAMES = {
    Modifier.NONE: "",
    Modifier.SEVENTH: "7th",
    Modifier.SUS4: "sus4",
    Modifier.POWER: "5 (power)",
    Modifier.DEGREE_VI: "vi",
    Modifier.OCTAVE_UP: "+oct",
}


@dataclass
class MappedChord:
    """
    Final chord ready for MIDI output, including modifier info.
    Extends ChordInfo with modifier metadata for the UI.
    """
    chord_info: ChordInfo           # The actual chord (notes, name, etc.)
    modifier: Modifier              # Active modifier
    modifier_name: str              # Display name of modifier
    display_name: str               # Full display: "Cmaj7" or "G5" etc.


class ChordMapper:
    """
    Combines right hand degree + left hand modifier into final chords.

    Also handles left hand modifier debouncing — the modifier must be
    stable for `settle_frames` before taking effect.

    Args:
        music_engine: The music theory engine for chord generation.
        settle_frames: How many frames the left hand count must be stable
            before the modifier takes effect.
    """

    def __init__(self, music_engine: MusicTheoryEngine, settle_frames: int = 4):
        self._engine = music_engine
        self._settle_frames = settle_frames

        # Left hand modifier state
        self._active_modifier = Modifier.NONE
        self._pending_modifier = Modifier.NONE
        self._modifier_settle_counter = 0
        self._last_left_count: Optional[int] = None

        # Track what's currently playing for re-trigger on modifier change
        self._current_degree: int = 0

        logger.info(f"ChordMapper: settle_frames={settle_frames}")

    @property
    def active_modifier(self) -> Modifier:
        return self._active_modifier

    @property
    def active_modifier_name(self) -> str:
        return MODIFIER_NAMES.get(self._active_modifier, "")

    def update_modifier(self, left_finger_count: Optional[int]) -> bool:
        """
        Update the left hand modifier state.

        Args:
            left_finger_count: Left hand finger count, or None if not detected.

        Returns:
            True if the modifier CHANGED this frame (chord should be re-triggered).
        """
        new_modifier = Modifier.from_finger_count(left_finger_count)

        # If same as active, nothing to do
        if new_modifier == self._active_modifier:
            self._pending_modifier = new_modifier
            self._modifier_settle_counter = 0
            return False

        # Different from active — check if it matches pending (settling)
        if new_modifier == self._pending_modifier:
            self._modifier_settle_counter += 1
            if self._modifier_settle_counter >= self._settle_frames:
                old = self._active_modifier
                self._active_modifier = new_modifier
                self._modifier_settle_counter = 0
                logger.info(
                    f"Modifier changed: {MODIFIER_NAMES.get(old, '?')} -> "
                    f"{MODIFIER_NAMES.get(new_modifier, '?')}"
                )
                return True
        else:
            # New pending modifier
            self._pending_modifier = new_modifier
            self._modifier_settle_counter = 1

        return False

    def get_chord(self, right_finger_count: int) -> Optional[MappedChord]:
        """
        Get the final chord for a right hand finger count + current modifier.

        Args:
            right_finger_count: Right hand finger count (1-5).

        Returns:
            MappedChord with full chord info and modifier metadata.
        """
        self._current_degree = right_finger_count
        modifier = self._active_modifier

        # Handle degree override (vi chord)
        degree = right_finger_count
        if modifier == Modifier.DEGREE_VI:
            degree = 6  # Override to vi regardless of right hand

        # Get base diatonic chord
        base = self._engine.get_chord_for_degree(degree)
        if base is None:
            return None

        # Apply modifier to transform the chord
        if modifier == Modifier.NONE or modifier == Modifier.DEGREE_VI:
            # No transformation needed (DEGREE_VI already changed the degree)
            return MappedChord(
                chord_info=base,
                modifier=modifier,
                modifier_name=MODIFIER_NAMES[modifier],
                display_name=base.chord_name,
            )

        if modifier == Modifier.SEVENTH:
            return self._apply_seventh(base, modifier)

        if modifier == Modifier.SUS4:
            return self._apply_sus4(base, modifier)

        if modifier == Modifier.POWER:
            return self._apply_power(base, modifier)

        if modifier == Modifier.OCTAVE_UP:
            return self._apply_octave_up(base, modifier)

        return MappedChord(
            chord_info=base, modifier=modifier,
            modifier_name="", display_name=base.chord_name,
        )

    def _apply_seventh(self, base: ChordInfo, modifier: Modifier) -> MappedChord:
        """Add a 7th to the chord. Uses diatonic 7th (from the scale)."""
        root_midi = base.midi_notes[0]
        root_semitone = root_midi % 12

        # Determine 7th interval based on chord quality
        if base.quality == ChordQuality.MAJOR:
            # Major chord gets major 7th (11 semitones)
            seventh_interval = 11
            suffix = "maj7"
        elif base.quality == ChordQuality.MINOR:
            # Minor chord gets minor 7th (10 semitones)
            seventh_interval = 10
            suffix = "m7"
        elif base.quality == ChordQuality.DIMINISHED:
            # Diminished gets minor 7th (half-diminished)
            seventh_interval = 10
            suffix = "m7b5"
        else:
            # Default: minor 7th (dominant 7th for major chords on V)
            seventh_interval = 10
            suffix = "7"

        # Special case: V chord gets dominant 7th (major triad + minor 7th)
        if base.degree == 5 and base.quality == ChordQuality.MAJOR:
            seventh_interval = 10
            suffix = "7"

        seventh_note = root_midi + seventh_interval
        new_notes = base.midi_notes + [min(127, seventh_note)]
        seventh_name = NOTE_NAMES[(root_semitone + seventh_interval) % 12]
        new_names = base.note_names + [seventh_name]

        new_info = ChordInfo(
            midi_notes=new_notes,
            root_name=base.root_name,
            chord_name=f"{base.root_name}{suffix}",
            roman_numeral=base.roman_numeral + suffix.replace(base.root_name, ""),
            quality=base.quality,
            degree=base.degree,
            note_names=new_names,
            velocity=base.velocity,
        )
        return MappedChord(
            chord_info=new_info, modifier=modifier,
            modifier_name="7th", display_name=new_info.chord_name,
        )

    def _apply_sus4(self, base: ChordInfo, modifier: Modifier) -> MappedChord:
        """Replace the 3rd with a 4th (suspended 4th)."""
        root_midi = base.midi_notes[0]
        root_semitone = root_midi % 12

        # sus4 = root, 4th (5 semitones), 5th (7 semitones)
        sus4_intervals = [0, 5, 7]
        new_notes = [min(127, root_midi + iv) for iv in sus4_intervals]
        new_names = [NOTE_NAMES[(root_semitone + iv) % 12] for iv in sus4_intervals]

        new_info = ChordInfo(
            midi_notes=new_notes,
            root_name=base.root_name,
            chord_name=f"{base.root_name}sus4",
            roman_numeral=base.roman_numeral + "sus4",
            quality=ChordQuality.SUS4,
            degree=base.degree,
            note_names=new_names,
            velocity=base.velocity,
        )
        return MappedChord(
            chord_info=new_info, modifier=modifier,
            modifier_name="sus4", display_name=new_info.chord_name,
        )

    def _apply_power(self, base: ChordInfo, modifier: Modifier) -> MappedChord:
        """Root + 5th only (power chord)."""
        root_midi = base.midi_notes[0]
        root_semitone = root_midi % 12

        # Power chord = root + 5th (7 semitones)
        power_intervals = [0, 7]
        new_notes = [min(127, root_midi + iv) for iv in power_intervals]
        new_names = [NOTE_NAMES[(root_semitone + iv) % 12] for iv in power_intervals]

        new_info = ChordInfo(
            midi_notes=new_notes,
            root_name=base.root_name,
            chord_name=f"{base.root_name}5",
            roman_numeral=base.roman_numeral + "5",
            quality=base.quality,
            degree=base.degree,
            note_names=new_names,
            velocity=base.velocity,
        )
        return MappedChord(
            chord_info=new_info, modifier=modifier,
            modifier_name="power", display_name=new_info.chord_name,
        )

    def _apply_octave_up(self, base: ChordInfo, modifier: Modifier) -> MappedChord:
        """Shift all notes up one octave."""
        new_notes = [min(127, n + 12) for n in base.midi_notes]

        new_info = ChordInfo(
            midi_notes=new_notes,
            root_name=base.root_name,
            chord_name=base.chord_name + " +oct",
            roman_numeral=base.roman_numeral,
            quality=base.quality,
            degree=base.degree,
            note_names=base.note_names,
            velocity=base.velocity,
        )
        return MappedChord(
            chord_info=new_info, modifier=modifier,
            modifier_name="+oct", display_name=new_info.chord_name,
        )

    def reset(self):
        """Reset modifier state."""
        self._active_modifier = Modifier.NONE
        self._pending_modifier = Modifier.NONE
        self._modifier_settle_counter = 0
        self._current_degree = 0