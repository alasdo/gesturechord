"""
GestureChord v2 Phase 2 — Two-hand chord performance.

Right hand: finger count → scale degree (1-5)
Left hand: finger count → chord modifier
    0/absent = basic triad
    1 = add 7th
    2 = sus4
    3 = power chord
    4 = vi chord
    5 = octave up

Controls:
    ESC/Q=Quit  D=Debug  R=Reset  SPACE=Panic
    K=Key  M=Major/Minor  UP/DOWN=Octave  T=Test
"""

import sys
import time
import logging

import cv2

from vision.camera import Camera
from vision.hand_tracker import HandTracker
from vision.gesture_recognizer import GestureRecognizer, GestureResult
from engine.state_machine import GestureStateMachine, EventType, State
from engine.music_theory import MusicTheoryEngine, ChordInfo
from engine.chord_mapper import ChordMapper, Modifier, MODIFIER_NAMES
from midi.midi_output import MidiOutput
from ui.overlay import Overlay
from utils.logger import setup_logger


# ── Config ──

CAMERA_INDEX = 0
CAMERA_WIDTH = 640
CAMERA_HEIGHT = 480

MAX_HANDS = 2
DETECTION_CONFIDENCE = 0.65
TRACKING_CONFIDENCE = 0.55

HYSTERESIS_HIGH = 0.55
HYSTERESIS_LOW = 0.35
ROLLING_WINDOW = 7

CONFIRMATION_FRAMES = 5
CHANGE_FRAMES = 4
SETTLE_FRAMES = 3
RELEASE_GRACE_MS = 250

MODIFIER_SETTLE_FRAMES = 4  # Left hand modifier debounce

DEFAULT_KEY = "C"
DEFAULT_SCALE = "major"
DEFAULT_OCTAVE = 4
DEFAULT_VELOCITY = 100

MIDI_PORT_NAME = "GestureChord"
MIDI_CHANNEL = 0

WINDOW_NAME = "GestureChord v2"
PERFORMANCE_ZONE_THRESHOLD = 0.75
HAND_LOST_RESET_FRAMES = 15


def main():
    logger = setup_logger(name="gesturechord", level=logging.INFO)
    logger.info("=" * 60)
    logger.info("GestureChord v2 — Two-Hand Chord Performance")
    logger.info("=" * 60)

    # ── Components ──
    camera = Camera(device_index=CAMERA_INDEX, width=CAMERA_WIDTH,
                    height=CAMERA_HEIGHT, mirror=True)

    tracker = HandTracker(max_hands=MAX_HANDS,
                          detection_confidence=DETECTION_CONFIDENCE,
                          tracking_confidence=TRACKING_CONFIDENCE,
                          camera_mirrored=True)

    right_recognizer = GestureRecognizer(
        hysteresis_high=HYSTERESIS_HIGH, hysteresis_low=HYSTERESIS_LOW,
        rolling_window=ROLLING_WINDOW)
    left_recognizer = GestureRecognizer(
        hysteresis_high=HYSTERESIS_HIGH, hysteresis_low=HYSTERESIS_LOW,
        rolling_window=ROLLING_WINDOW)

    state_machine = GestureStateMachine(
        confirmation_frames=CONFIRMATION_FRAMES, change_frames=CHANGE_FRAMES,
        settle_frames=SETTLE_FRAMES, release_grace_ms=RELEASE_GRACE_MS,
        idle_gesture=0)

    music_engine = MusicTheoryEngine(
        root=DEFAULT_KEY, scale=DEFAULT_SCALE,
        octave=DEFAULT_OCTAVE, velocity=DEFAULT_VELOCITY)

    chord_mapper = ChordMapper(
        music_engine=music_engine, settle_frames=MODIFIER_SETTLE_FRAMES)

    midi_out = MidiOutput(port_name=MIDI_PORT_NAME, channel=MIDI_CHANNEL)
    overlay = Overlay(show_debug_info=True)

    # ── State ──
    right_frames_lost = 0
    right_filters_reset = False
    left_frames_lost = 0
    left_filters_reset = False
    current_mapped_chord = None
    midi_available = False

    # ── Init ──
    if not camera.open():
        logger.error("Cannot open camera.")
        sys.exit(1)

    tracker.initialize()
    midi_available = midi_out.open()

    if not midi_available:
        logger.warning("MIDI not available — PREVIEW MODE.")
    else:
        logger.info("MIDI ready!")

    logger.info(f"Key: {music_engine.key_display} | Octave: {music_engine.octave}")
    logger.info("Right hand: 1-5 fingers = I-V chord")
    logger.info("Left hand modifiers:")
    logger.info("  0/absent = triad | 1 = 7th | 2 = sus4 | 3 = power | 4 = vi | 5 = +octave")
    logger.info("Controls: ESC=Quit SPACE=Panic K=Key M=Mode D=Debug T=Test R=Reset")
    _print_chord_map(logger, music_engine)

    # ===================================================================
    try:
        while True:
            frame = camera.read()
            if frame is None:
                time.sleep(0.01)
                continue

            tracking = tracker.process_frame(frame)

            # ── Right hand (chord degree) ──
            right_gesture = None
            right_finger_count = None
            right_is_stable = False
            right_in_zone = False

            right_hand = tracking.get_right_hand()
            if right_hand is not None and right_hand.wrist.y < PERFORMANCE_ZONE_THRESHOLD:
                right_in_zone = True
                right_gesture = right_recognizer.recognize(right_hand)
                right_finger_count = right_gesture.finger_count
                right_is_stable = right_gesture.is_stable
                right_frames_lost = 0
                right_filters_reset = False
            else:
                right_frames_lost += 1
                if right_frames_lost >= HAND_LOST_RESET_FRAMES and not right_filters_reset:
                    right_recognizer.reset()
                    right_filters_reset = True

            # ── Left hand (modifier) ──
            left_gesture = None
            left_finger_count = None
            left_in_zone = False

            left_hand = tracking.get_left_hand()
            if left_hand is not None and left_hand.wrist.y < PERFORMANCE_ZONE_THRESHOLD:
                left_in_zone = True
                left_gesture = left_recognizer.recognize(left_hand)
                left_finger_count = left_gesture.finger_count
                left_frames_lost = 0
                left_filters_reset = False
            else:
                left_frames_lost += 1
                if left_frames_lost >= HAND_LOST_RESET_FRAMES and not left_filters_reset:
                    left_recognizer.reset()
                    left_filters_reset = True

            # ── Update modifier from left hand ──
            modifier_changed = chord_mapper.update_modifier(
                left_finger_count if left_in_zone else None
            )

            # ── State machine (right hand) ──
            event = state_machine.update(right_finger_count, right_is_stable)

            # ── Handle chord events ──
            chord_triggered = False

            if event.event_type == EventType.CHORD_ON:
                mapped = chord_mapper.get_chord(event.finger_count)
                if mapped:
                    if midi_available:
                        midi_out.play_chord(mapped.chord_info.midi_notes,
                                            mapped.chord_info.velocity)
                    current_mapped_chord = mapped
                    chord_triggered = True
                    mod_str = f" [{mapped.modifier_name}]" if mapped.modifier_name else ""
                    logger.info(f"CHORD ON: {mapped.display_name}{mod_str} "
                                f"[{' '.join(mapped.chord_info.note_names)}]")

            elif event.event_type == EventType.CHORD_CHANGE:
                mapped = chord_mapper.get_chord(event.finger_count)
                if mapped:
                    if midi_available:
                        midi_out.change_chord(mapped.chord_info.midi_notes,
                                              mapped.chord_info.velocity)
                    current_mapped_chord = mapped
                    chord_triggered = True
                    mod_str = f" [{mapped.modifier_name}]" if mapped.modifier_name else ""
                    logger.info(f"CHANGE: {mapped.display_name}{mod_str} "
                                f"[{' '.join(mapped.chord_info.note_names)}]")

            elif event.event_type == EventType.CHORD_OFF:
                if midi_available:
                    midi_out.stop_chord()
                current_mapped_chord = None
                logger.info("CHORD OFF")

            # ── Re-trigger on modifier change while chord is sustained ──
            if modifier_changed and not chord_triggered and state_machine.is_playing:
                mapped = chord_mapper.get_chord(state_machine.active_finger_count)
                if mapped:
                    if midi_available:
                        midi_out.change_chord(mapped.chord_info.midi_notes,
                                              mapped.chord_info.velocity)
                    current_mapped_chord = mapped
                    mod_str = f" [{mapped.modifier_name}]" if mapped.modifier_name else ""
                    logger.info(f"MODIFIER: {mapped.display_name}{mod_str} "
                                f"[{' '.join(mapped.chord_info.note_names)}]")

            # ── Build overlay ──
            chord_display = _build_chord_display(
                event, current_mapped_chord, music_engine,
                right_finger_count, chord_mapper,
            )

            status_parts = []
            if not midi_available:
                status_parts.append("PREVIEW")

            frame = overlay.draw(
                frame=frame, tracking=tracking, gesture=right_gesture,
                fps=camera.fps, status_text="  |  ".join(status_parts),
                chord_display=chord_display,
            )

            h_frame, w_frame = frame.shape[:2]

            # Hand badges
            _draw_hand_badge(frame, "R", right_gesture, right_in_zone, w_frame - 140, 10)
            _draw_hand_badge(frame, "L", left_gesture, left_in_zone, w_frame - 140, 65)

            # Modifier display
            mod_name = chord_mapper.active_modifier_name or "triad"
            mod_color = (0, 220, 0) if chord_mapper.active_modifier != Modifier.NONE else (150, 150, 150)
            cv2.rectangle(frame, (w_frame - 140, 120), (w_frame - 10, 150), (30, 30, 30), -1)
            cv2.putText(frame, f"MOD: {mod_name}", (w_frame - 135, 142),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.45, mod_color, 1, cv2.LINE_AA)
            cv2.rectangle(frame, (w_frame - 140, 120), (w_frame - 10, 150), (80, 80, 80), 1)

            # Zone line
            zone_y = int(h_frame * PERFORMANCE_ZONE_THRESHOLD)
            any_in_zone = right_in_zone or left_in_zone
            zone_color = (0, 180, 0) if any_in_zone else (60, 60, 60)
            cv2.line(frame, (0, zone_y), (w_frame, zone_y), zone_color, 1, cv2.LINE_AA)
            cv2.putText(frame, "- zone -", (w_frame - 75, zone_y - 5),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.35, zone_color, 1, cv2.LINE_AA)

            cv2.imshow(WINDOW_NAME, frame)

            key = cv2.waitKey(1) & 0xFF
            if key == 27 or key == ord("q"):
                break
            _handle_keyboard(key, logger, right_recognizer, left_recognizer,
                             state_machine, music_engine, chord_mapper,
                             midi_out, midi_available, overlay)

    except KeyboardInterrupt:
        pass
    finally:
        if midi_available:
            midi_out.close()
        tracker.release()
        camera.release()
        cv2.destroyAllWindows()


def _draw_hand_badge(frame, label, gesture, in_zone, x, y):
    w, h = 130, 50
    cv2.rectangle(frame, (x, y), (x + w, y + h), (30, 30, 30), -1)
    if gesture is not None and in_zone:
        color = (0, 220, 0) if gesture.is_stable else (0, 220, 255)
        cv2.putText(frame, f"{label}:{gesture.finger_count}", (x + 5, y + 35),
                    cv2.FONT_HERSHEY_SIMPLEX, 1.0, color, 2, cv2.LINE_AA)
        if gesture.raw_finger_count != gesture.finger_count:
            cv2.putText(frame, f"r:{gesture.raw_finger_count}", (x + 85, y + 35),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.4, (0, 220, 255), 1, cv2.LINE_AA)
    else:
        cv2.putText(frame, f"{label}: -", (x + 5, y + 35),
                    cv2.FONT_HERSHEY_SIMPLEX, 1.0, (100, 100, 100), 2, cv2.LINE_AA)
    cv2.rectangle(frame, (x, y), (x + w, y + h), (80, 80, 80), 1)


def _build_chord_display(event, mapped_chord, engine, right_count, mapper):
    state_name = event.state.name
    mod_name = mapper.active_modifier_name

    if mapped_chord is not None:
        ci = mapped_chord.chord_info
        name = mapped_chord.display_name
        if mod_name:
            name += f"  [{mod_name}]"
        return {
            "chord_name": name,
            "roman": ci.roman_numeral,
            "notes": " ".join(ci.note_names),
            "state": state_name,
            "progress": event.confirmation_progress,
            "key": engine.key_display,
        }

    if state_name in ("CONFIRMING", "CHANGING", "DETECTING") and right_count and right_count > 0:
        pending = mapper.get_chord(right_count)
        if pending:
            ci = pending.chord_info
            return {
                "chord_name": pending.display_name,
                "roman": ci.roman_numeral,
                "notes": " ".join(ci.note_names),
                "state": state_name,
                "progress": event.confirmation_progress,
                "key": engine.key_display,
            }

    return {
        "chord_name": "", "roman": "", "notes": "",
        "state": state_name, "progress": 0.0,
        "key": engine.key_display,
    }


def _handle_keyboard(key, logger, r_rec, l_rec, sm, me, cm, midi, midi_ok, ov):
    if key == ord(" "):
        sm.reset()
        cm.reset()
        if midi_ok: midi.panic()
        logger.info("PANIC")
    elif key == ord("r"):
        r_rec.reset(); l_rec.reset(); sm.reset(); cm.reset()
        if midi_ok: midi.panic()
        logger.info("Full reset")
    elif key == ord("d"):
        ov.show_debug_info = not ov.show_debug_info
    elif key == ord("k"):
        sm.reset(); cm.reset()
        if midi_ok: midi.stop_chord()
        me.cycle_root(1)
        logger.info(f"Key: {me.key_display}")
        _print_chord_map(logger, me)
    elif key == ord("m"):
        sm.reset(); cm.reset()
        if midi_ok: midi.stop_chord()
        new = "natural_minor" if me.scale_name == "major" else "major"
        me.set_key(me.root, new)
        logger.info(f"Scale: {me.key_display}")
        _print_chord_map(logger, me)
    elif key == ord("t"):
        if midi_ok: midi.send_test_note()
    elif key in (82, 0):  # UP
        sm.reset()
        if midi_ok: midi.stop_chord()
        me.set_octave(me.octave + 1)
        logger.info(f"Octave: {me.octave}")
    elif key in (84, 1):  # DOWN
        sm.reset()
        if midi_ok: midi.stop_chord()
        me.set_octave(me.octave - 1)
        logger.info(f"Octave: {me.octave}")


def _print_chord_map(logger, me):
    for d in range(1, 6):
        c = me.get_chord_for_degree(d)
        if c:
            logger.info(f"  {d}f = {c.roman_numeral} = {c.chord_name} [{' '.join(c.note_names)}]")


if __name__ == "__main__":
    main()