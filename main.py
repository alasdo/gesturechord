"""
GestureChord v2 — Full Rhythm Suite.

Rhythm modes (one active at a time):
    Pump (R): pump hand up/down to retrigger chords manually
    Groove (G): automatic rhythm patterns — just select chords
    Arp (A): sequential note playback

Controls:
    ESC/Q=Quit  SPACE=Panic  D=Debug  X=Full reset
    K=Key  M=Major/Minor  S=Scale  +/-=Octave
    I=Inversion  E=Expression  V=Velocity  T=Test
    R=Rhythm(pump)  G=Groove  F=Groove pattern  A=Arp  P=Arp pattern  [/]=BPM
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
from engine.velocity import VelocityController
from engine.arpeggiator import Arpeggiator, ArpPattern
from engine.rhythm_engine import RhythmEngine
from engine.groove_patterns import GrooveEngine
from midi.midi_output import MidiOutput
from ui.overlay import Overlay, OverlayState
from utils.logger import setup_logger
from utils.config import load_config


def main():
    logger = setup_logger(name="gesturechord", level=logging.INFO)
    logger.info("=" * 60)
    logger.info("GestureChord v2 — Full Rhythm Suite")
    logger.info("=" * 60)

    cfg = load_config()

    # ── Build components ──
    camera = Camera(device_index=cfg.camera.index, width=cfg.camera.width,
                    height=cfg.camera.height, mirror=True)
    tracker = HandTracker(max_hands=cfg.tracking.max_hands,
                          detection_confidence=cfg.tracking.detection_confidence,
                          tracking_confidence=cfg.tracking.tracking_confidence,
                          camera_mirrored=True)
    right_rec = GestureRecognizer(hysteresis_high=cfg.gesture.hysteresis_high,
                                  hysteresis_low=cfg.gesture.hysteresis_low,
                                  rolling_window=cfg.gesture.rolling_window)
    left_rec = GestureRecognizer(hysteresis_high=cfg.gesture.hysteresis_high,
                                 hysteresis_low=cfg.gesture.hysteresis_low,
                                 rolling_window=cfg.gesture.rolling_window)
    sm = GestureStateMachine(confirmation_frames=cfg.state_machine.confirmation_frames,
                             change_frames=cfg.state_machine.change_frames,
                             settle_frames=cfg.state_machine.settle_frames,
                             release_grace_ms=cfg.state_machine.release_grace_ms,
                             idle_gesture=0)
    me = MusicTheoryEngine(root=cfg.music.key, scale=cfg.music.scale,
                           octave=cfg.music.octave, velocity=cfg.music.velocity)
    cm = ChordMapper(music_engine=me, settle_frames=cfg.modifier.settle_frames)
    expr = ExpressionController(cc_number=cfg.expression.cc_number,
                                zone_top=cfg.expression.zone_top,
                                zone_bottom=cfg.expression.zone_bottom,
                                smoothing_alpha=cfg.expression.smoothing,
                                dead_zone=cfg.expression.dead_zone,
                                enabled=cfg.expression.enabled)
    vel = VelocityController(min_velocity=cfg.velocity.min_velocity,
                             max_velocity=cfg.velocity.max_velocity,
                             speed_low=cfg.velocity.speed_low,
                             speed_high=cfg.velocity.speed_high,
                             enabled=cfg.velocity.enabled)
    rhythm = RhythmEngine(velocity_threshold=cfg.rhythm.velocity_threshold,
                          cooldown_ms=cfg.rhythm.cooldown_ms,
                          smoothing_alpha=cfg.rhythm.smoothing,
                          min_velocity=cfg.rhythm.min_velocity,
                          max_velocity=cfg.rhythm.max_velocity,
                          speed_for_max=cfg.rhythm.speed_for_max,
                          enabled=cfg.rhythm.enabled)
    midi = MidiOutput(port_name=cfg.midi.port_name, channel=cfg.midi.channel)

    arp_map = {"up": ArpPattern.UP, "down": ArpPattern.DOWN,
               "up_down": ArpPattern.UP_DOWN, "random": ArpPattern.RANDOM}
    arp = Arpeggiator(midi_output=midi, bpm=cfg.arpeggiator.bpm,
                      pattern=arp_map.get(cfg.arpeggiator.pattern, ArpPattern.UP),
                      enabled=cfg.arpeggiator.enabled,
                      octave_range=cfg.arpeggiator.octave_range)

    groove = GrooveEngine(midi_output=midi, bpm=cfg.groove.bpm,
                          pattern_name=cfg.groove.pattern,
                          gate_length=cfg.groove.gate_length,
                          humanize_ms=cfg.groove.humanize_ms,
                          enabled=cfg.groove.enabled)

    ov = Overlay(show_debug_info=cfg.display.start_in_debug)

    # State
    r_lost = 0; r_reset = False
    l_lost = 0; l_reset = False
    current_chord = None
    midi_ok = False
    latency_ms = 0.0

    # ── Init ──
    if not camera.open():
        logger.error("Cannot open camera."); sys.exit(1)
    tracker.initialize()
    midi_ok = midi.open()
    if not midi_ok:
        logger.warning("MIDI not available — PREVIEW MODE.")
    else:
        logger.info("MIDI ready!")

    logger.info(f"Key: {me.key_display} | Oct: {me.octave}")
    logger.info("Right: 1-5=I-V | Left: 0=triad 1=7th 2=sus4 3=9th 4=SHIFT 5=SHIFT+7")
    logger.info(f"Pump: {'ON' if rhythm.enabled else 'OFF'} | "
                f"Groove: {'OFF' if not groove.enabled else groove.pattern_name + ' ' + str(int(groove.bpm)) + 'bpm'} | "
                f"Arp: {'OFF' if not arp.enabled else arp.pattern_name}")
    logger.info("Keys: R=Pump G=Groove F=GroovePattern A=Arp P=ArpPattern []=BPM")
    logger.info("      K=Key M=Mode S=Scale +/-=Oct I=Inv E=Expr V=Vel D=Debug X=Reset")
    _print_chords(logger, me)

    # ===================================================================
    try:
        while True:
            t0 = time.perf_counter()

            frame = camera.read()
            if frame is None:
                time.sleep(0.001); continue

            t1 = time.perf_counter()
            tracking = tracker.process_frame(frame)
            t2 = time.perf_counter()

            # ── Right hand ──
            rg = None; rc = None; rs = False; rz = False; rwy = None
            rh = tracking.get_right_hand()
            if rh and rh.wrist.y < cfg.zone.threshold:
                rz = True; rg = right_rec.recognize(rh)
                rc = rg.finger_count; rs = rg.is_stable
                vel.update(rh.wrist.x, rh.wrist.y); rwy = rh.wrist.y
                r_lost = 0; r_reset = False
            else:
                vel.update(None, None); rwy = None
                r_lost += 1
                if r_lost >= cfg.zone.hand_lost_frames and not r_reset:
                    right_rec.reset(); r_reset = True

            # ── Left hand ──
            lg = None; lc = None; lz = False; ly = None
            lh = tracking.get_left_hand()
            if lh and lh.wrist.y < cfg.zone.threshold:
                lz = True; lg = left_rec.recognize(lh)
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
            tv = vel.get_trigger_velocity() if vel.enabled else cfg.music.velocity

            # ── MIDI: chord state events ──
            triggered = False

            if ev.event_type == EventType.CHORD_ON:
                m = cm.get_chord(ev.finger_count)
                if m:
                    if midi_ok:
                        if groove.enabled:
                            groove.set_chord(m.chord_info.midi_notes, tv)
                        elif arp.enabled:
                            arp.set_chord(m.chord_info.midi_notes, tv)
                        else:
                            midi.play_chord(m.chord_info.midi_notes, tv)
                    current_chord = m; triggered = True
                    latency_ms = (time.perf_counter() - t0) * 1000
                    _log(logger, "ON", m, tv, latency_ms)

            elif ev.event_type == EventType.CHORD_CHANGE:
                m = cm.get_chord(ev.finger_count)
                if m:
                    if midi_ok:
                        if groove.enabled:
                            groove.set_chord(m.chord_info.midi_notes, tv)
                        elif arp.enabled:
                            arp.set_chord(m.chord_info.midi_notes, tv)
                        else:
                            midi.change_chord(m.chord_info.midi_notes, tv)
                    current_chord = m; triggered = True
                    latency_ms = (time.perf_counter() - t0) * 1000
                    _log(logger, "CHANGE", m, tv, latency_ms)

            elif ev.event_type == EventType.CHORD_OFF:
                if midi_ok:
                    if groove.enabled: groove.stop()
                    elif arp.enabled: arp.stop()
                    else: midi.stop_chord()
                current_chord = None; rhythm.reset()

            # Modifier re-trigger
            if mod_changed and not triggered and sm.is_playing:
                m = cm.get_chord(sm.active_finger_count)
                if m:
                    if midi_ok:
                        if groove.enabled:
                            groove.set_chord(m.chord_info.midi_notes, tv)
                        elif arp.enabled:
                            arp.set_chord(m.chord_info.midi_notes, tv)
                        else:
                            midi.change_chord(m.chord_info.midi_notes, tv)
                    current_chord = m

            # ── Pump retrigger (only when no groove/arp) ──
            pump = rhythm.update(rwy if rz else None)
            if pump and sm.is_playing and current_chord and midi_ok:
                if not groove.enabled and not arp.enabled:
                    midi.play_chord(current_chord.chord_info.midi_notes, pump.velocity)

            # ── Groove / Arp tick ──
            if groove.enabled and midi_ok:
                groove.tick()
            elif arp.enabled and midi_ok:
                arp.tick()

            t3 = time.perf_counter()

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
                midi_available=midi_ok, zone_threshold=cfg.zone.threshold,
            )

            if not current_chord and rc and rc > 0 and ev.state.name in ("CONFIRMING", "CHANGING", "DETECTING"):
                p = cm.get_chord(rc)
                if p:
                    os.chord_name = p.display_name
                    os.roman = p.chord_info.roman_numeral
                    os.notes = " ".join(p.chord_info.note_names)

            frame = ov.draw(frame, os)

            h_f, w_f = frame.shape[:2]
            _draw_status(frame, vel, arp, rhythm, groove, latency_ms,
                         t1-t0, t2-t1, t3-t2, ov.show_debug_info, w_f, h_f)

            if cfg.display.scale != 1.0:
                dw = int(w_f * cfg.display.scale)
                dh = int(h_f * cfg.display.scale)
                frame = cv2.resize(frame, (dw, dh), interpolation=cv2.INTER_LINEAR)

            cv2.imshow(cfg.display.window_name, frame)

            key = cv2.waitKeyEx(1)
            if key == 27 or key == ord("q"): break
            _keys(key, logger, right_rec, left_rec, sm, me, cm, expr,
                  vel, arp, rhythm, groove, midi, midi_ok, ov)

    except KeyboardInterrupt:
        pass
    finally:
        groove.stop(); arp.stop()
        if midi_ok: midi.close()
        tracker.release(); camera.release()
        cv2.destroyAllWindows()


def _draw_status(frame, vel, arp, rhy, grv, lat, cap, trk, proc, dbg, w, h):
    parts = []
    if vel.enabled: parts.append(f"VEL:{vel.velocity}")
    if grv.enabled: parts.append(f"GRV:{grv.pattern_name} {grv.bpm:.0f}")
    elif arp.enabled: parts.append(f"ARP:{arp.pattern_name} {arp.bpm:.0f}")
    elif rhy.enabled: parts.append("PUMP" if rhy.is_pumping else "pump")
    if dbg:
        parts.append(f"C:{cap*1000:.0f} T:{trk*1000:.0f} P:{proc*1000:.0f}ms")
        if lat > 0: parts.append(f"L:{lat:.0f}ms")
    cv2.putText(frame, "  ".join(parts), (8, h-8),
                cv2.FONT_HERSHEY_SIMPLEX, 0.32, (0, 170, 170), 1, cv2.LINE_AA)


def _log(logger, action, m, v=100, lat=0):
    mod = f" [{m.modifier_name}]" if m.modifier_name else ""
    logger.info(f"{action}: {m.display_name}{mod} [{' '.join(m.chord_info.note_names)}] v={v}"
                + (f" ({lat:.0f}ms)" if lat else ""))


def _keys(key, log, rr, lr, sm, me, cm, ex, vel, arp, rhy, grv, mi, mo, ov):
    if key == ord(" "):
        sm.reset(); cm.reset(); ex.reset(); vel.reset()
        arp.stop(); rhy.reset(); grv.stop()
        if mo: mi.panic()
        log.info("PANIC")
    elif key == ord("x"):
        rr.reset(); lr.reset(); sm.reset(); cm.reset(); ex.reset()
        vel.reset(); arp.stop(); rhy.reset(); grv.stop()
        if mo: mi.panic()
        log.info("Full reset")
    elif key == ord("r"):
        rhy.enabled = not rhy.enabled
        if not rhy.enabled: rhy.reset()
        log.info(f"Pump rhythm: {'ON' if rhy.enabled else 'OFF'}")
    elif key == ord("g"):
        grv.enabled = not grv.enabled
        if grv.enabled:
            # Disable arp when groove is on
            if arp.enabled: arp.enabled = False; arp.stop()
            log.info(f"Groove ON: {grv.pattern_name} {grv.bpm:.0f}bpm")
            # Start playing if a chord is already held
            if sm.is_playing and mo:
                m = cm.get_chord(sm.active_finger_count)
                if m: grv.set_chord(m.chord_info.midi_notes, vel.velocity)
        else:
            grv.stop()
            if mo: mi.stop_chord()
            log.info("Groove OFF")
    elif key == ord("f"):
        name = grv.cycle_pattern()
        log.info(f"Groove pattern: {name}")
    elif key == ord("a"):
        arp.enabled = not arp.enabled
        if arp.enabled:
            if grv.enabled: grv.enabled = False; grv.stop()
            log.info(f"Arp ON: {arp.pattern_name} {arp.bpm:.0f}bpm")
        else:
            arp.stop()
            if mo: mi.stop_chord()
            log.info("Arp OFF")
    elif key == ord("p"):
        arp.cycle_pattern()
        log.info(f"Arp pattern: {arp.pattern_name}")
    elif key == ord("["):
        if grv.enabled:
            log.info(f"Groove BPM: {grv.adjust_bpm(-10):.0f}")
        else:
            log.info(f"Arp BPM: {arp.adjust_bpm(-20):.0f}")
    elif key == ord("]"):
        if grv.enabled:
            log.info(f"Groove BPM: {grv.adjust_bpm(10):.0f}")
        else:
            log.info(f"Arp BPM: {arp.adjust_bpm(20):.0f}")
    elif key == ord("d"):
        ov.show_debug_info = not ov.show_debug_info
    elif key == ord("e"):
        ex.enabled = not ex.enabled
        log.info(f"Expression: {'ON' if ex.enabled else 'OFF'}")
    elif key == ord("v"):
        vel.enabled = not vel.enabled
        log.info(f"Velocity: {'ON' if vel.enabled else 'OFF'}")
    elif key == ord("i"):
        inv = cm.cycle_inversion()
        log.info(f"Inversion: {['root', '1st', '2nd'][inv]}")
        if sm.is_playing and mo:
            m = cm.get_chord(sm.active_finger_count)
            if m: mi.change_chord(m.chord_info.midi_notes, m.chord_info.velocity)
    elif key == ord("k"):
        sm.reset(); cm.reset()
        if mo: mi.stop_chord()
        me.cycle_root(1); log.info(f"Key: {me.key_display}")
        _print_chords(log, me)
    elif key == ord("m"):
        sm.reset(); cm.reset()
        if mo: mi.stop_chord()
        me.set_key(me.root, "natural_minor" if me.scale_name == "major" else "major")
        log.info(f"Scale: {me.key_display}"); _print_chords(log, me)
    elif key == ord("s"):
        sm.reset(); cm.reset()
        if mo: mi.stop_chord()
        me.cycle_scale(1); log.info(f"Scale: {me.key_display}")
        _print_chords(log, me)
    elif key == ord("t"):
        if mo: mi.send_test_note()
    elif key in (2490368, ord("="), ord("+")):
        sm.reset()
        if mo: mi.stop_chord()
        me.set_octave(me.octave + 1); log.info(f"Octave UP: {me.octave}")
    elif key in (2621440, ord("-"), ord("_")):
        sm.reset()
        if mo: mi.stop_chord()
        me.set_octave(me.octave - 1); log.info(f"Octave DOWN: {me.octave}")


def _print_chords(log, me):
    for d in range(1, min(8, me.num_degrees + 1)):
        c = me.get_chord_for_degree(d)
        if c:
            log.info(f"  {d} = {c.roman_numeral} {c.chord_name} [{' '.join(c.note_names)}]"
                     + (" <" if d <= 5 else " (SHIFT)"))


if __name__ == "__main__":
    main()