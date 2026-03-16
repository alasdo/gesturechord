"""
Visual overlay v2 — clean performance mode + debug mode.

Performance mode (default): shows only what a performer needs to glance at.
Debug mode (press D): adds hand skeletons, finger ratios, FPS, raw counts.

Layout (performance mode):
    ┌─────────────────────────────────────────────┐
    │                                   [R:3][L:1]│
    │                                   [MOD:7th ]│
    │                                   [INV:root]│
    │                                   [CC1  85 ]│
    │              (hands here)         [████░░░░]│
    │                                             │
    │────────────── zone line ────────────────────│
    │        ┌─────────────────────┐              │
    │        │Key: C major         │              │
    │        │ I    C major        │              │
    │        │      C E G          │              │
    │        │[████████████████░░░]│              │
    │        └─────────────────────┘              │
    └─────────────────────────────────────────────┘
"""

import cv2
import numpy as np
from typing import Optional, List
from dataclasses import dataclass

from vision.hand_tracker import HandData, LandmarkIndex, TrackingResult
from vision.gesture_recognizer import GestureResult


# ── Colors (BGR) ──
C_BG = (20, 20, 20)
C_BG_ALPHA = (30, 30, 30)
C_BORDER = (70, 70, 70)
C_WHITE = (255, 255, 255)
C_GRAY = (140, 140, 140)
C_DARK_GRAY = (80, 80, 80)
C_GREEN = (0, 210, 0)
C_YELLOW = (0, 210, 255)
C_RED = (0, 0, 210)
C_CYAN = (255, 220, 0)
C_TEAL = (200, 200, 0)
C_ACCENT = (255, 160, 0)     # Bright cyan-blue for active elements
C_SKELETON = (200, 180, 0)   # Muted cyan for hand bones
C_JOINT = (0, 180, 220)      # Orange for joint dots
C_TIP_UP = (0, 230, 0)       # Green fingertip
C_TIP_DN = (0, 0, 180)       # Red fingertip

# Hand skeleton connections
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
    chord_state: str = "IDLE"       # IDLE, DETECTING, CONFIRMING, ACTIVE, CHANGING
    confirm_progress: float = 0.0
    key_display: str = ""
    modifier_name: str = ""
    modifier_active: bool = False
    inversion: int = 0              # 0=root, 1=1st, 2=2nd
    cc_number: int = 1
    cc_value: int = 0
    cc_normalized: float = 0.0
    cc_enabled: bool = True
    fps: float = 0.0
    inference_ms: float = 0.0
    midi_available: bool = True
    zone_threshold: float = 0.75


class Overlay:
    """
    Draws all visual feedback. Two modes:
        Performance mode (show_debug=False): minimal, glanceable
        Debug mode (show_debug=True): full diagnostics
    """

    def __init__(self, show_debug_info: bool = False):
        self.show_debug_info = show_debug_info

    def draw(self, frame: np.ndarray, state: OverlayState) -> np.ndarray:
        """Draw all overlay elements onto the frame."""
        h, w = frame.shape[:2]

        # ── Always drawn (both modes) ──
        self._draw_chord_panel(frame, state, w, h)
        self._draw_right_badge(frame, state, w)
        self._draw_left_badge(frame, state, w)
        self._draw_status_strip(frame, state, w)
        self._draw_cc_bar(frame, state, w)
        self._draw_zone_line(frame, state, w, h)

        # ── Hand skeletons (performance: thin/subtle, debug: thick/bright) ──
        if state.tracking.has_hands:
            for hand in state.tracking.hands:
                self._draw_skeleton(frame, hand)

        # ── Debug-only elements ──
        if self.show_debug_info:
            self._draw_fingertips(frame, state)
            self._draw_finger_ratios(frame, state)
            self._draw_fps(frame, state, w, h)

        # ── Mode indicator ──
        mode_text = "DEBUG (D)" if self.show_debug_info else "PERF (D)"
        mode_color = C_YELLOW if self.show_debug_info else C_DARK_GRAY
        cv2.putText(frame, mode_text, (10, h - 8),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.33, mode_color, 1, cv2.LINE_AA)

        return frame

    # ── Chord Panel (center-bottom) ──

    def _draw_chord_panel(self, frame, s: OverlayState, w, h):
        pw, ph = 340, 90
        px = (w - pw) // 2
        py = h - ph - 8

        # Background with slight transparency effect
        overlay_region = frame[py:py+ph, px:px+pw].copy()
        cv2.rectangle(frame, (px, py), (px+pw, py+ph), C_BG, -1)
        # Blend for semi-transparency
        cv2.addWeighted(overlay_region, 0.2, frame[py:py+ph, px:px+pw], 0.8, 0,
                        frame[py:py+ph, px:px+pw])

        # Border color based on state
        if s.chord_state == "ACTIVE":
            border = C_GREEN
        elif s.chord_state in ("CONFIRMING", "CHANGING"):
            border = C_YELLOW
        else:
            border = C_BORDER
        cv2.rectangle(frame, (px, py), (px+pw, py+ph), border, 2)

        # Key (top line)
        cv2.putText(frame, s.key_display, (px+10, py+16),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.4, C_GRAY, 1, cv2.LINE_AA)

        if s.chord_name:
            # Roman numeral (large)
            r_color = C_GREEN if s.chord_state == "ACTIVE" else C_YELLOW
            cv2.putText(frame, s.roman, (px+10, py+55),
                        cv2.FONT_HERSHEY_SIMPLEX, 1.1, r_color, 2, cv2.LINE_AA)

            # Chord name
            cv2.putText(frame, s.chord_name, (px+110, py+42),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.55, C_WHITE, 1, cv2.LINE_AA)

            # Note names
            cv2.putText(frame, s.notes, (px+110, py+62),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.42, C_GRAY, 1, cv2.LINE_AA)
        else:
            prompt = "Show your hand" if s.chord_state == "IDLE" else "..."
            cv2.putText(frame, prompt, (px+100, py+50),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.55, C_DARK_GRAY, 1, cv2.LINE_AA)

        # Progress bar
        bx, by, bw, bh = px+8, py+ph-10, pw-16, 5
        cv2.rectangle(frame, (bx, by), (bx+bw, by+bh), (40, 40, 40), -1)
        if s.confirm_progress > 0:
            fill = int(bw * s.confirm_progress)
            fc = C_GREEN if s.confirm_progress >= 1.0 else C_YELLOW
            cv2.rectangle(frame, (bx, by), (bx+fill, by+bh), fc, -1)

    # ── Right Hand Badge ──

    def _draw_right_badge(self, frame, s: OverlayState, w):
        self._draw_badge(frame, "R", s.right_gesture, s.right_in_zone, w-135, 8)

    # ── Left Hand Badge ──

    def _draw_left_badge(self, frame, s: OverlayState, w):
        self._draw_badge(frame, "L", s.left_gesture, s.left_in_zone, w-135, 48)

    def _draw_badge(self, frame, label, gesture, in_zone, x, y):
        bw, bh = 127, 35
        cv2.rectangle(frame, (x, y), (x+bw, y+bh), C_BG, -1)

        if gesture is not None and in_zone:
            color = C_GREEN if gesture.is_stable else C_YELLOW
            cv2.putText(frame, f"{label}: {gesture.finger_count}",
                        (x+6, y+26), cv2.FONT_HERSHEY_SIMPLEX, 0.7,
                        color, 2, cv2.LINE_AA)
            # Raw count indicator if different
            if gesture.raw_finger_count != gesture.finger_count:
                cv2.putText(frame, f"({gesture.raw_finger_count})",
                            (x+88, y+26), cv2.FONT_HERSHEY_SIMPLEX, 0.38,
                            C_YELLOW, 1, cv2.LINE_AA)
        else:
            cv2.putText(frame, f"{label}: --",
                        (x+6, y+26), cv2.FONT_HERSHEY_SIMPLEX, 0.7,
                        C_DARK_GRAY, 2, cv2.LINE_AA)

        cv2.rectangle(frame, (x, y), (x+bw, y+bh), C_BORDER, 1)

    # ── Status Strip (modifier + inversion) ──

    def _draw_status_strip(self, frame, s: OverlayState, w):
        x, y = w - 135, 88
        bw, bh = 127, 22

        # Modifier
        cv2.rectangle(frame, (x, y), (x+bw, y+bh), C_BG, -1)
        mod_text = s.modifier_name if s.modifier_name else "triad"
        mod_color = C_ACCENT if s.modifier_active else C_DARK_GRAY
        cv2.putText(frame, f"MOD {mod_text}", (x+4, y+16),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.4, mod_color, 1, cv2.LINE_AA)
        cv2.rectangle(frame, (x, y), (x+bw, y+bh), C_BORDER, 1)

        # Inversion
        y2 = y + bh + 2
        cv2.rectangle(frame, (x, y2), (x+bw, y2+bh), C_BG, -1)
        inv_names = ["root", "1st inv", "2nd inv"]
        inv_color = C_TEAL if s.inversion > 0 else C_DARK_GRAY
        cv2.putText(frame, f"INV {inv_names[s.inversion]}", (x+4, y2+16),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.4, inv_color, 1, cv2.LINE_AA)
        cv2.rectangle(frame, (x, y2), (x+bw, y2+bh), C_BORDER, 1)

        # MIDI status
        if not s.midi_available:
            y3 = y2 + bh + 2
            cv2.rectangle(frame, (x, y3), (x+bw, y3+bh), C_BG, -1)
            cv2.putText(frame, "NO MIDI", (x+20, y3+16),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.45, C_RED, 1, cv2.LINE_AA)
            cv2.rectangle(frame, (x, y3), (x+bw, y3+bh), C_RED, 1)

    # ── CC Expression Bar ──

    def _draw_cc_bar(self, frame, s: OverlayState, w):
        x, y = w - 135, 140
        bw, bh = 127, 52
        cv2.rectangle(frame, (x, y), (x+bw, y+bh), C_BG, -1)

        # Header
        en_str = "ON" if s.cc_enabled else "OFF"
        hdr_color = C_TEAL if s.cc_enabled else C_DARK_GRAY
        cv2.putText(frame, f"CC{s.cc_number} {en_str}", (x+4, y+14),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.35, hdr_color, 1, cv2.LINE_AA)
        cv2.putText(frame, str(s.cc_value), (x+90, y+14),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.35, C_WHITE, 1, cv2.LINE_AA)

        # Fill bar
        bar_x, bar_y = x+4, y+20
        bar_w, bar_h = bw-8, 12
        cv2.rectangle(frame, (bar_x, bar_y), (bar_x+bar_w, bar_y+bar_h), (40, 40, 40), -1)
        if s.cc_enabled and s.cc_normalized > 0:
            fill = int(bar_w * s.cc_normalized)
            # Color gradient: dark teal -> bright teal
            g = int(160 + 60 * s.cc_normalized)
            bar_color = (180, g, 0)
            cv2.rectangle(frame, (bar_x, bar_y), (bar_x+fill, bar_y+bar_h), bar_color, -1)
        cv2.rectangle(frame, (bar_x, bar_y), (bar_x+bar_w, bar_y+bar_h), C_BORDER, 1)

        # Hint
        cv2.putText(frame, "hand height=effect", (x+4, y+46),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.28, C_DARK_GRAY, 1, cv2.LINE_AA)

        cv2.rectangle(frame, (x, y), (x+bw, y+bh), C_BORDER, 1)

    # ── Zone Line ──

    def _draw_zone_line(self, frame, s: OverlayState, w, h):
        zy = int(h * s.zone_threshold)
        active = s.right_in_zone or s.left_in_zone
        color = (0, 140, 0) if active else (50, 50, 50)
        cv2.line(frame, (0, zy), (w, zy), color, 1, cv2.LINE_AA)
        cv2.putText(frame, "zone", (w-50, zy-4),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.3, color, 1, cv2.LINE_AA)

    # ── Hand Skeleton ──

    def _draw_skeleton(self, frame, hand: HandData):
        alpha = 1.0 if self.show_debug_info else 0.5
        thickness = 2 if self.show_debug_info else 1
        color = C_SKELETON if self.show_debug_info else (130, 120, 0)

        for s_idx, e_idx in HAND_CONNECTIONS:
            s = hand.landmarks[s_idx]
            e = hand.landmarks[e_idx]
            cv2.line(frame, (s.px, s.py), (e.px, e.py), color, thickness, cv2.LINE_AA)

        if self.show_debug_info:
            for lm in hand.landmarks:
                cv2.circle(frame, (lm.px, lm.py), 3, C_JOINT, -1, cv2.LINE_AA)

    # ── Debug: Fingertips ──

    def _draw_fingertips(self, frame, s: OverlayState):
        """Color-coded fingertip dots for both hands."""
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
                color = C_TIP_UP if gesture.finger_states[i] else C_TIP_DN
                cv2.circle(frame, (lm.px, lm.py), 7, color, -1, cv2.LINE_AA)
                cv2.circle(frame, (lm.px, lm.py), 7, C_WHITE, 1, cv2.LINE_AA)

    # ── Debug: Finger Ratios ──

    def _draw_finger_ratios(self, frame, s: OverlayState):
        """Per-finger extension ratios for the right hand."""
        gesture = s.right_gesture
        if gesture is None:
            return

        names = ["THM", "IDX", "MID", "RNG", "PNK"]
        x0, y0 = 8, 10

        cv2.rectangle(frame, (x0, y0), (x0+240, y0+80), C_BG, -1)

        for i, (name, ratio, up) in enumerate(
            zip(names, gesture.extension_ratios, gesture.finger_states)
        ):
            y = y0 + 4 + i * 15
            color = C_GREEN if up else C_RED
            tag = "UP" if up else "DN"
            cv2.putText(frame, f"{name} {tag} {ratio:.2f}", (x0+4, y+11),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.35, color, 1, cv2.LINE_AA)
            # Mini bar
            bx = x0 + 115
            bw = int(120 * ratio)
            cv2.rectangle(frame, (bx, y), (bx+bw, y+10), color, -1)
            cv2.rectangle(frame, (bx, y), (bx+120, y+10), C_BORDER, 1)

        cv2.rectangle(frame, (x0, y0), (x0+240, y0+80), C_BORDER, 1)

    # ── Debug: FPS ──

    def _draw_fps(self, frame, s: OverlayState, w, h):
        text = f"FPS:{s.fps:.0f}  INF:{s.inference_ms:.0f}ms"
        cv2.putText(frame, text, (w-200, h-8),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.38, C_DARK_GRAY, 1, cv2.LINE_AA)