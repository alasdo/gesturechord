"""
Chord bank — custom chord-to-finger mapping with presets.

Presets let you define multiple chord banks in config.yaml and switch
between them with number keys during performance. Zero latency —
all presets are parsed at startup.

Example config:
    chord_bank:
      enabled: false
      octave: 4
      active_preset: 0
      presets:
        - name: "Tu Me Dejaste"
          chords: {1: "C", 2: "Em", 3: "D", 4: "B7", 5: "Am"}
        - name: "Pop I-V-vi-IV"
          chords: {1: "C", 2: "G", 3: "Am", 4: "F", 5: "Em"}
        - name: "Jazz ii-V-I"
          chords: {1: "Dm7", 2: "G7", 3: "Cmaj7", 4: "Am7", 5: "Fmaj7"}

Supported chord formats:
    C, Cm, Cdim, Caug, C7, Cm7, Cmaj7, Cdim7,
    C9, Cm9, Cmaj9, Csus2, Csus4, C6, Cm6,
    C#, Db, F#m, Bbmaj7, etc.
"""

import logging
from typing import Dict, List, Optional
from dataclasses import dataclass


logger = logging.getLogger("gesturechord.engine.chord_bank")


NOTE_MAP = {
    "C": 0, "C#": 1, "Db": 1,
    "D": 2, "D#": 3, "Eb": 3,
    "E": 4, "Fb": 4,
    "F": 5, "F#": 6, "Gb": 6,
    "G": 7, "G#": 8, "Ab": 8,
    "A": 9, "A#": 10, "Bb": 10,
    "B": 11, "Cb": 11,
}

NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]

QUALITIES = {
    "":       [0, 4, 7],
    "m":      [0, 3, 7],
    "dim":    [0, 3, 6],
    "aug":    [0, 4, 8],
    "7":      [0, 4, 7, 10],
    "m7":     [0, 3, 7, 10],
    "maj7":   [0, 4, 7, 11],
    "dim7":   [0, 3, 6, 9],
    "m7b5":   [0, 3, 6, 10],
    "9":      [0, 4, 7, 10, 14],
    "m9":     [0, 3, 7, 10, 14],
    "maj9":   [0, 4, 7, 11, 14],
    "sus2":   [0, 2, 7],
    "sus4":   [0, 5, 7],
    "add9":   [0, 4, 7, 14],
    "6":      [0, 4, 7, 9],
    "m6":     [0, 3, 7, 9],
}


@dataclass
class BankChord:
    name: str
    midi_notes: List[int]
    note_names: List[str]
    root_name: str
    velocity: int = 100


def parse_chord(chord_str: str, octave: int = 4) -> Optional[BankChord]:
    chord_str = chord_str.strip()
    if not chord_str:
        return None

    root = None
    root_len = 0

    if len(chord_str) >= 2 and chord_str[1] in ("#", "b"):
        candidate = chord_str[:2]
        if candidate in NOTE_MAP:
            root = candidate
            root_len = 2

    if root is None and chord_str[0] in NOTE_MAP:
        root = chord_str[0]
        root_len = 1

    if root is None:
        logger.warning(f"Cannot parse chord root: '{chord_str}'")
        return None

    quality_str = chord_str[root_len:]
    intervals = QUALITIES.get(quality_str)
    if intervals is None:
        logger.warning(f"Unknown chord quality: '{quality_str}' in '{chord_str}', using major")
        intervals = QUALITIES[""]

    root_semitone = NOTE_MAP[root]
    root_midi = (octave + 1) * 12 + root_semitone

    midi_notes = []
    note_names = []
    for iv in intervals:
        note = root_midi + iv
        if 0 <= note <= 127:
            midi_notes.append(note)
            note_names.append(NOTE_NAMES[(root_semitone + iv) % 12])

    return BankChord(
        name=chord_str, midi_notes=midi_notes,
        note_names=note_names, root_name=root,
    )


@dataclass
class Preset:
    name: str
    chord_map: Dict[int, str]
    parsed: Dict[int, Optional[BankChord]]


class ChordBank:
    def __init__(self, presets: List[Dict], octave: int = 4,
                 active_preset: int = 0, enabled: bool = False):
        self.enabled = enabled
        self.octave = octave
        self._presets: List[Preset] = []
        self._active_idx: int = 0

        for i, p in enumerate(presets):
            name = p.get("name", f"Preset {i+1}")
            chord_map = {}
            raw_chords = p.get("chords", {})
            for k, v in raw_chords.items():
                try:
                    chord_map[int(k)] = str(v)
                except (ValueError, TypeError):
                    pass

            parsed = {}
            for slot, chord_name in chord_map.items():
                bc = parse_chord(chord_name, octave)
                if bc:
                    parsed[slot] = bc

            self._presets.append(Preset(name=name, chord_map=chord_map, parsed=parsed))
            logger.info(f"  Preset {i}: '{name}' — {list(chord_map.values())}")

        if not self._presets:
            default_map = {1: "C", 2: "Em", 3: "D", 4: "B7", 5: "Am"}
            parsed = {s: parse_chord(n, octave) for s, n in default_map.items()}
            self._presets.append(Preset(name="Default", chord_map=default_map, parsed=parsed))

        self._active_idx = min(active_preset, len(self._presets) - 1)

    @property
    def active_preset_name(self) -> str:
        return self._presets[self._active_idx].name

    @property
    def active_preset_index(self) -> int:
        return self._active_idx

    @property
    def preset_count(self) -> int:
        return len(self._presets)

    def get_chord(self, finger_count: int) -> Optional[BankChord]:
        if not self.enabled:
            return None
        return self._presets[self._active_idx].parsed.get(finger_count)

    def switch_preset(self, index: int) -> bool:
        if 0 <= index < len(self._presets):
            self._active_idx = index
            return True
        return False

    def next_preset(self) -> int:
        self._active_idx = (self._active_idx + 1) % len(self._presets)
        return self._active_idx

    def set_octave(self, octave: int) -> None:
        self.octave = max(1, min(7, octave))
        for preset in self._presets:
            preset.parsed = {}
            for slot, chord_name in preset.chord_map.items():
                bc = parse_chord(chord_name, self.octave)
                if bc:
                    preset.parsed[slot] = bc

    def print_bank(self, log) -> None:
        p = self._presets[self._active_idx]
        log.info(f"  Preset [{self._active_idx}]: {p.name}")
        for slot in sorted(p.parsed.keys()):
            bc = p.parsed[slot]
            log.info(f"    {slot} = {bc.name} [{' '.join(bc.note_names)}]")

    def print_all_presets(self, log) -> None:
        for i, p in enumerate(self._presets):
            marker = " <<<" if i == self._active_idx else ""
            chords = ", ".join(f"{s}={p.chord_map[s]}" for s in sorted(p.chord_map.keys()))
            log.info(f"  [{i}] {p.name}: {chords}{marker}")