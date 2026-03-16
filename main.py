"""
GestureChord v2 — Two-hand gesture-to-MIDI chord controller.

All settings are loaded from config.yaml. Edit that file to customize.
Delete config.yaml to regenerate defaults.

Controls:
    ESC/Q=Quit  D=Debug  R=Reset  SPACE=Panic
    K=Key  M=Major/Minor  UP/DOWN=Octave
    I=Inversion  E=Expression  T=Test
"""

import sys
import time
import logging

import cv2

from vision.camera import Camera
from vision.hand_tracker import HandTracker
from vision.gesture_recognizer import GestureRecognizer
from engine.state_machine import GestureStateMachine, EventType
from engine.music_theory import MusicTheoryEngine
from engine.chord_mapper import ChordMapper, Modifier
from engine.expression import ExpressionController
from midi.midi_output import MidiOutput
from ui.overlay import Overlay, OverlayState
from utils.logger import setup_logger
from utils.config import load_config


def main():
    logger = setup_logger(name="gesturechord", level=logging.INFO)
    logger.info("=" * 60)
    logger.info("GestureChord v2")
    logger.info("=" * 60)

    # ── Load config ──
    cfg = load_config()

    # ── Build components from config ──
    camera = Camera(
        device_index=cfg.camera.index,
        width=cfg.camera.width, height=cfg.camera.height, mirror=True)

    tracker = HandTracker(
        max_hands=cfg.tracking.max_hands,
        detection_confidence=cfg.tracking.detection_confidence,
        tracking_confidence=cfg.tracking.tracking_confidence,
        camera_mirrored=True)

    right_rec = GestureRecognizer(
        hysteresis_high=cfg.gesture.hysteresis_high,
        hysteresis_low=cfg.gesture.hysteresis_low,
        rolling_window=cfg.gesture.rolling_window)
    left_rec = GestureRecognizer(
        hysteresis_high=cfg.gesture.hysteresis_high,
        hysteresis_low=cfg.gesture.hysteresis_low,
        rolling_window=cfg.gesture.rolling_window)

    sm = GestureStateMachine(
        confirmation_frames=cfg.state_machine.confirmation_frames,
        change_frames=cfg.state_machine.change_frames,
        settle_frames=cfg.state_machine.settle_frames,
        release_grace_ms=cfg.state_machine.release_grace_ms,
        idle_gesture=0)

    me = MusicTheoryEngine(
        root=cfg.music.key, scale=cfg.music.scale,
        octave=cfg.music.octave, velocity=cfg.music.velocity)

    cm = ChordMapper(music_engine=me, settle_frames=cfg.modifier.settle_frames)

    expr = ExpressionController(
        cc_number=cfg.expression.cc_number,
        zone_top=cfg.expression.zone_top,
        zone_bottom=cfg.expression.zone_bottom,
        smoothing_alpha=cfg.expression.smoothing,
        dead_zone=cfg.expression.dead_zone,
        enabled=cfg.expression.enabled)

    midi = MidiOutput(port_name=cfg.midi.port_name, channel=cfg.midi.channel)
    ov = Overlay(show_debug_info=cfg.display.start_in_debug)

    # State
    r_lost = 0; r_reset = False
    l_lost = 0; l_reset = False
    current_chord = None
    midi_ok = False

    # ── Init ──
    if not camera.open():
        logger.error("Cannot open camera.")
        sys.exit(1)

    tracker.initialize()
    midi_ok = midi.open()

    if not midi_ok:
        logger.warning("MIDI not available — PREVIEW MODE.")
    else:
        logger.info("MIDI ready!")

    logger.info(f"Key: {me.key_display} | Oct: {me.octave} | Scale: {cfg.display.scale}x")
    logger.info("Right hand: 1-5 = I-V | Left hand: 0=triad 1=7th 2=sus4 3=9th 4=vi 5=vii")
    logger.info(f"Expression: CC{cfg.expression.cc_number} | I=Inversion | E=Expr toggle")
    _print_chords(logger, me)

    # ===================================================================
    try:
        while True:
            frame = camera.read()
            if frame is None:
                time.sleep(0.01)
                continue

            tracking = tracker.process_frame(frame)

            # ── Right hand ──
            rg = None; rc = None; rs = False; rz = False
            rh = tracking.get_right_hand()
            if rh and rh.wrist.y < cfg.zone.threshold:
                rz = True
                rg = right_rec.recognize(rh)
                rc = rg.finger_count; rs = rg.is_stable
                r_lost = 0; r_reset = False
            else:
                r_lost += 1
                if r_lost >= cfg.zone.hand_lost_frames and not r_reset:
                    right_rec.reset(); r_reset = True

            # ── Left hand ──
            lg = None; lc = None; lz = False; ly = None
            lh = tracking.get_left_hand()
            if lh and lh.wrist.y < cfg.zone.threshold:
                lz = True
                lg = left_rec.recognize(lh)
                lc = lg.finger_count; ly = lh.wrist.y
                l_lost = 0; l_reset = False
            else:
                l_lost += 1
                if l_lost >= cfg.zone.hand_lost_frames and not l_reset:
                    left_rec.reset(); l_reset = True

            # ── Modifier + Expression ──
            mod_changed = cm.update_modifier(lc if lz else None)
            cc_val = expr.update(ly if lz else None)
            if cc_val is not None and midi_ok:
                midi.send_cc(expr.cc_number, cc_val)

            # ── State machine ──
            ev = sm.update(rc, rs)

            # ── Chord events ──
            triggered = False

            if ev.event_type == EventType.CHORD_ON:
                m = cm.get_chord(ev.finger_count)
                if m:
                    if midi_ok: midi.play_chord(m.chord_info.midi_notes, m.chord_info.velocity)
                    current_chord = m; triggered = True
                    _log(logger, "ON", m)

            elif ev.event_type == EventType.CHORD_CHANGE:
                m = cm.get_chord(ev.finger_count)
                if m:
                    if midi_ok: midi.change_chord(m.chord_info.midi_notes, m.chord_info.velocity)
                    current_chord = m; triggered = True
                    _log(logger, "CHANGE", m)

            elif ev.event_type == EventType.CHORD_OFF:
                if midi_ok: midi.stop_chord()
                current_chord = None
                logger.info("CHORD OFF")

            # Re-trigger on modifier change
            if mod_changed and not triggered and sm.is_playing:
                m = cm.get_chord(sm.active_finger_count)
                if m:
                    if midi_ok: midi.change_chord(m.chord_info.midi_notes, m.chord_info.velocity)
                    current_chord = m
                    _log(logger, "MOD", m)

            # ── Overlay ──
            os = OverlayState(
                tracking=tracking,
                right_gesture=rg, left_gesture=lg,
                right_in_zone=rz, left_in_zone=lz,
                chord_name=current_chord.display_name if current_chord else "",
                roman=current_chord.chord_info.roman_numeral if current_chord else "",
                notes=" ".join(current_chord.chord_info.note_names) if current_chord else "",
                chord_state=ev.state.name,
                confirm_progress=ev.confirmation_progress,
                key_display=me.key_display,
                modifier_name=cm.active_modifier_name or "triad",
                modifier_active=cm.active_modifier != Modifier.NONE,
                inversion=cm.inversion,
                cc_number=expr.cc_number, cc_value=expr.cc_value,
                cc_normalized=expr.cc_normalized, cc_enabled=expr.enabled,
                fps=camera.fps, inference_ms=tracking.inference_time_ms,
                midi_available=midi_ok,
                zone_threshold=cfg.zone.threshold,
            )

            # Pending chord preview
            if not current_chord and rc and rc > 0 and ev.state.name in ("CONFIRMING", "CHANGING", "DETECTING"):
                p = cm.get_chord(rc)
                if p:
                    os.chord_name = p.display_name
                    os.roman = p.chord_info.roman_numeral
                    os.notes = " ".join(p.chord_info.note_names)

            frame = ov.draw(frame, os)

            # Scale display
            if cfg.display.scale != 1.0:
                dw = int(frame.shape[1] * cfg.display.scale)
                dh = int(frame.shape[0] * cfg.display.scale)
                frame = cv2.resize(frame, (dw, dh), interpolation=cv2.INTER_LINEAR)

            cv2.imshow(cfg.display.window_name, frame)

            key = cv2.waitKey(1) & 0xFF
            if key == 27 or key == ord("q"):
                break
            _keys(key, logger, right_rec, left_rec, sm, me, cm, expr, midi, midi_ok, ov)

    except KeyboardInterrupt:
        pass
    finally:
        if midi_ok: midi.close()
        tracker.release()
        camera.release()
        cv2.destroyAllWindows()


def _log(logger, action, m):
    mod = f" [{m.modifier_name}]" if m.modifier_name else ""
    logger.info(f"{action}: {m.display_name}{mod} [{' '.join(m.chord_info.note_names)}]")


def _keys(key, log, rr, lr, sm, me, cm, ex, mi, mo, ov):
    if key == ord(" "):
        sm.reset(); cm.reset(); ex.reset()
        if mo: mi.panic()
        log.info("PANIC")
    elif key == ord("r"):
        rr.reset(); lr.reset(); sm.reset(); cm.reset(); ex.reset()
        if mo: mi.panic()
        log.info("Reset")
    elif key == ord("d"):
        ov.show_debug_info = not ov.show_debug_info
        log.info(f"{'Debug' if ov.show_debug_info else 'Performance'} mode")
    elif key == ord("e"):
        ex.enabled = not ex.enabled
        log.info(f"Expression: {'ON' if ex.enabled else 'OFF'}")
    elif key == ord("i"):
        inv = cm.cycle_inversion()
        names = ["root", "1st inv", "2nd inv"]
        log.info(f"Inversion: {names[inv]}")
        if sm.is_playing and mo:
            m = cm.get_chord(sm.active_finger_count)
            if m: mi.change_chord(m.chord_info.midi_notes, m.chord_info.velocity)
    elif key == ord("k"):
        sm.reset(); cm.reset()
        if mo: mi.stop_chord()
        me.cycle_root(1)
        log.info(f"Key: {me.key_display}")
        _print_chords(log, me)
    elif key == ord("m"):
        sm.reset(); cm.reset()
        if mo: mi.stop_chord()
        new = "natural_minor" if me.scale_name == "major" else "major"
        me.set_key(me.root, new)
        log.info(f"Scale: {me.key_display}")
        _print_chords(log, me)
    elif key == ord("t"):
        if mo: mi.send_test_note()
    elif key in (82, 0):
        sm.reset()
        if mo: mi.stop_chord()
        me.set_octave(me.octave + 1)
        log.info(f"Octave: {me.octave}")
    elif key in (84, 1):
        sm.reset()
        if mo: mi.stop_chord()
        me.set_octave(me.octave - 1)
        log.info(f"Octave: {me.octave}")


def _print_chords(log, me):
    for d in range(1, 6):
        c = me.get_chord_for_degree(d)
        if c: log.info(f"  {d}f = {c.roman_numeral} {c.chord_name} [{' '.join(c.note_names)}]")


if __name__ == "__main__":
    main()