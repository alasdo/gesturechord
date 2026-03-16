"""
Groove patterns v2 — with gate length for clean articulation.

v1 problem: notes were cut instantly at each new hit, causing harsh
digital clicks because the synth's release envelope got chopped.

v2 fix: Gate system.
    - Each hit starts notes (note-on)
    - Notes are held for `gate_length` fraction of the step interval
    - After gate expires, note-off is sent
    - If the SAME chord retriggers, skip note-off (let it naturally
      re-articulate without a gap)
    - If a DIFFERENT chord plays, send note-off then note-on

gate_length:
    0.8 = notes ring for 80% of the step interval (legato, smooth)
    0.5 = notes ring for 50% (staccato, punchy)
    1.0 = notes sustain until next hit (fully legato, no gap)

The result: clean rhythmic playback without digital artifacts.
"""

import logging
import time
import random
from typing import List, Optional
from dataclasses import dataclass


logger = logging.getLogger("gesturechord.engine.groove")


PATTERNS = {
    "four_floor": [
        (0.0, 1.0), (0.25, 0.75), (0.5, 0.9), (0.75, 0.75),
    ],
    "syncopated": [
        (0.0, 1.0), (0.375, 0.7), (0.5, 0.85), (0.875, 0.65),
    ],
    "trap": [
        (0.0, 1.0), (0.125, 0.4), (0.25, 0.7), (0.375, 0.45),
        (0.5, 0.9), (0.625, 0.4), (0.75, 0.7), (0.875, 0.5),
    ],
    "half_time": [
        (0.0, 1.0), (0.5, 0.85),
    ],
    "offbeat": [
        (0.25, 0.9), (0.75, 0.85),
    ],
    "waltz": [
        (0.0, 1.0), (0.333, 0.6), (0.666, 0.6),
    ],
    "shuffle": [
        (0.0, 1.0), (0.167, 0.5), (0.333, 0.8),
        (0.5, 0.9), (0.667, 0.5), (0.833, 0.75),
    ],
    "sparse": [
        (0.0, 1.0),
    ],
}

PATTERN_NAMES = list(PATTERNS.keys())


class GrooveEngine:
    """
    Plays chords in rhythmic patterns with proper gate articulation.

    Args:
        midi_output: MidiOutput instance.
        bpm: Tempo in BPM.
        pattern_name: Key in PATTERNS dict.
        gate_length: Fraction of step interval to hold notes (0.0-1.0).
            0.8 = legato, 0.5 = staccato, 1.0 = fully sustained.
        humanize_ms: Micro-timing variation in ms.
        enabled: Whether groove is active.
    """

    def __init__(
        self,
        midi_output,
        bpm: float = 120.0,
        pattern_name: str = "four_floor",
        gate_length: float = 0.85,
        humanize_ms: float = 10.0,
        enabled: bool = False,
    ):
        self._midi = midi_output
        self.bpm = bpm
        self.gate_length = gate_length
        self.humanize_ms = humanize_ms
        self.enabled = enabled

        self._pattern_name = pattern_name
        self._pattern = PATTERNS.get(pattern_name, PATTERNS["four_floor"])

        self._chord_notes: List[int] = []
        self._velocity: int = 100
        self._playing: bool = False

        # Timing
        self._bar_start_time: float = 0.0
        self._current_step: int = 0

        # Gate tracking
        self._active_notes: List[int] = []
        self._note_on_time: float = 0.0
        self._current_gate_duration: float = 0.0
        self._notes_need_off: bool = False

        logger.info(f"GrooveEngine v2: bpm={bpm}, pattern={pattern_name}, "
                    f"gate={gate_length}, humanize={humanize_ms}ms")

    @property
    def bar_duration(self) -> float:
        return (60.0 / max(1.0, self.bpm)) * 4.0

    @property
    def pattern_name(self) -> str:
        return self._pattern_name

    @property
    def is_playing(self) -> bool:
        return self._playing and self.enabled

    def set_chord(self, notes: List[int], velocity: int = 100) -> None:
        """Set chord. If pattern is running, next hit uses new chord."""
        self._chord_notes = list(notes)
        self._velocity = velocity
        if self.enabled and not self._playing:
            self._start()

    def _start(self) -> None:
        self._bar_start_time = time.perf_counter()
        self._current_step = 0
        self._playing = True
        self._play_hit(self._pattern[0][1])

    def stop(self) -> None:
        self._release_notes()
        self._playing = False
        self._chord_notes = []
        self._notes_need_off = False

    def tick(self) -> None:
        """Call every frame. Handles hit timing and gate release."""
        if not self.enabled or not self._playing or not self._chord_notes:
            return

        now = time.perf_counter()

        # ── Gate release check ──
        # If notes are active and gate duration has passed, release them
        if self._notes_need_off and self._active_notes:
            elapsed_since_on = now - self._note_on_time
            if elapsed_since_on >= self._current_gate_duration:
                self._release_notes()
                self._notes_need_off = False

        # ── Step timing ──
        bar_elapsed = now - self._bar_start_time
        bar_dur = self.bar_duration

        # Bar loop
        if bar_elapsed >= bar_dur:
            self._bar_start_time += bar_dur
            self._current_step = 0
            bar_elapsed = now - self._bar_start_time

        bar_position = bar_elapsed / bar_dur

        # Check next step
        if self._current_step < len(self._pattern):
            step_pos, step_vel = self._pattern[self._current_step]

            # Humanization
            offset = 0.0
            if self.humanize_ms > 0:
                offset = random.uniform(-self.humanize_ms, self.humanize_ms) / 1000.0 / bar_dur

            if bar_position >= step_pos + offset:
                self._play_hit(step_vel)
                self._current_step += 1

    def cycle_pattern(self) -> str:
        idx = PATTERN_NAMES.index(self._pattern_name) if self._pattern_name in PATTERN_NAMES else 0
        self._pattern_name = PATTERN_NAMES[(idx + 1) % len(PATTERN_NAMES)]
        self._pattern = PATTERNS[self._pattern_name]
        self._current_step = 0
        return self._pattern_name

    def adjust_bpm(self, delta: float) -> float:
        self.bpm = max(40.0, min(300.0, self.bpm + delta))
        return self.bpm

    def _play_hit(self, vel_multiplier: float) -> None:
        """Play chord with gate timing."""
        if not self._chord_notes or not self._midi.is_open:
            return

        hit_vel = max(1, min(127, int(self._velocity * vel_multiplier)))
        same_chord = (set(self._active_notes) == set(self._chord_notes))

        if same_chord and self._active_notes:
            # Same chord retriggering — DON'T send note-off first.
            # Just send note-on again. Most synths handle this as a
            # clean retrigger without the harsh cut.
            import mido
            for note in self._chord_notes:
                msg = mido.Message("note_on", note=note, velocity=hit_vel,
                                   channel=self._midi.channel)
                self._midi._port.send(msg)
        else:
            # Different chord — release old, play new
            self._release_notes()
            import mido
            for note in self._chord_notes:
                msg = mido.Message("note_on", note=note, velocity=hit_vel,
                                   channel=self._midi.channel)
                self._midi._port.send(msg)
            self._active_notes = list(self._chord_notes)

        # Calculate gate duration
        # Find time until next step to compute gate
        step_interval = self._get_step_interval()
        self._current_gate_duration = step_interval * self.gate_length
        self._note_on_time = time.perf_counter()
        self._notes_need_off = True

    def _get_step_interval(self) -> float:
        """Get time between current step and next step in seconds."""
        bar_dur = self.bar_duration
        pattern = self._pattern

        if self._current_step < len(pattern) - 1:
            # Time to next step
            current_pos = pattern[self._current_step][0] if self._current_step < len(pattern) else 0
            # Next step might be current_step (just played) or current_step+1
            next_idx = min(self._current_step, len(pattern) - 1)
            if next_idx + 1 < len(pattern):
                gap = pattern[next_idx + 1][0] - pattern[next_idx][0]
            else:
                # Last step — gap to end of bar + first step of next bar
                gap = 1.0 - pattern[next_idx][0] + pattern[0][0]
            return gap * bar_dur
        else:
            # Last step — gap wraps to start of next bar
            last_pos = pattern[-1][0]
            gap = 1.0 - last_pos + pattern[0][0]
            return gap * bar_dur

    def _release_notes(self) -> None:
        """Send note-off for active notes."""
        if not self._active_notes or not self._midi.is_open:
            return
        import mido
        for note in self._active_notes:
            msg = mido.Message("note_off", note=note, velocity=0,
                               channel=self._midi.channel)
            self._midi._port.send(msg)
        self._active_notes = []

    def reset(self):
        self.stop()