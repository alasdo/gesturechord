"""
Overlay v3 — clean, animated, rhythm-aware.

Performance mode layout:
    ┌──────────────────────────────────────────────────┐
    │                                        [R:3][L:1]│
    │                                        [MOD:7th ]│
    │                                        [INV:root]│
    │                                        [CC1 ████]│
    │                                                  │
    │               (hands here)                       │
    │                                                  │
    │─────────────── zone line ────────────────────────│
    │ [pump]  [GRV:sync 120]  [VEL:85]                │
    │         ┌───────────────────────────┐            │
    │         │  C major                  │            │
    │         │  I     Cmaj7              │            │
    │         │        C E G B            │            │
    │         │  [████████████████░░░░░░] │            │
    │         └───────────────────────────┘            │
    └──────────────────────────────────────────────────┘

Animations:
    - Chord panel border pulses bright on each chord hit (ON/CHANGE/pump)
    - Rhythm beat dot blinks on each groove hit
    - Confirmation bar fills smoothly
"""

import cv2
import numpy as np
import time
from typing import Optional
from dataclasses import dataclass, field

from vision.hand_tracker import HandData, LandmarkIndex, TrackingResult
from vision.gesture_recognizer import GestureResult


# ── Colors (BGR) ──
BG = (18, 18, 18)
BORDER = (55, 55, 55)
WHITE = (245, 245, 245)
GRAY = (150, 150, 150)
DIM = (70, 70, 70)
GREEN = (0, 220, 0)
YELLOW = (0, 210, 255)
RED = (0, 0, 210)
CYAN = (255, 210, 0)
TEAL = (180, 200, 0)
ACCENT = (255, 150, 0)       # Bright blue for active elements
PULSE_COLOR = (255, 255, 100) # Bright flash on chord hit
BEAT_DOT = (0, 180, 255)     # Orange beat indicator
SKELETON_PERF = (100, 95, 0)
SKELETON_DBG = (180, 170, 0)
JOINT_COLOR = (0, 170, 210)
TIP_UP = (0, 220, 0)
TIP_DN = (0, 0, 170)

# Skeleton connections
HAND_CONNECTIONS = [
    (LandmarkIndex.WRIST, LandmarkIndex.THUMB_CMC),
    (LandmarkIndex.THUMB_CMC, LandmarkIndex.THUMB_MCP),
    (LandmarkIndex.THUMB_MCP, LandmarkIndex.THUMB_IP),
    (LandmarkIndex.THUMB_IP, LandmarkIndex.THUMB_TIP),
    (LandmarkIndex.WRIST, LandmarkIndex.INDEX_MCP),
    (LandmarkIndex.INDEX_MCP, LandmarkIndex.INDEX_PIP),
    (LandmarkIndex.INDEX_PIP, LandmarkIndex.INDEX_DIP),
    (LandmarkIndex.INDEX_DIP, LandmarkIndex.INDEX_TIP),
    (LandmarkIndex.WRIST, LandmarkIndex.MIDDLE_MCP),
    (LandmarkIndex.MIDDLE_MCP, LandmarkIndex.MIDDLE_PIP),
    (LandmarkIndex.MIDDLE_PIP, LandmarkIndex.MIDDLE_DIP),
    (LandmarkIndex.MIDDLE_DIP, LandmarkIndex.MIDDLE_TIP),
    (LandmarkIndex.WRIST, LandmarkIndex.RING_MCP),
    (LandmarkIndex.RING_MCP, LandmarkIndex.RING_PIP),
    (LandmarkIndex.RING_PIP, LandmarkIndex.RING_DIP),
    (LandmarkIndex.RING_DIP, LandmarkIndex.RING_TIP),
    (LandmarkIndex.WRIST, LandmarkIndex.PINKY_MCP),
    (LandmarkIndex.PINKY_MCP, LandmarkIndex.PINKY_PIP),
    (LandmarkIndex.PINKY_PIP, LandmarkIndex.PINKY_DIP),
    (LandmarkIndex.PINKY_DIP, LandmarkIndex.PINKY_TIP),
    (LandmarkIndex.INDEX_MCP, LandmarkIndex.MIDDLE_MCP),
    (LandmarkIndex.MIDDLE_MCP, LandmarkIndex.RING_MCP),
    (LandmarkIndex.RING_MCP, LandmarkIndex.PINKY_MCP),
    (LandmarkIndex.PINKY_MCP, LandmarkIndex.WRIST),
]


@dataclass
class OverlayState:
    """All data the overlay needs to render one frame."""
    tracking: TrackingResult
    right_gesture: Optional[GestureResult] = None
    left_gesture: Optional[GestureResult] = None
    right_in_zone: bool = False
    left_in_zone: bool = False
    chord_name: str = ""
    roman: str = ""
    notes: str = ""
    chord_state: str = "IDLE"
    confirm_progress: float = 0.0
    key_display: str = ""
    modifier_name: str = ""
    modifier_active: bool = False
    inversion: int = 0
    cc_number: int = 1
    cc_value: int = 0
    cc_normalized: float = 0.0
    cc_enabled: bool = True
    cc2_number: int = 74
    cc2_value: int = 0
    cc2_normalized: float = 0.0
    cc2_enabled: bool = False
    fps: float = 0.0
    inference_ms: float = 0.0
    midi_available: bool = True
    zone_threshold: float = 0.75
    # Rhythm features
    rhythm_enabled: bool = False
    rhythm_pumping: bool = False
    groove_enabled: bool = False
    groove_pattern: str = ""
    groove_bpm: float = 120.0
    arp_enabled: bool = False
    arp_pattern: str = ""
    arp_bpm: float = 160.0
    velocity_enabled: bool = False
    velocity_value: int = 100
    # Triggers (set True on the frame a hit occurs)
    chord_triggered: bool = False


class Overlay:
    """
    Draws all visual feedback with animations.

    Performance mode: minimal, glanceable, rhythm-aware.
    Debug mode: adds skeletons, finger ratios, timing.
    """

    def __init__(self, show_debug_info: bool = False):
        self.show_debug_info = show_debug_info
        # Animation state
        self._pulse_start: float = 0.0
        self._pulse_duration: float = 0.15  # seconds

    def trigger_pulse(self):
        """Call when a chord hit occurs (ON, CHANGE, pump, groove hit)."""
        self._pulse_start = time.perf_counter()

    @property
    def _pulse_intensity(self) -> float:
        """0.0-1.0, decays from 1.0 to 0.0 over pulse_duration."""
        elapsed = time.perf_counter() - self._pulse_start
        if elapsed > self._pulse_duration:
            return 0.0
        return 1.0 - (elapsed / self._pulse_duration)

    def draw(self, frame: np.ndarray, state: OverlayState) -> np.ndarray:
        h, w = frame.shape[:2]

        # Trigger pulse animation on chord hit
        if state.chord_triggered:
            self.trigger_pulse()

        # ── Always drawn ──
        self._draw_chord_panel(frame, state, w, h)
        self._draw_hand_badges(frame, state, w)
        self._draw_info_strip(frame, state, w)
        self._draw_cc_bar(frame, state, w)
        self._draw_rhythm_bar(frame, state, w, h)
        self._draw_zone_line(frame, state, w, h)

        # Hand skeletons
        # Debug-only elements
        if self.show_debug_info:
            if state.tracking.has_hands:
                for hand in state.tracking.hands:
                    self._draw_skeleton(frame, hand)
            self._draw_fingertips(frame, state)
            self._draw_finger_ratios(frame, state)
            self._draw_fps(frame, state, w, h)

        return frame

    # ═══════════════════════════════════════════════════════════
    # Chord Panel (center-bottom) — the main display
    # ═══════════════════════════════════════════════════════════

    def _draw_chord_panel(self, frame, s, w, h):
        pw, ph = 300, 88
        px = (w - pw) // 2
        py = h - ph - 10

        # Background
        cv2.rectangle(frame, (px, py), (px+pw, py+ph), BG, -1)

        # Border with pulse animation
        pulse = self._pulse_intensity
        if pulse > 0 and s.chord_state == "ACTIVE":
            # Interpolate border color: normal → bright pulse
            bp = int(pulse * 255)
            border = (bp, 255, bp)  # bright green-white flash
        elif s.chord_state == "ACTIVE":
            border = GREEN
        elif s.chord_state in ("CONFIRMING", "CHANGING"):
            border = YELLOW
        else:
            border = BORDER

        thickness = 2 if pulse > 0.3 else 1
        cv2.rectangle(frame, (px, py), (px+pw, py+ph), border, thickness)

        # Key display (top-left of panel)
        cv2.putText(frame, s.key_display, (px+10, py+15),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.38, GRAY, 1)

        if s.chord_name:
            # Roman numeral — large, left side
            r_color = GREEN if s.chord_state == "ACTIVE" else YELLOW
            # Pulse brightens the roman numeral
            if pulse > 0.2:
                r_color = (
                    min(255, r_color[0] + int(pulse * 100)),
                    min(255, r_color[1] + int(pulse * 50)),
                    min(255, r_color[2] + int(pulse * 50)),
                )
            cv2.putText(frame, s.roman, (px+10, py+52),
                        cv2.FONT_HERSHEY_SIMPLEX, 1.0, r_color, 2, cv2.LINE_AA)

            # Chord name — right of roman
            cv2.putText(frame, s.chord_name, (px+105, py+38),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, WHITE, 1, cv2.LINE_AA)

            # Note names
            cv2.putText(frame, s.notes, (px+105, py+56),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.38, GRAY, 1)
        else:
            prompt = "Show hand to play" if s.chord_state == "IDLE" else "..."
            cv2.putText(frame, prompt, (px+70, py+48),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.45, DIM, 1)

        # Confirmation progress bar
        bx, by, bw, bh = px+8, py+ph-10, pw-16, 4
        cv2.rectangle(frame, (bx, by), (bx+bw, by+bh), (35, 35, 35), -1)
        if s.confirm_progress > 0:
            fill = int(bw * min(1.0, s.confirm_progress))
            fc = GREEN if s.confirm_progress >= 1.0 else YELLOW
            cv2.rectangle(frame, (bx, by), (bx+fill, by+bh), fc, -1)

    # ═══════════════════════════════════════════════════════════
    # Hand Badges (top-right)
    # ═══════════════════════════════════════════════════════════

    def _draw_hand_badges(self, frame, s, w):
        x0 = w - 120
        self._draw_badge(frame, "R", s.right_gesture, s.right_in_zone, x0, 8)
        self._draw_badge(frame, "L", s.left_gesture, s.left_in_zone, x0, 40)

    def _draw_badge(self, frame, label, gesture, in_zone, x, y):
        bw, bh = 112, 28
        cv2.rectangle(frame, (x, y), (x+bw, y+bh), BG, -1)

        if gesture is not None and in_zone:
            color = GREEN if gesture.is_stable else YELLOW
            text = f"{label}: {gesture.finger_count}"
            cv2.putText(frame, text, (x+6, y+21),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.55, color, 1)
            if gesture.raw_finger_count != gesture.finger_count:
                cv2.putText(frame, f"({gesture.raw_finger_count})",
                            (x+78, y+21), cv2.FONT_HERSHEY_SIMPLEX, 0.32,
                            YELLOW, 1)
        else:
            cv2.putText(frame, f"{label}: --", (x+6, y+21),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.55, DIM, 1)

        cv2.rectangle(frame, (x, y), (x+bw, y+bh), BORDER, 1)

    # ═══════════════════════════════════════════════════════════
    # Info Strip (modifier + inversion, top-right below badges)
    # ═══════════════════════════════════════════════════════════

    def _draw_info_strip(self, frame, s, w):
        x0, y0 = w - 120, 72
        bw, bh = 112, 20

        # Modifier
        cv2.rectangle(frame, (x0, y0), (x0+bw, y0+bh), BG, -1)
        mod_text = s.modifier_name if s.modifier_name else "triad"
        color = ACCENT if s.modifier_active else DIM
        cv2.putText(frame, f"MOD {mod_text}", (x0+4, y0+15),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.35, color, 1)
        cv2.rectangle(frame, (x0, y0), (x0+bw, y0+bh), BORDER, 1)

        # Inversion
        y1 = y0 + bh + 2
        cv2.rectangle(frame, (x0, y1), (x0+bw, y1+bh), BG, -1)
        inv_names = ["root", "1st inv", "2nd inv"]
        inv_color = TEAL if s.inversion > 0 else DIM
        cv2.putText(frame, f"INV {inv_names[s.inversion]}", (x0+4, y1+15),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.35, inv_color, 1)
        cv2.rectangle(frame, (x0, y1), (x0+bw, y1+bh), BORDER, 1)

        # MIDI warning
        if not s.midi_available:
            y2 = y1 + bh + 2
            cv2.rectangle(frame, (x0, y2), (x0+bw, y2+bh), BG, -1)
            cv2.putText(frame, "NO MIDI", (x0+22, y2+15),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.4, RED, 1)
            cv2.rectangle(frame, (x0, y2), (x0+bw, y2+bh), RED, 1)

    # ═══════════════════════════════════════════════════════════
    # CC Expression Bar (top-right below info strip)
    # ═══════════════════════════════════════════════════════════

    def _draw_cc_bar(self, frame, s, w):
        x0, y0 = w - 120, 118
        bw = 112

        # CC1 (Y-axis)
        bh1 = 30
        cv2.rectangle(frame, (x0, y0), (x0+bw, y0+bh1), BG, -1)
        hdr_color = TEAL if s.cc_enabled else DIM
        cv2.putText(frame, f"CC{s.cc_number}", (x0+4, y0+12),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.28, hdr_color, 1)
        cv2.putText(frame, str(s.cc_value), (x0+82, y0+12),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.28, WHITE if s.cc_enabled else DIM, 1)
        bx, by = x0+4, y0+16
        bar_w, bar_h = bw-8, 7
        cv2.rectangle(frame, (bx, by), (bx+bar_w, by+bar_h), (35, 35, 35), -1)
        if s.cc_enabled and s.cc_normalized > 0:
            fill = int(bar_w * s.cc_normalized)
            g = int(150 + 80 * s.cc_normalized)
            cv2.rectangle(frame, (bx, by), (bx+fill, by+bar_h), (170, g, 0), -1)
        cv2.rectangle(frame, (bx, by), (bx+bar_w, by+bar_h), BORDER, 1)
        cv2.rectangle(frame, (x0, y0), (x0+bw, y0+bh1), BORDER, 1)

        # CC2 (X-axis) — only drawn when enabled
        if s.cc2_enabled:
            y1 = y0 + bh1 + 2
            bh2 = 30
            cv2.rectangle(frame, (x0, y1), (x0+bw, y1+bh2), BG, -1)
            hdr2 = ACCENT
            cv2.putText(frame, f"CC{s.cc2_number} X", (x0+4, y1+12),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.28, hdr2, 1)
            cv2.putText(frame, str(s.cc2_value), (x0+82, y1+12),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.28, WHITE, 1)
            bx2, by2 = x0+4, y1+16
            cv2.rectangle(frame, (bx2, by2), (bx2+bar_w, by2+bar_h), (35, 35, 35), -1)
            if s.cc2_normalized > 0:
                fill2 = int(bar_w * s.cc2_normalized)
                cv2.rectangle(frame, (bx2, by2), (bx2+fill2, by2+bar_h), ACCENT, -1)
            cv2.rectangle(frame, (bx2, by2), (bx2+bar_w, by2+bar_h), BORDER, 1)
            cv2.rectangle(frame, (x0, y1), (x0+bw, y1+bh2), BORDER, 1)

    # ═══════════════════════════════════════════════════════════
    # Rhythm Bar (above chord panel — shows active rhythm mode)
    # ═══════════════════════════════════════════════════════════

    def _draw_rhythm_bar(self, frame, s, w, h):
        """Status bar showing rhythm mode, groove, arp, velocity."""
        panel_top = h - 98 - 10
        y = panel_top - 22
        items = []

        if s.velocity_enabled:
            items.append((f"VEL:{s.velocity_value}", TEAL))
        if s.groove_enabled:
            items.append((f"GRV:{s.groove_pattern} {s.groove_bpm:.0f}", ACCENT))
        elif s.arp_enabled:
            items.append((f"ARP:{s.arp_pattern} {s.arp_bpm:.0f}", CYAN))
        elif s.rhythm_enabled:
            items.append(("PUMP" if s.rhythm_pumping else "pump",
                          GREEN if s.rhythm_pumping else DIM))

        if not items:
            return

        # Build single string — avoids per-item getTextSize
        text = "   ".join(t for t, _ in items)
        # Estimate width: ~7px per char at 0.35 scale
        est_w = len(text) * 7 + 16
        x = (w - est_w) // 2
        bar_h = 18

        cv2.rectangle(frame, (x-4, y), (x + est_w + 4, y + bar_h), BG, -1)
        cv2.rectangle(frame, (x-4, y), (x + est_w + 4, y + bar_h), BORDER, 1)

        # Draw each item with its color
        cx = x + 4
        for txt, color in items:
            cv2.putText(frame, txt, (cx, y + 13),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.35, color, 1)
            cx += len(txt) * 7 + 12

        # Beat dot for groove
        if s.groove_enabled:
            pulse = self._pulse_intensity
            dot_x = x + est_w - 2
            dot_y = y + bar_h // 2
            if pulse > 0.1:
                cv2.circle(frame, (dot_x, dot_y), int(4 + pulse * 3), BEAT_DOT, -1)
            else:
                cv2.circle(frame, (dot_x, dot_y), 3, DIM, -1)

    # ═══════════════════════════════════════════════════════════
    # Zone Line
    # ═══════════════════════════════════════════════════════════

    def _draw_zone_line(self, frame, s, w, h):
        zy = int(h * s.zone_threshold)
        active = s.right_in_zone or s.left_in_zone
        color = (0, 100, 0) if active else (35, 35, 35)
        cv2.line(frame, (0, zy), (w, zy), color, 1)
        if active:
            cv2.putText(frame, "zone", (w-42, zy - 4),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.26, color, 1)

    # ═══════════════════════════════════════════════════════════
    # Hand Skeleton
    # ═══════════════════════════════════════════════════════════

    def _draw_skeleton(self, frame, hand: HandData):
        thick = 2 if self.show_debug_info else 1
        color = SKELETON_DBG if self.show_debug_info else SKELETON_PERF

        for s_idx, e_idx in HAND_CONNECTIONS:
            s = hand.landmarks[s_idx]
            e = hand.landmarks[e_idx]
            cv2.line(frame, (s.px, s.py), (e.px, e.py), color, thick)

        if self.show_debug_info:
            for lm in hand.landmarks:
                cv2.circle(frame, (lm.px, lm.py), 3, JOINT_COLOR, -1)

    # ═══════════════════════════════════════════════════════════
    # Debug: Fingertips
    # ═══════════════════════════════════════════════════════════

    def _draw_fingertips(self, frame, s):
        tips = [
            LandmarkIndex.THUMB_TIP, LandmarkIndex.INDEX_TIP,
            LandmarkIndex.MIDDLE_TIP, LandmarkIndex.RING_TIP,
            LandmarkIndex.PINKY_TIP,
        ]
        for hand, gesture in [(s.tracking.get_right_hand(), s.right_gesture),
                              (s.tracking.get_left_hand(), s.left_gesture)]:
            if hand is None or gesture is None:
                continue
            for i, tip_idx in enumerate(tips):
                lm = hand.landmarks[tip_idx]
                color = TIP_UP if gesture.finger_states[i] else TIP_DN
                cv2.circle(frame, (lm.px, lm.py), 6, color, -1)
                cv2.circle(frame, (lm.px, lm.py), 6, WHITE, 1)

    # ═══════════════════════════════════════════════════════════
    # Debug: Finger Ratios
    # ═══════════════════════════════════════════════════════════

    def _draw_finger_ratios(self, frame, s):
        gesture = s.right_gesture
        if gesture is None:
            return

        names = ["THM", "IDX", "MID", "RNG", "PNK"]
        x0, y0 = 8, 8
        pw, ph = 220, 80

        cv2.rectangle(frame, (x0, y0), (x0+pw, y0+ph), BG, -1)

        for i, (name, ratio, up) in enumerate(
            zip(names, gesture.extension_ratios, gesture.finger_states)
        ):
            y = y0 + 3 + i * 15
            color = GREEN if up else RED
            tag = "UP" if up else "DN"
            cv2.putText(frame, f"{name} {tag} {ratio:.2f}", (x0+4, y+11),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.32, color, 1)
            bx = x0 + 105
            bw = int(110 * min(1.0, ratio))
            cv2.rectangle(frame, (bx, y), (bx+bw, y+10), color, -1)
            cv2.rectangle(frame, (bx, y), (bx+110, y+10), BORDER, 1)

        cv2.rectangle(frame, (x0, y0), (x0+pw, y0+ph), BORDER, 1)

    # ═══════════════════════════════════════════════════════════
    # Debug: FPS + Timing
    # ═══════════════════════════════════════════════════════════

    def _draw_fps(self, frame, s, w, h):
        text = f"FPS:{s.fps:.0f}  INF:{s.inference_ms:.0f}ms"
        cv2.putText(frame, text, (w-180, h-8),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.35, DIM, 1)