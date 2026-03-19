"""
GestureChord v2 — Full Rhythm Suite + Chord Bank Presets.

Chord Bank (B): map any chord to any finger, bypass scale system.
Number keys 0-9 switch between presets when bank is active.

Controls:
    ESC/Q=Quit  SPACE=Panic  D=Debug  X=Full reset
    K=Key  M=Major/Minor  S=Scale  +/-=Octave
    I=Inversion  E=Expression  V=Velocity  T=Test
    R=Rhythm(pump)  G=Groove  F=Groove pattern  A=Arp  P=Arp pattern  [/]=BPM
    B=Toggle chord bank  0-9=Switch preset (when bank is ON)
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
from engine.chord_bank import ChordBank
from midi.midi_output import MidiOutput
from ui.overlay import Overlay, OverlayState
from utils.logger import setup_logger
from utils.config import load_config


def main():
    logger = setup_logger(name="gesturechord", level=logging.INFO)
    logger.info("=" * 60)
    logger.info("GestureChord v2 — Chord Bank Presets")
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
                                enabled=cfg.expression.enabled,
                                invert=True)
    expr2 = ExpressionController(cc_number=cfg.expression2.cc_number,
                                 zone_top=cfg.expression2.zone_left,
                                 zone_bottom=cfg.expression2.zone_right,
                                 smoothing_alpha=cfg.expression2.smoothing,
                                 dead_zone=cfg.expression2.dead_zone,
                                 enabled=cfg.expression2.enabled,
                                 invert=False)
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

    bank = ChordBank(presets=cfg.chord_bank.presets,
                     octave=cfg.chord_bank.octave,
                     active_preset=cfg.chord_bank.active_preset,
                     enabled=cfg.chord_bank.enabled)

    ov = Overlay(show_debug_info=cfg.display.start_in_debug)

    # State
    r_lost = 0; r_reset = False
    l_lost = 0; l_reset = False
    current_chord = None
    midi_ok = False
    latency_ms = 0.0
    _cached_chord_name = ""
    _cached_roman = ""
    _cached_notes = ""
    link_mode = [0]

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
    logger.info(f"Chord Bank: {'ON' if bank.enabled else 'OFF'} (B to toggle, 0-9 switch presets)")
    logger.info(f"  {bank.preset_count} presets loaded:")
    bank.print_all_presets(logger)
    logger.info("Keys: B=Bank 0-9=Preset R=Pump G=Groove F=Pattern A=Arp P=ArpPattern []=BPM")
    logger.info("      K=Key M=Mode S=Scale +/-=Oct I=Inv E=CC1 W=CC2 L=Link V=Vel D=Debug")
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
            lg = None; lc = None; lz = False; ly = None; lx = None
            lh = tracking.get_left_hand()
            if lh and lh.wrist.y < cfg.zone.threshold:
                lz = True; lg = left_rec.recognize(lh)
                lc = lg.finger_count; ly = lh.wrist.y; lx = lh.wrist.x
                l_lost = 0; l_reset = False
            else:
                l_lost += 1
                if l_lost >= cfg.zone.hand_lost_frames and not l_reset:
                    left_rec.reset(); l_reset = True

            # ── Modifier + Expression ──
            mod_changed = cm.update_modifier(lc if lz else None)
            cc_val = expr.update(ly if lz else None)
            if cc_val is not None and midi_ok and link_mode[0] != 2:
                midi.send_cc(expr.cc_number, cc_val)
            cc2_val = expr2.update(lx if lz else None)
            if cc2_val is not None and midi_ok and link_mode[0] != 1:
                midi.send_cc(expr2.cc_number, cc2_val)

            # ── State machine ──
            ev = sm.update(rc, rs)
            tv = vel.get_trigger_velocity() if vel.enabled else cfg.music.velocity

            # ── Helper: get notes ──
            def _get_notes(finger_count):
                if bank.enabled:
                    bc = bank.get_chord(finger_count)
                    if bc:
                        return bc.midi_notes, bc.name, "", " ".join(bc.note_names)
                    return None, "", "", ""
                else:
                    m = cm.get_chord(finger_count)
                    if m:
                        return m.chord_info.midi_notes, m.display_name, m.chord_info.roman_numeral, " ".join(m.chord_info.note_names)
                    return None, "", "", ""

            # ── MIDI events ──
            triggered = False

            if ev.event_type == EventType.CHORD_ON:
                notes, name, roman, note_str = _get_notes(ev.finger_count)
                if notes:
                    if midi_ok:
                        if groove.enabled: groove.set_chord(notes, tv)
                        elif arp.enabled: arp.set_chord(notes, tv)
                        else: midi.play_chord(notes, tv)
                    current_chord = _ChordState(notes, name, roman, note_str)
                    triggered = True
                    _cached_chord_name = name; _cached_roman = roman; _cached_notes = note_str
                    latency_ms = (time.perf_counter() - t0) * 1000
                    logger.info(f"ON: {name} [{note_str}] v={tv}" +
                                (f" ({latency_ms:.0f}ms)" if latency_ms else ""))

            elif ev.event_type == EventType.CHORD_CHANGE:
                notes, name, roman, note_str = _get_notes(ev.finger_count)
                if notes:
                    if midi_ok:
                        if groove.enabled: groove.set_chord(notes, tv)
                        elif arp.enabled: arp.set_chord(notes, tv)
                        else: midi.change_chord(notes, tv)
                    current_chord = _ChordState(notes, name, roman, note_str)
                    triggered = True
                    _cached_chord_name = name; _cached_roman = roman; _cached_notes = note_str
                    latency_ms = (time.perf_counter() - t0) * 1000
                    logger.info(f"CHANGE: {name} [{note_str}] v={tv}" +
                                (f" ({latency_ms:.0f}ms)" if latency_ms else ""))

            elif ev.event_type == EventType.CHORD_OFF:
                if midi_ok:
                    if groove.enabled: groove.stop()
                    elif arp.enabled: arp.stop()
                    else: midi.stop_chord()
                current_chord = None; rhythm.reset()
                _cached_chord_name = ""; _cached_roman = ""; _cached_notes = ""

            # Modifier re-trigger (scale mode only)
            if not bank.enabled and mod_changed and not triggered and sm.is_playing:
                m = cm.get_chord(sm.active_finger_count)
                if m:
                    if midi_ok:
                        if groove.enabled: groove.set_chord(m.chord_info.midi_notes, tv)
                        elif arp.enabled: arp.set_chord(m.chord_info.midi_notes, tv)
                        else: midi.change_chord(m.chord_info.midi_notes, tv)
                    current_chord = _ChordState(
                        m.chord_info.midi_notes, m.display_name,
                        m.chord_info.roman_numeral, " ".join(m.chord_info.note_names))
                    _cached_chord_name = m.display_name
                    _cached_roman = m.chord_info.roman_numeral
                    _cached_notes = " ".join(m.chord_info.note_names)

            # ── Pump retrigger ──
            pump = rhythm.update(rwy if rz else None)
            if pump and sm.is_playing and current_chord and midi_ok:
                if not groove.enabled and not arp.enabled:
                    midi.play_chord(current_chord.midi_notes, pump.velocity)

            # ── Groove / Arp tick ──
            if groove.enabled and midi_ok: groove.tick()
            elif arp.enabled and midi_ok: arp.tick()

            t3 = time.perf_counter()

            # ── Overlay ──
            # Build key_display for overlay
            if bank.enabled:
                key_disp = f"BANK [{bank.active_preset_index}] {bank.active_preset_name}"
            else:
                key_disp = me.key_display

            os = OverlayState(
                tracking=tracking,
                right_gesture=rg, left_gesture=lg,
                right_in_zone=rz, left_in_zone=lz,
                chord_name=_cached_chord_name,
                roman=_cached_roman,
                notes=_cached_notes,
                chord_state=ev.state.name,
                confirm_progress=ev.confirmation_progress,
                key_display=key_disp,
                modifier_name=(cm.active_modifier_name or "triad") if not bank.enabled else "",
                modifier_active=(cm.active_modifier != Modifier.NONE) if not bank.enabled else False,
                inversion=cm.inversion,
                cc_number=expr.cc_number, cc_value=expr.cc_value,
                cc_normalized=expr.cc_normalized, cc_enabled=expr.enabled,
                cc2_number=expr2.cc_number, cc2_value=expr2.cc_value,
                cc2_normalized=expr2.cc_normalized, cc2_enabled=expr2.enabled,
                link_mode=link_mode[0],
                fps=camera.fps, inference_ms=tracking.inference_time_ms,
                midi_available=midi_ok, zone_threshold=cfg.zone.threshold,
                rhythm_enabled=rhythm.enabled,
                rhythm_pumping=rhythm.is_pumping,
                groove_enabled=groove.enabled,
                groove_pattern=groove.pattern_name,
                groove_bpm=groove.bpm,
                arp_enabled=arp.enabled,
                arp_pattern=arp.pattern_name,
                arp_bpm=arp.bpm,
                velocity_enabled=vel.enabled,
                velocity_value=vel.velocity,
                chord_triggered=triggered or (pump is not None and sm.is_playing and not groove.enabled and not arp.enabled),
            )

            if not current_chord and rc and rc > 0 and ev.state.name in ("CONFIRMING", "CHANGING", "DETECTING"):
                notes, name, roman, note_str = _get_notes(rc)
                if notes:
                    os.chord_name = name; os.roman = roman; os.notes = note_str

            frame = ov.draw(frame, os)

            t4 = time.perf_counter()
            if ov.show_debug_info:
                h_f, w_f = frame.shape[:2]
                total = (t4 - t0) * 1000
                timing = f"C:{(t1-t0)*1000:.0f} T:{(t2-t1)*1000:.0f} M:{(t3-t2)*1000:.0f} O:{(t4-t3)*1000:.0f} ={total:.0f}ms"
                if latency_ms > 0: timing += f"  LAT:{latency_ms:.0f}ms"
                cv2.putText(frame, timing, (8, h_f-8),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.3, (0, 150, 150), 1, cv2.LINE_AA)

            if cfg.display.scale != 1.0:
                h_f, w_f = frame.shape[:2]
                dw = int(w_f * cfg.display.scale)
                dh = int(h_f * cfg.display.scale)
                frame = cv2.resize(frame, (dw, dh), interpolation=cv2.INTER_NEAREST)

            cv2.imshow(cfg.display.window_name, frame)

            key = cv2.waitKeyEx(1)
            if key == 27 or key == ord("q"): break
            _keys(key, logger, right_rec, left_rec, sm, me, cm, expr,
                  expr2, vel, arp, rhythm, groove, midi, midi_ok, ov, link_mode, bank)

    except KeyboardInterrupt:
        pass
    finally:
        groove.stop(); arp.stop()
        if midi_ok: midi.close()
        tracker.release(); camera.release()
        cv2.destroyAllWindows()


class _ChordState:
    def __init__(self, midi_notes, name, roman, note_str):
        self.midi_notes = midi_notes
        self.name = name
        self.roman = roman
        self.note_str = note_str


def _keys(key, log, rr, lr, sm, me, cm, ex, ex2, vel, arp, rhy, grv, mi, mo, ov, lm, bank):
    if key == ord(" "):
        sm.reset(); cm.reset(); ex.reset(); ex2.reset(); vel.reset()
        arp.stop(); rhy.reset(); grv.stop()
        lm[0] = 0
        if mo: mi.panic()
        log.info("PANIC")
    elif key == ord("b"):
        bank.enabled = not bank.enabled
        sm.reset()
        if mo: mi.stop_chord()
        if bank.enabled:
            log.info("CHORD BANK ON:")
            bank.print_bank(log)
            log.info("  Press 0-9 to switch presets")
        else:
            log.info("CHORD BANK OFF — scale mode")
            _print_chords(log, me)
    # Number keys 0-9 switch presets when bank is active
    elif key in range(ord("0"), ord("9") + 1) and bank.enabled:
        idx = key - ord("0")
        if bank.switch_preset(idx):
            sm.reset()
            if mo: mi.stop_chord()
            log.info(f"Preset switched to [{idx}]:")
            bank.print_bank(log)
        else:
            log.info(f"No preset at index {idx} (have {bank.preset_count})")
    elif key == ord("x"):
        rr.reset(); lr.reset(); sm.reset(); cm.reset(); ex.reset(); ex2.reset()
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
            if arp.enabled: arp.enabled = False; arp.stop()
            log.info(f"Groove ON: {grv.pattern_name} {grv.bpm:.0f}bpm")
            if sm.is_playing and mo:
                if bank.enabled:
                    bc = bank.get_chord(sm.active_finger_count)
                    if bc: grv.set_chord(bc.midi_notes, vel.velocity)
                else:
                    m = cm.get_chord(sm.active_finger_count)
                    if m: grv.set_chord(m.chord_info.midi_notes, vel.velocity)
        else:
            grv.stop()
            if mo: mi.stop_chord()
            log.info("Groove OFF")
    elif key == ord("f"):
        log.info(f"Groove pattern: {grv.cycle_pattern()}")
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
        if grv.enabled: log.info(f"Groove BPM: {grv.adjust_bpm(-10):.0f}")
        else: log.info(f"Arp BPM: {arp.adjust_bpm(-20):.0f}")
    elif key == ord("]"):
        if grv.enabled: log.info(f"Groove BPM: {grv.adjust_bpm(10):.0f}")
        else: log.info(f"Arp BPM: {arp.adjust_bpm(20):.0f}")
    elif key == ord("d"):
        ov.show_debug_info = not ov.show_debug_info
    elif key == ord("e"):
        ex.enabled = not ex.enabled
        log.info(f"Expression CC{ex.cc_number}: {'ON' if ex.enabled else 'OFF'}")
    elif key == ord("w"):
        ex2.enabled = not ex2.enabled
        log.info(f"Expression2 CC{ex2.cc_number} (X-axis): {'ON' if ex2.enabled else 'OFF'}")
    elif key == ord("l"):
        lm[0] = (lm[0] + 1) % 3
        if lm[0] == 0: log.info("LINK MODE OFF — both CC1 + CC2 active")
        elif lm[0] == 1: log.info(f"LINK MODE: CC{ex.cc_number} SOLO")
        elif lm[0] == 2: log.info(f"LINK MODE: CC{ex2.cc_number} SOLO")
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
        me.set_octave(me.octave + 1)
        if bank.enabled: bank.set_octave(me.octave)
        log.info(f"Octave UP: {me.octave}")
    elif key in (2621440, ord("-"), ord("_")):
        sm.reset()
        if mo: mi.stop_chord()
        me.set_octave(me.octave - 1)
        if bank.enabled: bank.set_octave(me.octave)
        log.info(f"Octave DOWN: {me.octave}")


def _print_chords(log, me):
    for d in range(1, min(8, me.num_degrees + 1)):
        c = me.get_chord_for_degree(d)
        if c:
            log.info(f"  {d} = {c.roman_numeral} {c.chord_name} [{' '.join(c.note_names)}]"
                     + (" <" if d <= 5 else " (SHIFT)"))


if __name__ == "__main__":
    main()