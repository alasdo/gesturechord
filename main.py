"""
GestureChord v2 Phase 3 — Two-hand performance with expression control.

Right hand: finger count -> scale degree (1-5)
Left hand:
    Finger count -> chord modifier (7th, sus4, power, vi, +oct)
    Y position -> MIDI CC for continuous effects control

CC Expression:
    Hand high = CC 127 (max effect)
    Hand low = CC 0 (min effect)
    Map to any FL Studio parameter via "Link to controller"

Controls:
    ESC/Q=Quit  D=Debug  R=Reset  SPACE=Panic
    K=Key  M=Major/Minor  UP/DOWN=Octave  T=Test
    E=Toggle expression CC on/off
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
from engine.expression import ExpressionController
from midi.midi_output import MidiOutput
from ui.overlay import Overlay, OverlayState
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

MODIFIER_SETTLE_FRAMES = 4

DEFAULT_KEY = "C"
DEFAULT_SCALE = "major"
DEFAULT_OCTAVE = 4
DEFAULT_VELOCITY = 100

# Expression CC
EXPRESSION_CC = 1            # CC 1 = Mod Wheel (most widely supported)
EXPRESSION_ZONE_TOP = 0.15   # Hand at top of frame = CC 127
EXPRESSION_ZONE_BOTTOM = 0.65  # Hand near zone line = CC 0
EXPRESSION_SMOOTHING = 0.25  # EMA alpha (lower = smoother)
EXPRESSION_DEAD_ZONE = 2.0   # Min CC change before sending

MIDI_PORT_NAME = "GestureChord"
MIDI_CHANNEL = 0

WINDOW_NAME = "GestureChord v2"
DISPLAY_SCALE = 1.5              # Scale up the window (1.0 = native 640x480, 1.5 = 960x720)
PERFORMANCE_ZONE_THRESHOLD = 0.75
HAND_LOST_RESET_FRAMES = 15


def main():
    logger = setup_logger(name="gesturechord", level=logging.INFO)
    logger.info("=" * 60)
    logger.info("GestureChord v2 — Two-Hand Performance + Expression")
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

    expression = ExpressionController(
        cc_number=EXPRESSION_CC,
        zone_top=EXPRESSION_ZONE_TOP,
        zone_bottom=EXPRESSION_ZONE_BOTTOM,
        smoothing_alpha=EXPRESSION_SMOOTHING,
        dead_zone=EXPRESSION_DEAD_ZONE,
        enabled=True)

    midi_out = MidiOutput(port_name=MIDI_PORT_NAME, channel=MIDI_CHANNEL)
    overlay = Overlay(show_debug_info=False)  # Press D for debug mode

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
    logger.info("Left hand: fingers=modifier, height=CC expression")
    logger.info("  Modifiers: 0=triad 1=7th 2=sus4 3=9th 4=vi 5=vii")
    logger.info("  Right thumb up = first inversion")
    logger.info(f"  Expression: CC{EXPRESSION_CC} (hand height)")
    logger.info("Controls: ESC=Quit SPACE=Panic K=Key M=Mode D=Debug T=Test R=Reset E=Expr I=Inversion")
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

            # ── Left hand (modifier + expression) ──
            left_gesture = None
            left_finger_count = None
            left_in_zone = False
            left_hand_y = None

            left_hand = tracking.get_left_hand()
            if left_hand is not None and left_hand.wrist.y < PERFORMANCE_ZONE_THRESHOLD:
                left_in_zone = True
                left_gesture = left_recognizer.recognize(left_hand)
                left_finger_count = left_gesture.finger_count
                left_hand_y = left_hand.wrist.y  # For expression CC
                left_frames_lost = 0
                left_filters_reset = False
            else:
                left_frames_lost += 1
                if left_frames_lost >= HAND_LOST_RESET_FRAMES and not left_filters_reset:
                    left_recognizer.reset()
                    left_filters_reset = True

            # ── Update modifier ──
            modifier_changed = chord_mapper.update_modifier(
                left_finger_count if left_in_zone else None)

            # ── Update expression CC ──
            cc_value = expression.update(left_hand_y if left_in_zone else None)
            if cc_value is not None and midi_available:
                midi_out.send_cc(expression.cc_number, cc_value)

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
                    _log_chord(logger, "ON", mapped)

            elif event.event_type == EventType.CHORD_CHANGE:
                mapped = chord_mapper.get_chord(event.finger_count)
                if mapped:
                    if midi_available:
                        midi_out.change_chord(mapped.chord_info.midi_notes,
                                              mapped.chord_info.velocity)
                    current_mapped_chord = mapped
                    chord_triggered = True
                    _log_chord(logger, "CHANGE", mapped)

            elif event.event_type == EventType.CHORD_OFF:
                if midi_available:
                    midi_out.stop_chord()
                current_mapped_chord = None
                logger.info("CHORD OFF")

            # ── Re-trigger on modifier change ──
            if modifier_changed and not chord_triggered and state_machine.is_playing:
                mapped = chord_mapper.get_chord(state_machine.active_finger_count)
                if mapped:
                    if midi_available:
                        midi_out.change_chord(mapped.chord_info.midi_notes,
                                              mapped.chord_info.velocity)
                    current_mapped_chord = mapped
                    _log_chord(logger, "MODIFIER", mapped)

            # ── Build overlay state and render ──
            os = OverlayState(
                tracking=tracking,
                right_gesture=right_gesture,
                left_gesture=left_gesture,
                right_in_zone=right_in_zone,
                left_in_zone=left_in_zone,
                chord_name=current_mapped_chord.display_name if current_mapped_chord else "",
                roman=current_mapped_chord.chord_info.roman_numeral if current_mapped_chord else "",
                notes=" ".join(current_mapped_chord.chord_info.note_names) if current_mapped_chord else "",
                chord_state=event.state.name,
                confirm_progress=event.confirmation_progress,
                key_display=music_engine.key_display,
                modifier_name=chord_mapper.active_modifier_name or "triad",
                modifier_active=chord_mapper.active_modifier != Modifier.NONE,
                inversion=chord_mapper.inversion,
                cc_number=expression.cc_number,
                cc_value=expression.cc_value,
                cc_normalized=expression.cc_normalized,
                cc_enabled=expression.enabled,
                fps=camera.fps,
                inference_ms=tracking.inference_time_ms,
                midi_available=midi_available,
                zone_threshold=PERFORMANCE_ZONE_THRESHOLD,
            )

            # Show pending chord during confirmation if nothing active
            if not current_mapped_chord and right_finger_count and right_finger_count > 0:
                if event.state.name in ("CONFIRMING", "CHANGING", "DETECTING"):
                    pending = chord_mapper.get_chord(right_finger_count)
                    if pending:
                        os.chord_name = pending.display_name
                        os.roman = pending.chord_info.roman_numeral
                        os.notes = " ".join(pending.chord_info.note_names)

            frame = overlay.draw(frame, os)

            # Scale up for larger display
            if DISPLAY_SCALE != 1.0:
                disp_w = int(frame.shape[1] * DISPLAY_SCALE)
                disp_h = int(frame.shape[0] * DISPLAY_SCALE)
                frame = cv2.resize(frame, (disp_w, disp_h), interpolation=cv2.INTER_LINEAR)

            cv2.imshow(WINDOW_NAME, frame)

            key = cv2.waitKey(1) & 0xFF
            if key == 27 or key == ord("q"):
                break
            _handle_keyboard(key, logger, right_recognizer, left_recognizer,
                             state_machine, music_engine, chord_mapper, expression,
                             midi_out, midi_available, overlay)

    except KeyboardInterrupt:
        pass
    finally:
        if midi_available:
            midi_out.close()
        tracker.release()
        camera.release()
        cv2.destroyAllWindows()


# ── Helpers ──

def _log_chord(logger, action, mapped):
    mod_str = f" [{mapped.modifier_name}]" if mapped.modifier_name else ""
    notes = " ".join(mapped.chord_info.note_names)
    logger.info(f"{action}: {mapped.display_name}{mod_str} [{notes}]")


def _handle_keyboard(key, logger, r_rec, l_rec, sm, me, cm, expr, midi, midi_ok, ov):
    if key == ord(" "):
        sm.reset(); cm.reset(); expr.reset()
        if midi_ok: midi.panic()
        logger.info("PANIC")
    elif key == ord("r"):
        r_rec.reset(); l_rec.reset(); sm.reset(); cm.reset(); expr.reset()
        if midi_ok: midi.panic()
        logger.info("Full reset")
    elif key == ord("d"):
        ov.show_debug_info = not ov.show_debug_info
    elif key == ord("e"):
        expr.enabled = not expr.enabled
        logger.info(f"Expression CC: {'ON' if expr.enabled else 'OFF'}")
    elif key == ord("i"):
        inv = cm.cycle_inversion()
        inv_names = ["root position", "1st inversion", "2nd inversion"]
        logger.info(f"Inversion: {inv_names[inv]}")
        # Re-trigger current chord with new inversion if playing
        if sm.is_playing and midi_ok:
            mapped = cm.get_chord(sm.active_finger_count)
            if mapped:
                midi.change_chord(mapped.chord_info.midi_notes, mapped.chord_info.velocity)
                logger.info(f"  -> {mapped.display_name} [{' '.join(mapped.chord_info.note_names)}]")
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
    elif key in (82, 0):
        sm.reset()
        if midi_ok: midi.stop_chord()
        me.set_octave(me.octave + 1)
        logger.info(f"Octave: {me.octave}")
    elif key in (84, 1):
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