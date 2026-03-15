"""
Music theory engine — scales, chords, intervals, and MIDI note computation.

This module contains all music theory knowledge. It maps abstract concepts
(key of C major, degree 5) to concrete MIDI data (notes 67, 71, 74 = G4, B4, D5).

Design philosophy:
    - Scales and chord qualities are DATA, not code. Adding a new scale or
      chord type means adding an entry to a dictionary, not writing new logic.
    - All note calculations work in semitones from C0 (MIDI note 0).
    - The system thinks in scale degrees (1-7), not note names. This makes
      key changes trivial — just change the root, everything else follows.
    - Chord voicings are configurable. Root position triads for MVP, with
      the structure ready for inversions and extensions later.

Music theory reference for producers:
    - A SCALE is a set of intervals from the root. Major = [0, 2, 4, 5, 7, 9, 11]
    - A CHORD is built by stacking scale tones. Triad = root + 3rd + 5th of the scale
    - DIATONIC means "from the scale." In C major, diatonic triads are C, Dm, Em, F, G, Am, Bdim
    - ROMAN NUMERALS indicate chord function: I = tonic, IV = subdominant, V = dominant
    - Upper case = major, lower case = minor, ° = diminished
"""

import logging
from dataclasses import dataclass
from enum import Enum
from typing import Dict, List, Optional, Tuple


logger = logging.getLogger("gesturechord.engine.music_theory")


# ─── Note names ───

NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]

# Mapping from note name to semitone offset from C
NOTE_TO_SEMITONE = {name: i for i, name in enumerate(NOTE_NAMES)}
# Also accept flats
NOTE_TO_SEMITONE.update({
    "Db": 1, "Eb": 3, "Fb": 4, "Gb": 6, "Ab": 8, "Bb": 10, "Cb": 11,
})


# ─── Scale definitions ───
# Each scale is a list of semitone intervals from the root.
# Index 0 = root (always 0), index 1 = 2nd degree, etc.

SCALES: Dict[str, List[int]] = {
    "major":            [0, 2, 4, 5, 7, 9, 11],
    "natural_minor":    [0, 2, 3, 5, 7, 8, 10],
    "harmonic_minor":   [0, 2, 3, 5, 7, 8, 11],
    "melodic_minor":    [0, 2, 3, 5, 7, 9, 11],
    "dorian":           [0, 2, 3, 5, 7, 9, 10],
    "mixolydian":       [0, 2, 4, 5, 7, 9, 10],
    "pentatonic_major": [0, 2, 4, 7, 9],
    "pentatonic_minor": [0, 3, 5, 7, 10],
}


# ─── Chord quality definitions ───
# Each quality defines intervals above the chord root in semitones.

class ChordQuality(Enum):
    MAJOR = "major"
    MINOR = "minor"
    DIMINISHED = "diminished"
    AUGMENTED = "augmented"
    MAJOR_7 = "major_7"
    MINOR_7 = "minor_7"
    DOMINANT_7 = "dominant_7"
    HALF_DIM_7 = "half_dim_7"
    SUS2 = "sus2"
    SUS4 = "sus4"


# Intervals (in semitones) for each chord quality
CHORD_INTERVALS: Dict[ChordQuality, List[int]] = {
    ChordQuality.MAJOR:       [0, 4, 7],
    ChordQuality.MINOR:       [0, 3, 7],
    ChordQuality.DIMINISHED:  [0, 3, 6],
    ChordQuality.AUGMENTED:   [0, 4, 8],
    ChordQuality.MAJOR_7:     [0, 4, 7, 11],
    ChordQuality.MINOR_7:     [0, 3, 7, 10],
    ChordQuality.DOMINANT_7:  [0, 4, 7, 10],
    ChordQuality.HALF_DIM_7:  [0, 3, 6, 10],
    ChordQuality.SUS2:        [0, 2, 7],
    ChordQuality.SUS4:        [0, 5, 7],
}


# ─── Diatonic chord qualities for standard scales ───
# For each scale type, defines the chord quality built on each degree.
# Index 0 = degree 1, index 1 = degree 2, etc.

DIATONIC_QUALITIES: Dict[str, List[ChordQuality]] = {
    "major": [
        ChordQuality.MAJOR,       # I
        ChordQuality.MINOR,       # ii
        ChordQuality.MINOR,       # iii
        ChordQuality.MAJOR,       # IV
        ChordQuality.MAJOR,       # V
        ChordQuality.MINOR,       # vi
        ChordQuality.DIMINISHED,  # vii°
    ],
    "natural_minor": [
        ChordQuality.MINOR,       # i
        ChordQuality.DIMINISHED,  # ii°
        ChordQuality.MAJOR,       # III
        ChordQuality.MINOR,       # iv
        ChordQuality.MINOR,       # v
        ChordQuality.MAJOR,       # VI
        ChordQuality.MAJOR,       # VII
    ],
    "harmonic_minor": [
        ChordQuality.MINOR,       # i
        ChordQuality.DIMINISHED,  # ii°
        ChordQuality.AUGMENTED,   # III+
        ChordQuality.MINOR,       # iv
        ChordQuality.MAJOR,       # V
        ChordQuality.MAJOR,       # VI
        ChordQuality.DIMINISHED,  # vii°
    ],
    "dorian": [
        ChordQuality.MINOR,       # i
        ChordQuality.MINOR,       # ii
        ChordQuality.MAJOR,       # III
        ChordQuality.MAJOR,       # IV
        ChordQuality.MINOR,       # v
        ChordQuality.DIMINISHED,  # vi°
        ChordQuality.MAJOR,       # VII
    ],
    "mixolydian": [
        ChordQuality.MAJOR,       # I
        ChordQuality.MINOR,       # ii
        ChordQuality.DIMINISHED,  # iii°
        ChordQuality.MAJOR,       # IV
        ChordQuality.MINOR,       # v
        ChordQuality.MINOR,       # vi
        ChordQuality.MAJOR,       # VII
    ],
}


# ─── Roman numeral display ───

ROMAN_NUMERALS = ["I", "II", "III", "IV", "V", "VI", "VII"]

QUALITY_SYMBOLS = {
    ChordQuality.MAJOR: "",
    ChordQuality.MINOR: "m",
    ChordQuality.DIMINISHED: "dim",
    ChordQuality.AUGMENTED: "aug",
    ChordQuality.MAJOR_7: "maj7",
    ChordQuality.MINOR_7: "m7",
    ChordQuality.DOMINANT_7: "7",
    ChordQuality.HALF_DIM_7: "m7b5",
    ChordQuality.SUS2: "sus2",
    ChordQuality.SUS4: "sus4",
}


# ─── Data classes ───

@dataclass
class ChordInfo:
    """
    Complete information about a chord, ready for MIDI output and UI display.

    This is the interface between the music engine and the MIDI/UI systems.
    """
    midi_notes: List[int]       # MIDI note numbers to play (e.g., [60, 64, 67])
    root_name: str              # Root note name (e.g., "C")
    chord_name: str             # Full display name (e.g., "C major")
    roman_numeral: str          # Roman numeral (e.g., "I", "ii", "IV")
    quality: ChordQuality       # Chord quality enum
    degree: int                 # Scale degree (1-7)
    note_names: List[str]       # Note names in the chord (e.g., ["C", "E", "G"])
    velocity: int = 100         # MIDI velocity (0-127)


# ─── Main engine class ───

class MusicTheoryEngine:
    """
    Maps scale degrees to concrete chords with MIDI note numbers.

    Usage:
        engine = MusicTheoryEngine(root="C", scale="major", octave=4)
        chord = engine.get_chord_for_degree(1)
        # chord.midi_notes = [60, 64, 67]  (C4, E4, G4)
        # chord.chord_name = "C major"
        # chord.roman_numeral = "I"

        engine.set_key("G", "major")
        chord = engine.get_chord_for_degree(5)
        # chord.midi_notes = [66, 71, 74]  (F#4, B4, D5)
        # chord.chord_name = "D major"
        # chord.roman_numeral = "V"

    Args:
        root: Root note name (e.g., "C", "F#", "Bb").
        scale: Scale type (e.g., "major", "natural_minor").
        octave: Base octave for chord voicing (MIDI octave, where 4 = middle C).
        velocity: Default MIDI velocity (0-127).
    """

    def __init__(
        self,
        root: str = "C",
        scale: str = "major",
        octave: int = 4,
        velocity: int = 100,
    ):
        self.velocity = velocity
        self._root = ""
        self._scale_name = ""
        self._octave = octave
        self._root_semitone = 0
        self._scale_intervals: List[int] = []
        self._diatonic_qualities: List[ChordQuality] = []

        self.set_key(root, scale)

    @property
    def root(self) -> str:
        return self._root

    @property
    def scale_name(self) -> str:
        return self._scale_name

    @property
    def octave(self) -> int:
        return self._octave

    @property
    def key_display(self) -> str:
        """Human-readable key name for UI (e.g., 'C major', 'A natural_minor')."""
        return f"{self._root} {self._scale_name.replace('_', ' ')}"

    @property
    def num_degrees(self) -> int:
        """Number of scale degrees (7 for standard scales, 5 for pentatonic)."""
        return len(self._scale_intervals)

    def set_key(self, root: str, scale: str) -> None:
        """
        Change the current key.

        Args:
            root: Note name (e.g., "C", "F#", "Bb").
            scale: Scale name (must be a key in SCALES dict).

        Raises:
            ValueError: If root or scale is invalid.
        """
        if root not in NOTE_TO_SEMITONE:
            raise ValueError(
                f"Unknown root note: '{root}'. "
                f"Valid notes: {list(NOTE_TO_SEMITONE.keys())}"
            )
        if scale not in SCALES:
            raise ValueError(
                f"Unknown scale: '{scale}'. "
                f"Valid scales: {list(SCALES.keys())}"
            )

        self._root = root
        self._scale_name = scale
        self._root_semitone = NOTE_TO_SEMITONE[root]
        self._scale_intervals = SCALES[scale]

        # Get diatonic qualities, defaulting to major if not defined
        self._diatonic_qualities = DIATONIC_QUALITIES.get(
            scale, DIATONIC_QUALITIES["major"]
        )

        logger.info(f"Key set to: {self.key_display}")

    def set_octave(self, octave: int) -> None:
        """Change the base octave (typically 3-5)."""
        self._octave = max(1, min(7, octave))
        logger.info(f"Octave set to: {self._octave}")

    def get_chord_for_degree(self, degree: int) -> Optional[ChordInfo]:
        """
        Get the diatonic chord for a given scale degree.

        Args:
            degree: Scale degree, 1-based (1 = tonic, 5 = dominant, etc.)
                Must be between 1 and num_degrees.

        Returns:
            ChordInfo with MIDI notes and display information,
            or None if degree is out of range.
        """
        if degree < 1 or degree > self.num_degrees:
            logger.warning(f"Degree {degree} out of range (1-{self.num_degrees})")
            return None

        # Zero-indexed
        idx = degree - 1

        # Find the chord root: base octave MIDI note + scale interval for this degree
        # MIDI note number: (octave + 1) * 12 + semitone
        # C4 = (4+1)*12 + 0 = 60
        chord_root_semitone = (self._root_semitone + self._scale_intervals[idx]) % 12
        chord_root_midi = (self._octave + 1) * 12 + self._scale_intervals[idx]

        # Adjust if the scale interval wraps past the octave boundary
        # (e.g., degree 7 in C major = B, which is interval 11, still in same octave)
        # Actually the interval is from the root, so we add root_semitone offset
        chord_root_midi = (self._octave + 1) * 12 + self._root_semitone + self._scale_intervals[idx]

        # Get chord quality for this degree
        quality = self._diatonic_qualities[idx] if idx < len(self._diatonic_qualities) else ChordQuality.MAJOR

        # Build chord notes from quality intervals
        intervals = CHORD_INTERVALS[quality]
        midi_notes = [chord_root_midi + interval for interval in intervals]

        # Ensure all notes are in valid MIDI range (0-127)
        midi_notes = [max(0, min(127, n)) for n in midi_notes]

        # Compute note names
        chord_root_name = NOTE_NAMES[chord_root_semitone]
        note_names = [NOTE_NAMES[(chord_root_semitone + iv) % 12] for iv in intervals]

        # Roman numeral display
        roman = ROMAN_NUMERALS[idx] if idx < len(ROMAN_NUMERALS) else str(degree)
        if quality in (ChordQuality.MINOR, ChordQuality.MINOR_7, ChordQuality.HALF_DIM_7):
            roman = roman.lower()

        quality_symbol = QUALITY_SYMBOLS.get(quality, "")
        # For basic triads, use just the root + quality for the name
        chord_name = f"{chord_root_name} {quality.value.replace('_', ' ')}"

        # Roman numeral with quality suffix for diminished/augmented
        roman_display = roman
        if quality == ChordQuality.DIMINISHED:
            roman_display = roman + "°"
        elif quality == ChordQuality.AUGMENTED:
            roman_display = roman + "+"

        return ChordInfo(
            midi_notes=midi_notes,
            root_name=chord_root_name,
            chord_name=chord_name,
            roman_numeral=roman_display,
            quality=quality,
            degree=degree,
            note_names=note_names,
            velocity=self.velocity,
        )

    def get_chord_for_finger_count(self, finger_count: int) -> Optional[ChordInfo]:
        """
        Map a finger count (1-5) to a chord.

        Mapping:
            1 finger = degree 1 (I)
            2 fingers = degree 2 (ii)
            3 fingers = degree 3 (iii)
            4 fingers = degree 4 (IV)
            5 fingers = degree 5 (V)

        For degrees 6-7, future two-hand gestures will be used.

        Args:
            finger_count: Number of fingers held up (1-5).

        Returns:
            ChordInfo or None if finger_count maps to no chord.
        """
        if finger_count < 1 or finger_count > min(5, self.num_degrees):
            return None

        return self.get_chord_for_degree(finger_count)

    def get_all_diatonic_chords(self) -> List[ChordInfo]:
        """Get all diatonic chords in the current key (for UI display)."""
        chords = []
        for degree in range(1, self.num_degrees + 1):
            chord = self.get_chord_for_degree(degree)
            if chord:
                chords.append(chord)
        return chords

    @staticmethod
    def get_available_roots() -> List[str]:
        """Get all valid root note names."""
        return list(NOTE_NAMES)

    @staticmethod
    def get_available_scales() -> List[str]:
        """Get all valid scale names."""
        return list(SCALES.keys())

    def cycle_root(self, direction: int = 1) -> str:
        """
        Cycle to the next/previous root note.

        Args:
            direction: 1 for next (C → C#), -1 for previous (C → B).

        Returns:
            New root note name.
        """
        current_idx = NOTE_NAMES.index(self._root) if self._root in NOTE_NAMES else 0
        new_idx = (current_idx + direction) % 12
        new_root = NOTE_NAMES[new_idx]
        self.set_key(new_root, self._scale_name)
        return new_root

    def cycle_scale(self, direction: int = 1) -> str:
        """
        Cycle to the next/previous scale type.

        Args:
            direction: 1 for next, -1 for previous.

        Returns:
            New scale name.
        """
        scale_names = list(SCALES.keys())
        try:
            current_idx = scale_names.index(self._scale_name)
        except ValueError:
            current_idx = 0
        new_idx = (current_idx + direction) % len(scale_names)
        new_scale = scale_names[new_idx]
        self.set_key(self._root, new_scale)
        return new_scale