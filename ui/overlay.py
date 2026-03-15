"""
Visual feedback overlay on the camera frame.

Design philosophy:
    The overlay must communicate three things at a glance:
    1. Is the system tracking my hand? (landmark visualization)
    2. What gesture does it think I'm making? (finger count display)
    3. How confident/stable is the detection? (confidence bar, stability indicator)

    Keep it visually clean — this runs in a window alongside FL Studio,
    so it should be compact and not distracting. Dark theme with bright
    accent colors for visibility.

Color scheme:
    - Hand landmarks: cyan connections, bright dots at joints
    - Finger count: large white text, top-left
    - Confidence: color-coded bar (red < yellow < green)
    - Status messages: color-coded (green = active, yellow = detecting, gray = idle)
    - FPS: small text, bottom-right (for performance monitoring)
"""

import logging
from typing import List, Optional, Tuple

import cv2
import numpy as np

from vision.hand_tracker import HandData, LandmarkIndex, TrackingResult
from vision.gesture_recognizer import GestureResult


logger = logging.getLogger("gesturechord.ui.overlay")


# MediaPipe hand connections for drawing skeleton
HAND_CONNECTIONS = [
    # Thumb
    (LandmarkIndex.WRIST, LandmarkIndex.THUMB_CMC),
    (LandmarkIndex.THUMB_CMC, LandmarkIndex.THUMB_MCP),
    (LandmarkIndex.THUMB_MCP, LandmarkIndex.THUMB_IP),
    (LandmarkIndex.THUMB_IP, LandmarkIndex.THUMB_TIP),
    # Index
    (LandmarkIndex.WRIST, LandmarkIndex.INDEX_MCP),
    (LandmarkIndex.INDEX_MCP, LandmarkIndex.INDEX_PIP),
    (LandmarkIndex.INDEX_PIP, LandmarkIndex.INDEX_DIP),
    (LandmarkIndex.INDEX_DIP, LandmarkIndex.INDEX_TIP),
    # Middle
    (LandmarkIndex.WRIST, LandmarkIndex.MIDDLE_MCP),
    (LandmarkIndex.MIDDLE_MCP, LandmarkIndex.MIDDLE_PIP),
    (LandmarkIndex.MIDDLE_PIP, LandmarkIndex.MIDDLE_DIP),
    (LandmarkIndex.MIDDLE_DIP, LandmarkIndex.MIDDLE_TIP),
    # Ring
    (LandmarkIndex.WRIST, LandmarkIndex.RING_MCP),
    (LandmarkIndex.RING_MCP, LandmarkIndex.RING_PIP),
    (LandmarkIndex.RING_PIP, LandmarkIndex.RING_DIP),
    (LandmarkIndex.RING_DIP, LandmarkIndex.RING_TIP),
    # Pinky
    (LandmarkIndex.WRIST, LandmarkIndex.PINKY_MCP),
    (LandmarkIndex.PINKY_MCP, LandmarkIndex.PINKY_PIP),
    (LandmarkIndex.PINKY_PIP, LandmarkIndex.PINKY_DIP),
    (LandmarkIndex.PINKY_DIP, LandmarkIndex.PINKY_TIP),
    # Palm
    (LandmarkIndex.INDEX_MCP, LandmarkIndex.MIDDLE_MCP),
    (LandmarkIndex.MIDDLE_MCP, LandmarkIndex.RING_MCP),
    (LandmarkIndex.RING_MCP, LandmarkIndex.PINKY_MCP),
    (LandmarkIndex.PINKY_MCP, LandmarkIndex.WRIST),
]

# Colors (BGR format for OpenCV)
COLOR_CYAN = (255, 255, 0)
COLOR_GREEN = (0, 220, 0)
COLOR_YELLOW = (0, 220, 255)
COLOR_RED = (0, 0, 220)
COLOR_WHITE = (255, 255, 255)
COLOR_GRAY = (150, 150, 150)
COLOR_DARK_BG = (30, 30, 30)
COLOR_LANDMARK_DOT = (0, 200, 255)  # Orange-yellow for joint dots
COLOR_FINGERTIP = (0, 255, 0)       # Green for extended fingertips
COLOR_FINGERTIP_CURLED = (0, 0, 200) # Red for curled fingertips


class Overlay:
    """
    Draws visual feedback onto camera frames.

    Usage:
        overlay = Overlay()
        frame = overlay.draw(frame, tracking_result, gesture_result, fps=30.0)
        cv2.imshow("GestureChord", frame)
    """

    WINDOW_NAME = "GestureChord"

    def __init__(self, show_debug_info: bool = True):
        """
        Args:
            show_debug_info: If True, show extension ratios and raw counts.
                Useful during development, can be toggled off for clean UI.
        """
        self.show_debug_info = show_debug_info

    def draw(
        self,
        frame: np.ndarray,
        tracking: TrackingResult,
        gesture: Optional[GestureResult] = None,
        fps: float = 0.0,
        status_text: str = "",
        chord_display: Optional[dict] = None,
    ) -> np.ndarray:
        """
        Draw all overlay elements onto the frame.

        Args:
            frame: BGR frame to draw on (will be modified in place).
            tracking: Hand tracking results for this frame.
            gesture: Gesture recognition results (None if no hand detected).
            fps: Current frames per second for display.
            status_text: Additional status message (e.g., current key, mode).
            chord_display: Optional dict with chord info for display:
                {
                    "chord_name": "C major",
                    "roman": "I",
                    "notes": "C E G",
                    "state": "ACTIVE" | "CONFIRMING" | "CHANGING" | ...,
                    "progress": 0.0-1.0,
                    "key": "C major",
                }

        Returns:
            The same frame with overlay drawn.
        """
        if tracking.has_hands:
            for hand in tracking.hands:
                self._draw_hand_skeleton(frame, hand)
                self._draw_fingertips(frame, hand, gesture)

        # Draw info panels
        self._draw_finger_count(frame, gesture)
        self._draw_confidence_bar(frame, tracking, gesture)
        self._draw_fps(frame, fps, tracking.inference_time_ms)
        self._draw_status(frame, tracking, gesture, status_text)

        if chord_display is not None:
            self._draw_chord_panel(frame, chord_display)

        if self.show_debug_info and gesture is not None:
            self._draw_debug_info(frame, gesture)

        return frame

    def _draw_hand_skeleton(self, frame: np.ndarray, hand: HandData) -> None:
        """Draw hand landmark connections (skeleton)."""
        for start_idx, end_idx in HAND_CONNECTIONS:
            start = hand.landmarks[start_idx]
            end = hand.landmarks[end_idx]
            cv2.line(
                frame,
                (start.px, start.py),
                (end.px, end.py),
                COLOR_CYAN,
                2,
                cv2.LINE_AA,
            )

        # Draw joint dots
        for lm in hand.landmarks:
            cv2.circle(frame, (lm.px, lm.py), 4, COLOR_LANDMARK_DOT, -1, cv2.LINE_AA)

    def _draw_fingertips(
        self,
        frame: np.ndarray,
        hand: HandData,
        gesture: Optional[GestureResult],
    ) -> None:
        """Draw colored circles on fingertips: green if extended, red if curled."""
        if gesture is None:
            return

        tip_indices = [
            LandmarkIndex.THUMB_TIP,
            LandmarkIndex.INDEX_TIP,
            LandmarkIndex.MIDDLE_TIP,
            LandmarkIndex.RING_TIP,
            LandmarkIndex.PINKY_TIP,
        ]

        for i, tip_idx in enumerate(tip_indices):
            lm = hand.landmarks[tip_idx]
            color = COLOR_FINGERTIP if gesture.finger_states[i] else COLOR_FINGERTIP_CURLED
            cv2.circle(frame, (lm.px, lm.py), 8, color, -1, cv2.LINE_AA)
            cv2.circle(frame, (lm.px, lm.py), 8, COLOR_WHITE, 1, cv2.LINE_AA)

    def _draw_finger_count(
        self, frame: np.ndarray, gesture: Optional[GestureResult]
    ) -> None:
        """Draw large finger count in the top-left corner."""
        h, w = frame.shape[:2]

        # Background panel
        panel_w, panel_h = 120, 80
        cv2.rectangle(frame, (10, 10), (10 + panel_w, 10 + panel_h), COLOR_DARK_BG, -1)
        cv2.rectangle(frame, (10, 10), (10 + panel_w, 10 + panel_h), COLOR_GRAY, 1)

        if gesture is not None:
            # Large finger count
            count_text = str(gesture.finger_count)
            color = COLOR_GREEN if gesture.is_stable else COLOR_YELLOW
            cv2.putText(
                frame, count_text, (30, 75),
                cv2.FONT_HERSHEY_SIMPLEX, 2.0, color, 3, cv2.LINE_AA,
            )

            # Small label
            cv2.putText(
                frame, "FINGERS", (40, 25),
                cv2.FONT_HERSHEY_SIMPLEX, 0.4, COLOR_GRAY, 1, cv2.LINE_AA,
            )

            # Raw count if different (shows filtering is active)
            if gesture.raw_finger_count != gesture.finger_count:
                raw_text = f"raw:{gesture.raw_finger_count}"
                cv2.putText(
                    frame, raw_text, (85, 75),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.4, COLOR_YELLOW, 1, cv2.LINE_AA,
                )
        else:
            cv2.putText(
                frame, "-", (45, 75),
                cv2.FONT_HERSHEY_SIMPLEX, 2.0, COLOR_GRAY, 3, cv2.LINE_AA,
            )
            cv2.putText(
                frame, "NO HAND", (30, 25),
                cv2.FONT_HERSHEY_SIMPLEX, 0.4, COLOR_GRAY, 1, cv2.LINE_AA,
            )

    def _draw_confidence_bar(
        self,
        frame: np.ndarray,
        tracking: TrackingResult,
        gesture: Optional[GestureResult],
    ) -> None:
        """Draw a confidence/stability bar below the finger count."""
        bar_x, bar_y = 10, 100
        bar_w, bar_h = 120, 12

        # Background
        cv2.rectangle(
            frame, (bar_x, bar_y), (bar_x + bar_w, bar_y + bar_h),
            COLOR_DARK_BG, -1,
        )

        if gesture is not None:
            # Fill based on rolling mode confidence
            confidence = self._count_filter_confidence(gesture)
            fill_w = int(bar_w * confidence)

            # Color: red → yellow → green
            if confidence < 0.4:
                color = COLOR_RED
            elif confidence < 0.7:
                color = COLOR_YELLOW
            else:
                color = COLOR_GREEN

            cv2.rectangle(
                frame,
                (bar_x, bar_y),
                (bar_x + fill_w, bar_y + bar_h),
                color, -1,
            )

        # Border
        cv2.rectangle(
            frame, (bar_x, bar_y), (bar_x + bar_w, bar_y + bar_h),
            COLOR_GRAY, 1,
        )

        # Label
        cv2.putText(
            frame, "STABILITY", (bar_x + 25, bar_y + 24),
            cv2.FONT_HERSHEY_SIMPLEX, 0.35, COLOR_GRAY, 1, cv2.LINE_AA,
        )

    def _count_filter_confidence(self, gesture: GestureResult) -> float:
        """Estimate confidence from gesture stability. Simple heuristic for now."""
        if gesture.is_stable:
            return 1.0
        if gesture.finger_count == gesture.raw_finger_count:
            return 0.8
        return 0.4

    def _draw_fps(
        self, frame: np.ndarray, fps: float, inference_ms: float
    ) -> None:
        """Draw FPS and inference time in bottom-right corner."""
        h, w = frame.shape[:2]
        text = f"FPS: {fps:.0f}  |  Inference: {inference_ms:.0f}ms"
        cv2.putText(
            frame, text, (w - 280, h - 15),
            cv2.FONT_HERSHEY_SIMPLEX, 0.45, COLOR_GRAY, 1, cv2.LINE_AA,
        )

    def _draw_status(
        self,
        frame: np.ndarray,
        tracking: TrackingResult,
        gesture: Optional[GestureResult],
        status_text: str,
    ) -> None:
        """Draw status text in the top-right area."""
        h, w = frame.shape[:2]

        # Detection status
        if not tracking.has_hands:
            status = "IDLE - Show your hand"
            color = COLOR_GRAY
        elif gesture is not None and gesture.is_stable:
            status = "TRACKING (stable)"
            color = COLOR_GREEN
        else:
            status = "TRACKING..."
            color = COLOR_YELLOW

        cv2.putText(
            frame, status, (w - 250, 30),
            cv2.FONT_HERSHEY_SIMPLEX, 0.55, color, 1, cv2.LINE_AA,
        )

        # Handedness
        if tracking.has_hands:
            primary = tracking.get_primary_hand()
            if primary:
                hand_text = f"{primary.handedness} hand ({primary.confidence:.0%})"
                cv2.putText(
                    frame, hand_text, (w - 250, 55),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.45, COLOR_GRAY, 1, cv2.LINE_AA,
                )

        # Custom status text (will show key/mode info in later phases)
        if status_text:
            cv2.putText(
                frame, status_text, (w - 250, 80),
                cv2.FONT_HERSHEY_SIMPLEX, 0.5, COLOR_WHITE, 1, cv2.LINE_AA,
            )

    def _draw_debug_info(
        self, frame: np.ndarray, gesture: GestureResult
    ) -> None:
        """Draw per-finger extension ratios and states for debugging."""
        h, w = frame.shape[:2]
        finger_names = ["THM", "IDX", "MID", "RNG", "PNK"]

        # Position below the stability bar (top-left area)
        y_start = 135
        x_start = 10

        # Background panel
        cv2.rectangle(
            frame,
            (x_start, y_start - 5),
            (x_start + 280, y_start + 75),
            COLOR_DARK_BG, -1,
        )

        for i, (name, ratio, state) in enumerate(
            zip(finger_names, gesture.extension_ratios, gesture.finger_states)
        ):
            y = y_start + i * 14
            color = COLOR_GREEN if state else COLOR_RED

            # Finger name and state (use ASCII-safe chars)
            state_char = "UP" if state else "DN"
            text = f"{name} {state_char} {ratio:.2f}"
            cv2.putText(
                frame, text, (x_start + 5, y + 10),
                cv2.FONT_HERSHEY_SIMPLEX, 0.38, color, 1, cv2.LINE_AA,
            )

            # Mini bar chart for extension ratio
            bar_x = x_start + 120
            bar_w = int(150 * ratio)
            cv2.rectangle(frame, (bar_x, y), (bar_x + bar_w, y + 10), color, -1)
            cv2.rectangle(frame, (bar_x, y), (bar_x + 150, y + 10), COLOR_GRAY, 1)

    def _draw_chord_panel(self, frame: np.ndarray, info: dict) -> None:
        """
        Draw chord information panel — the most important musical feedback.

        Shows: chord name, roman numeral, note names, confirmation progress,
        and current key. Positioned center-bottom for easy glancing.
        """
        h, w = frame.shape[:2]

        panel_w, panel_h = 340, 95
        panel_x = (w - panel_w) // 2
        panel_y = h - panel_h - 10

        state = info.get("state", "IDLE")
        progress = info.get("progress", 0.0)
        chord_name = info.get("chord_name", "")
        roman = info.get("roman", "")
        notes = info.get("notes", "")
        key_name = info.get("key", "")

        # Panel background
        cv2.rectangle(
            frame, (panel_x, panel_y),
            (panel_x + panel_w, panel_y + panel_h),
            COLOR_DARK_BG, -1,
        )

        # State-dependent border color
        if state == "ACTIVE":
            border_color = COLOR_GREEN
        elif state in ("CONFIRMING", "CHANGING"):
            border_color = COLOR_YELLOW
        else:
            border_color = COLOR_GRAY

        cv2.rectangle(
            frame, (panel_x, panel_y),
            (panel_x + panel_w, panel_y + panel_h),
            border_color, 2,
        )

        # Key display (top of panel)
        cv2.putText(
            frame, f"Key: {key_name}", (panel_x + 10, panel_y + 18),
            cv2.FONT_HERSHEY_SIMPLEX, 0.42, COLOR_GRAY, 1, cv2.LINE_AA,
        )

        if chord_name:
            # Roman numeral (large, left side)
            roman_color = COLOR_GREEN if state == "ACTIVE" else COLOR_YELLOW
            cv2.putText(
                frame, roman, (panel_x + 12, panel_y + 58),
                cv2.FONT_HERSHEY_SIMPLEX, 1.0, roman_color, 2, cv2.LINE_AA,
            )

            # Chord name (right of roman numeral — offset enough for "vii°")
            cv2.putText(
                frame, chord_name, (panel_x + 110, panel_y + 48),
                cv2.FONT_HERSHEY_SIMPLEX, 0.6, COLOR_WHITE, 1, cv2.LINE_AA,
            )

            # Note names
            cv2.putText(
                frame, notes, (panel_x + 110, panel_y + 68),
                cv2.FONT_HERSHEY_SIMPLEX, 0.45, COLOR_GRAY, 1, cv2.LINE_AA,
            )
        else:
            # No chord
            label = "Show fingers to play"
            if state == "IDLE":
                label = "Show your hand"
            cv2.putText(
                frame, label, (panel_x + 50, panel_y + 55),
                cv2.FONT_HERSHEY_SIMPLEX, 0.55, COLOR_GRAY, 1, cv2.LINE_AA,
            )

        # Confirmation progress bar (bottom of panel)
        bar_x = panel_x + 10
        bar_y = panel_y + panel_h - 12
        bar_w = panel_w - 20
        bar_h = 6

        cv2.rectangle(frame, (bar_x, bar_y), (bar_x + bar_w, bar_y + bar_h), (50, 50, 50), -1)
        if progress > 0:
            fill_w = int(bar_w * progress)
            fill_color = COLOR_GREEN if progress >= 1.0 else COLOR_YELLOW
            cv2.rectangle(frame, (bar_x, bar_y), (bar_x + fill_w, bar_y + bar_h), fill_color, -1)