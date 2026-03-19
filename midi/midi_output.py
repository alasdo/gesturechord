"""
MIDI output for FL Studio integration.

This module sends MIDI note-on/note-off messages through a virtual MIDI port
that FL Studio reads from. On Windows, this requires loopMIDI by Tobias Erichsen.

Architecture:
    - Uses `mido` for MIDI message construction (clean API)
    - Uses `python-rtmidi` as the backend (low latency, reliable)
    - Manages note tracking to prevent stuck notes
    - Provides panic function (all-notes-off) for emergency

MIDI routing on Windows:
    1. Install loopMIDI: https://www.tobias-erichsen.de/software/loopmidi.html
    2. Create a port named "GestureChord" in loopMIDI
    3. In FL Studio: Options → MIDI Settings → select "GestureChord" as input
    4. Enable the port and assign it to a channel or "any"
    5. Load a synth/plugin on a channel — it will receive the MIDI notes

Note tracking:
    We track which notes are currently "on" so we can reliably turn them off.
    This prevents stuck notes when:
    - The program crashes
    - The hand is lost during a chord
    - A chord change happens (old notes must be turned off before new ones)
"""

import logging
import random
import time
from typing import List, Optional, Set, Tuple

import mido
import mido.backends.rtmidi  # Ensure rtmidi backend is loaded


logger = logging.getLogger("gesturechord.midi.midi_output")


# Default port name — must match what's created in loopMIDI
DEFAULT_PORT_NAME = "GestureChord"


class MidiOutput:
    """
    MIDI output manager for sending chords to FL Studio.

    Handles port management, note tracking, and clean note-off behavior.

    Usage:
        midi = MidiOutput()
        midi.open()

        # Play a chord
        midi.play_chord([60, 64, 67], velocity=100)

        # Change to different chord (auto note-off on previous)
        midi.play_chord([65, 69, 72], velocity=100)

        # Stop all notes
        midi.stop_chord()

        midi.close()

    Args:
        port_name: Name of the MIDI port to open. Must match the loopMIDI port.
        channel: MIDI channel (0-15). FL Studio default is usually channel 0.
    """

    def __init__(
        self,
        port_name: str = DEFAULT_PORT_NAME,
        channel: int = 0,
    ):
        self.port_name = port_name
        self.channel = channel

        self._port: Optional[mido.ports.BaseOutput] = None
        self._active_notes: Set[int] = set()  # Currently sounding MIDI note numbers
        self._is_open = False

    @property
    def is_open(self) -> bool:
        return self._is_open

    @property
    def active_notes(self) -> Set[int]:
        """Set of currently sounding MIDI note numbers."""
        return self._active_notes.copy()

    @staticmethod
    def list_ports() -> List[str]:
        """List all available MIDI output ports."""
        try:
            return mido.get_output_names()
        except Exception as e:
            logger.error(f"Failed to list MIDI ports: {e}")
            return []

    def open(self, auto_find: bool = True) -> bool:
        """
        Open the MIDI output port.

        Search strategy:
            1. Exact match for port_name
            2. Substring match for port_name
            3. Auto-detect known virtual MIDI drivers (loopBe, loopMIDI)
            4. If still nothing, list what's available and fail

        Returns:
            True if port opened successfully.
        """
        available = self.list_ports()
        logger.info(f"Available MIDI ports: {available}")

        if not available:
            logger.error(
                "No MIDI output ports found!\n"
                "On Windows, install loopBe1: https://www.nerds.de/en/loopbe1.html"
            )
            return False

        target_port = None

        # 1. Exact match
        for port in available:
            if port == self.port_name:
                target_port = port
                break

        # 2. Substring match
        if target_port is None and auto_find:
            for port in available:
                if self.port_name.lower() in port.lower():
                    target_port = port
                    logger.info(f"Substring match found: '{port}'")
                    break

        # 3. Auto-detect known virtual MIDI port names
        if target_port is None and auto_find:
            known_keywords = ["loopbe", "loopmidi", "gesturechord", "virtual midi"]
            for port in available:
                port_lower = port.lower()
                for keyword in known_keywords:
                    if keyword in port_lower:
                        target_port = port
                        logger.info(f"Auto-detected virtual MIDI port: '{port}'")
                        break
                if target_port is not None:
                    break

        if target_port is None:
            logger.error(
                f"No suitable MIDI port found.\n"
                f"Available ports: {available}\n"
                f"Install loopBe1 or loopMIDI and restart."
            )
            return False

        try:
            self._port = mido.open_output(target_port)
            self._is_open = True
            logger.info(f"Opened MIDI port: '{target_port}'")
            return True
        except Exception as e:
            logger.error(f"Failed to open MIDI port '{target_port}': {e}")
            return False

    def close(self) -> None:
        """Close the MIDI port, stopping all active notes first."""
        if self._is_open:
            self.panic()  # Stop all notes
            if self._port is not None:
                self._port.close()
                self._port = None
            self._is_open = False
            logger.info("MIDI port closed")

    def play_chord(self, midi_notes: List[int], velocity: int = 100) -> None:
        """
        Play a chord with subtle velocity humanization.

        All notes fire immediately (no sleep/stagger) for minimum latency.
        Subtle velocity variation (±4 per note) prevents robotic feel.

        Args:
            midi_notes: List of MIDI note numbers (0-127).
            velocity: MIDI velocity (0-127). Higher = louder.
        """
        if not self._is_open:
            return

        self._stop_active_notes()

        for note in midi_notes:
            note = max(0, min(127, note))
            vel = max(1, min(127, velocity + random.randint(-4, 4)))
            msg = mido.Message("note_on", note=note, velocity=vel, channel=self.channel)
            self._port.send(msg)
            self._active_notes.add(note)

    def stop_chord(self) -> None:
        """Stop all currently sounding notes."""
        if not self._is_open:
            return
        self._stop_active_notes()

    def change_chord(self, new_notes: List[int], velocity: int = 100) -> None:
        """
        Change from current chord to a new chord.

        Same as play_chord() but semantically distinct for logging/debugging.
        In the future, this could implement smart voice leading (keeping
        common tones sustained rather than retriggering them).

        Args:
            new_notes: MIDI note numbers for the new chord.
            velocity: MIDI velocity.
        """
        self.play_chord(new_notes, velocity)

    def send_cc(self, control: int, value: int) -> None:
        """
        Send a MIDI Control Change message.

        Use this for continuous expression control (filter cutoff, reverb, etc.)
        In FL Studio, link any plugin parameter to a CC using "Link to controller."

        Args:
            control: CC number (0-127). Common ones:
                1 = Mod Wheel, 7 = Volume, 10 = Pan, 11 = Expression,
                74 = Brightness/Cutoff
            value: CC value (0-127).
        """
        if not self._is_open:
            return

        control = max(0, min(127, control))
        value = max(0, min(127, value))
        msg = mido.Message("control_change", control=control, value=value, channel=self.channel)
        self._port.send(msg)

    def panic(self) -> None:
        """
        Emergency: stop ALL notes on ALL channels.

        Sends both individual note-offs for tracked notes AND the standard
        MIDI "All Notes Off" CC message for safety.
        """
        if not self._is_open:
            return

        # First, turn off tracked notes
        self._stop_active_notes()

        # Then send All Notes Off (CC 123) and All Sound Off (CC 120)
        # on all channels for maximum safety
        for ch in range(16):
            self._port.send(mido.Message("control_change", control=123, value=0, channel=ch))
            self._port.send(mido.Message("control_change", control=120, value=0, channel=ch))

        logger.info("MIDI panic: all notes off")

    def send_test_note(self, note: int = 60, velocity: int = 100, duration_ms: int = 500) -> None:
        """
        Send a single test note for verifying MIDI routing.

        Plays the note, waits, then stops it. Blocking call.

        Args:
            note: MIDI note number (60 = middle C).
            velocity: Note velocity.
            duration_ms: How long to hold the note in milliseconds.
        """
        import time

        if not self._is_open:
            logger.warning("Cannot send test note: port not open")
            return

        logger.info(f"Sending test note: {_midi_to_name(note)} for {duration_ms}ms")

        self._port.send(mido.Message("note_on", note=note, velocity=velocity, channel=self.channel))
        time.sleep(duration_ms / 1000.0)
        self._port.send(mido.Message("note_off", note=note, velocity=0, channel=self.channel))

        logger.info("Test note complete")

    def _stop_active_notes(self) -> None:
        """Send note-off for all tracked active notes."""
        if not self._active_notes:
            return

        for note in self._active_notes:
            msg = mido.Message("note_off", note=note, velocity=0, channel=self.channel)
            self._port.send(msg)

        logger.debug(f"MIDI note-off: {len(self._active_notes)} notes")
        self._active_notes.clear()

    def __enter__(self) -> "MidiOutput":
        self.open()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()


def _midi_to_name(note: int) -> str:
    """Convert MIDI note number to name (e.g., 60 → 'C4')."""
    octave = (note // 12) - 1
    name = NOTE_NAMES[note % 12]
    return f"{name}{octave}"


NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]