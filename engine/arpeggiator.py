"""
Arpeggiator — plays chord notes sequentially instead of simultaneously.

Instead of sending all notes at once (block chord), the arpeggiator plays
them one at a time in a pattern at a configurable speed.

Patterns:
    UP:      C E G C E G ...
    DOWN:    G E C G E C ...
    UP_DOWN: C E G E C E G ...
    RANDOM:  random order each cycle

Speed is in BPM (beats per minute), where each beat = one note.
120 BPM = 2 notes per second. 240 BPM = 4 notes per second.

The arpeggiator runs on a timer. Every frame, check if it's time to
advance to the next note. This avoids threading.

Usage:
    arp = Arpeggiator(midi_out)
    arp.set_chord([60, 64, 67])  # C major
    # In main loop:
    arp.tick()  # Call every frame — handles timing internally
    # To stop:
    arp.stop()
"""

import logging
import time
import random
from typing import List, Optional
from enum import Enum, auto


logger = logging.getLogger("gesturechord.engine.arpeggiator")


class ArpPattern(Enum):
    UP = auto()
    DOWN = auto()
    UP_DOWN = auto()
    RANDOM = auto()


PATTERN_NAMES = {
    ArpPattern.UP: "up",
    ArpPattern.DOWN: "down",
    ArpPattern.UP_DOWN: "up-dn",
    ArpPattern.RANDOM: "rand",
}

ALL_PATTERNS = [ArpPattern.UP, ArpPattern.DOWN, ArpPattern.UP_DOWN, ArpPattern.RANDOM]


class Arpeggiator:
    """
    Plays chord notes in sequence at configurable BPM.

    Args:
        midi_output: MidiOutput instance for sending notes.
        bpm: Speed in beats per minute (each beat = one note).
        pattern: Arpeggio pattern (UP, DOWN, UP_DOWN, RANDOM).
        enabled: Whether the arpeggiator is active.
        octave_range: How many octaves to span (1 = just the chord, 2 = chord + octave up).
    """

    def __init__(
        self,
        midi_output,
        bpm: float = 160.0,
        pattern: ArpPattern = ArpPattern.UP,
        enabled: bool = False,
        octave_range: int = 1,
    ):
        self._midi = midi_output
        self.bpm = bpm
        self.pattern = pattern
        self.enabled = enabled
        self.octave_range = octave_range

        self._chord_notes: List[int] = []
        self._sequence: List[int] = []
        self._step_index: int = 0
        self._last_step_time: float = 0.0
        self._current_note: Optional[int] = None
        self._playing: bool = False
        self._velocity: int = 100
        self._direction: int = 1  # For UP_DOWN pattern

        logger.info(f"Arpeggiator: bpm={bpm}, pattern={pattern.name}, "
                    f"octaves={octave_range}, enabled={enabled}")

    @property
    def step_interval(self) -> float:
        """Seconds between each note."""
        return 60.0 / max(1.0, self.bpm)

    @property
    def pattern_name(self) -> str:
        return PATTERN_NAMES.get(self.pattern, "?")

    @property
    def is_playing(self) -> bool:
        return self._playing and self.enabled

    def set_chord(self, notes: List[int], velocity: int = 100) -> None:
        """
        Set the chord to arpeggiate. Call on CHORD_ON or CHORD_CHANGE.
        If arpeggiator is enabled, starts playing immediately.
        If disabled, does nothing (chord plays normally through midi_output).
        """
        self._velocity = velocity

        if not self.enabled:
            return

        self._chord_notes = sorted(notes)

        # Build full note sequence including octave range
        all_notes = []
        for octave in range(self.octave_range):
            for note in self._chord_notes:
                n = note + (octave * 12)
                if 0 <= n <= 127:
                    all_notes.append(n)

        self._sequence = self._build_sequence(all_notes)
        self._step_index = 0
        self._direction = 1
        self._last_step_time = time.perf_counter()
        self._playing = True

        # Play first note immediately
        self._play_current_step()

    def stop(self) -> None:
        """Stop the arpeggiator and silence current note."""
        if self._current_note is not None and self._midi.is_open:
            self._midi.stop_chord()
        self._current_note = None
        self._playing = False
        self._chord_notes = []
        self._sequence = []

    def tick(self) -> None:
        """
        Call every frame. Advances to next note when timing says so.
        This is non-blocking — just checks the clock and acts if needed.
        """
        if not self.enabled or not self._playing or not self._sequence:
            return

        now = time.perf_counter()
        elapsed = now - self._last_step_time

        if elapsed >= self.step_interval:
            self._advance_step()
            self._play_current_step()
            self._last_step_time = now

    def cycle_pattern(self) -> ArpPattern:
        """Cycle to next pattern. Returns new pattern."""
        idx = ALL_PATTERNS.index(self.pattern)
        self.pattern = ALL_PATTERNS[(idx + 1) % len(ALL_PATTERNS)]

        # Rebuild sequence if currently playing
        if self._playing and self._chord_notes:
            all_notes = []
            for octave in range(self.octave_range):
                for note in self._chord_notes:
                    n = note + (octave * 12)
                    if 0 <= n <= 127:
                        all_notes.append(n)
            self._sequence = self._build_sequence(all_notes)
            self._step_index = 0
            self._direction = 1

        return self.pattern

    def adjust_bpm(self, delta: float) -> float:
        """Adjust BPM by delta. Returns new BPM."""
        self.bpm = max(40.0, min(480.0, self.bpm + delta))
        return self.bpm

    def _build_sequence(self, notes: List[int]) -> List[int]:
        """Build the note sequence based on current pattern."""
        if not notes:
            return []

        if self.pattern == ArpPattern.UP:
            return list(notes)
        elif self.pattern == ArpPattern.DOWN:
            return list(reversed(notes))
        elif self.pattern == ArpPattern.UP_DOWN:
            if len(notes) <= 1:
                return list(notes)
            # Up then down, don't repeat top and bottom
            return list(notes) + list(reversed(notes[1:-1]))
        elif self.pattern == ArpPattern.RANDOM:
            shuffled = list(notes)
            random.shuffle(shuffled)
            return shuffled
        return list(notes)

    def _advance_step(self) -> None:
        """Move to next step in sequence."""
        if not self._sequence:
            return

        if self.pattern == ArpPattern.RANDOM:
            self._step_index = random.randint(0, len(self._sequence) - 1)
        else:
            self._step_index = (self._step_index + 1) % len(self._sequence)

    def _play_current_step(self) -> None:
        """Play the note at the current step index."""
        if not self._sequence or not self._midi.is_open:
            return

        # Stop previous note
        if self._current_note is not None:
            import mido
            msg = mido.Message("note_off", note=self._current_note, velocity=0,
                               channel=self._midi.channel)
            self._midi._port.send(msg)

        # Play new note
        note = self._sequence[self._step_index % len(self._sequence)]
        import mido
        msg = mido.Message("note_on", note=note, velocity=self._velocity,
                           channel=self._midi.channel)
        self._midi._port.send(msg)
        self._current_note = note

    def reset(self):
        self.stop()