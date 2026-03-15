"""
Gesture recognition v3 — Y-position comparison with robust normalization.

v1 problem: distance-based ratios gave 0.35-0.51 for a closed fist.
v2 problem: angle at PIP-DIP was wrong joint pair — DIP barely moves.

v3 solution: The SIMPLEST method that actually works reliably.

For INDEX, MIDDLE, RING, PINKY:
    Compare fingertip Y to PIP (middle knuckle) Y position.
    In image coordinates, Y increases downward:
    - Extended finger: tip Y is ABOVE (less than) PIP Y → extended
    - Curled finger: tip Y is BELOW (greater than) PIP Y → curled

    We compute: ratio = (pip.y - tip.y) / hand_height
    - Positive = tip above PIP = extended
    - Negative = tip below PIP = curled
    - Normalized by hand height for scale invariance

    Then map to 0.0-1.0:
    - ratio < -0.02 → 0.0 (curled, tip below PIP)
    - ratio > 0.08 → 1.0 (extended, tip well above PIP)

For THUMB:
    The thumb extends laterally, not vertically.
    Compare thumb tip X to thumb IP X:
    - Right hand: extended = tip is further LEFT (lower X in mirrored image)
    - Left hand: extended = tip is further RIGHT (higher X in mirrored image)

    We use absolute X distance normalized by hand width:
    - Small distance = tucked in (fist)
    - Large distance = extended

    Secondary: also check if tip is beyond the index MCP horizontally,
    which is a reliable "thumb is sticking out" indicator.

Why this works better:
    For a closed fist, ALL fingertips are below their PIP joints. The Y
    difference is strongly negative (tip is well below PIP). This gives
    ratios firmly at 0.0. For extended fingers, tips are well above PIP,
    giving ratios firmly at 1.0. The gap is enormous.
"""

import logging
import math
from dataclasses import dataclass
from typing import List, Optional

from vision.hand_tracker import HandData, LandmarkIndex, HandLandmark
from utils.filters import HysteresisFilter, RollingModeFilter


logger = logging.getLogger("gesturechord.vision.gesture_recognizer")


@dataclass
class GestureResult:
    """Output of gesture recognition for a single hand."""
    finger_states: List[bool]       # [thumb, index, middle, ring, pinky]
    finger_count: int               # Filtered count
    raw_finger_count: int           # Unfiltered count
    extension_ratios: List[float]   # 0.0-1.0 per finger
    handedness: str
    confidence: float
    is_stable: bool


def _distance_2d(a: HandLandmark, b: HandLandmark) -> float:
    return math.sqrt((a.x - b.x) ** 2 + (a.y - b.y) ** 2)


class GestureRecognizer:
    """
    Recognizes finger gestures using Y-position comparison.

    The most reliable method for webcam hand tracking:
    fingertip above PIP = extended, fingertip below PIP = curled.

    Args:
        hysteresis_high: Ratio above which finger is "up".
        hysteresis_low: Ratio below which finger is "down".
        rolling_window: Frames for rolling mode filter on count.
    """

    # Finger landmarks for tip-vs-PIP comparison
    FINGER_LANDMARKS = [
        # (name, tip_idx, pip_idx)
        ("index",  LandmarkIndex.INDEX_TIP,  LandmarkIndex.INDEX_PIP),
        ("middle", LandmarkIndex.MIDDLE_TIP, LandmarkIndex.MIDDLE_PIP),
        ("ring",   LandmarkIndex.RING_TIP,   LandmarkIndex.RING_PIP),
        ("pinky",  LandmarkIndex.PINKY_TIP,  LandmarkIndex.PINKY_PIP),
    ]

    def __init__(
        self,
        hysteresis_high: float = 0.55,
        hysteresis_low: float = 0.35,
        rolling_window: int = 7,
    ):
        self._finger_filters: List[HysteresisFilter] = [
            HysteresisFilter(high_threshold=hysteresis_high, low_threshold=hysteresis_low)
            for _ in range(5)
        ]
        self._count_filter = RollingModeFilter(window_size=rolling_window)

        logger.info(
            f"GestureRecognizer v3 (Y-position): "
            f"hysteresis=[{hysteresis_low}, {hysteresis_high}], "
            f"rolling_window={rolling_window}"
        )

    def recognize(self, hand: HandData) -> GestureResult:
        """Analyze hand landmarks and return gesture info."""
        extension_ratios = self._compute_extension_ratios(hand)
        finger_states = self._apply_hysteresis(extension_ratios)
        raw_count = sum(finger_states)
        filtered_count = self._count_filter.update(raw_count)

        return GestureResult(
            finger_states=finger_states,
            finger_count=filtered_count,
            raw_finger_count=raw_count,
            extension_ratios=extension_ratios,
            handedness=hand.handedness,
            confidence=hand.confidence,
            is_stable=self._count_filter.is_stable,
        )

    def _compute_extension_ratios(self, hand: HandData) -> List[float]:
        """
        Compute extension ratio for each finger.

        Returns: [thumb, index, middle, ring, pinky] each 0.0-1.0
        """
        lm = hand.landmarks
        ratios = []

        # Hand height for normalization (wrist to middle MCP)
        wrist = lm[LandmarkIndex.WRIST]
        middle_mcp = lm[LandmarkIndex.MIDDLE_MCP]
        hand_height = _distance_2d(wrist, middle_mcp)

        if hand_height < 0.01:
            return [0.0, 0.0, 0.0, 0.0, 0.0]

        # ── THUMB ──
        thumb_ratio = self._compute_thumb_ratio(hand, hand_height)
        ratios.append(thumb_ratio)

        # ── INDEX, MIDDLE, RING, PINKY ──
        # Simple and reliable: is the tip above or below the PIP joint?
        for name, tip_idx, pip_idx in self.FINGER_LANDMARKS:
            tip = lm[tip_idx]
            pip_joint = lm[pip_idx]

            # In normalized coordinates, Y increases downward.
            # pip.y - tip.y > 0 means tip is ABOVE pip = extended
            # pip.y - tip.y < 0 means tip is BELOW pip = curled
            y_diff = (pip_joint.y - tip.y) / hand_height

            # Map to 0.0-1.0:
            # -0.05 or less → 0.0 (clearly curled)
            # +0.10 or more → 1.0 (clearly extended)
            # Linear in between
            curl_threshold = -0.05
            extend_threshold = 0.10
            range_size = extend_threshold - curl_threshold

            if range_size < 0.001:
                ratio = 0.0
            else:
                ratio = (y_diff - curl_threshold) / range_size
                ratio = max(0.0, min(1.0, ratio))

            ratios.append(ratio)

        return ratios

    def _compute_thumb_ratio(self, hand: HandData, hand_height: float) -> float:
        """
        Compute thumb extension ratio.

        The thumb moves laterally, so Y-comparison doesn't work.
        Instead, compare thumb tip X position to thumb IP X position.

        For a right hand (user's perspective, mirrored image):
            Extended: thumb tip is to the LEFT of IP (tip.x < ip.x)
        For a left hand:
            Extended: thumb tip is to the RIGHT of IP (tip.x > ip.x)

        We also use the distance from thumb tip to index MCP as a
        secondary signal — when thumb is tucked, it's close to palm.
        """
        lm = hand.landmarks
        thumb_tip = lm[LandmarkIndex.THUMB_TIP]
        thumb_ip = lm[LandmarkIndex.THUMB_IP]
        thumb_mcp = lm[LandmarkIndex.THUMB_MCP]
        index_mcp = lm[LandmarkIndex.INDEX_MCP]

        # Primary: X distance of tip from IP, relative to hand height
        x_diff = abs(thumb_tip.x - thumb_ip.x) / hand_height

        # Secondary: distance from thumb tip to index MCP
        # (tucked thumb is close to index MCP)
        tip_to_index = _distance_2d(thumb_tip, index_mcp) / hand_height

        # Combine: thumb is extended if it's far from IP horizontally
        # AND reasonably far from the index MCP
        # Tucked thumb: x_diff ≈ 0.05-0.15, tip_to_index ≈ 0.1-0.3
        # Extended thumb: x_diff ≈ 0.3-0.6, tip_to_index ≈ 0.5-0.9

        # Use the average of both signals
        raw = (x_diff + tip_to_index) / 2.0

        # Map to 0-1: tucked ≈ 0.15, extended ≈ 0.5
        curl_thresh = 0.18
        extend_thresh = 0.40
        range_size = extend_thresh - curl_thresh

        if range_size < 0.001:
            return 0.0

        ratio = (raw - curl_thresh) / range_size
        return max(0.0, min(1.0, ratio))

    def _apply_hysteresis(self, ratios: List[float]) -> List[bool]:
        return [self._finger_filters[i].update(r) for i, r in enumerate(ratios)]

    def reset(self) -> None:
        for f in self._finger_filters:
            f.reset()
        self._count_filter.reset()
        logger.debug("GestureRecognizer filters reset")