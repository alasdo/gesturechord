"""
Expression controller — continuous MIDI CC from hand position.

Maps the left hand's Y position in the frame to a MIDI CC value (0-127).
Runs independently every frame, not tied to chord events.

How it works:
    - Hand high in frame → CC 127 (maximum)
    - Hand low (near zone line) → CC 0 (minimum)
    - Smoothed with EMA to eliminate jitter
    - Dead zone prevents sending CC when nothing changed
    - Only sends CC when a value actually changes (saves MIDI bandwidth)

In FL Studio:
    1. Right-click any knob/slider in a plugin
    2. Select "Link to controller"
    3. Move your left hand — FL Studio auto-detects the CC
    4. Click "Accept"
    Now that parameter follows your hand movement.

Default CC numbers:
    CC 1  = Mod Wheel (primary expression — widely supported)
    CC 74 = Brightness/Cutoff (common for filter control)

The user can map these to any parameter in FL Studio.
"""

import logging
from typing import Optional

from utils.filters import ExponentialMovingAverage


logger = logging.getLogger("gesturechord.engine.expression")


class ExpressionController:
    """
    Converts hand Y position to MIDI CC values.

    Args:
        cc_number: MIDI CC number to send (1 = mod wheel, 74 = cutoff).
        zone_top: Normalized Y position for CC=127 (top of range).
            Default 0.15 = near top of frame.
        zone_bottom: Normalized Y position for CC=0 (bottom of range).
            Default 0.70 = just above the performance zone line.
        smoothing_alpha: EMA smoothing factor. Lower = smoother.
        dead_zone: Minimum CC change before sending. Prevents jitter.
        enabled: Whether CC output is active.
    """

    def __init__(
        self,
        cc_number: int = 1,
        zone_top: float = 0.15,
        zone_bottom: float = 0.70,
        smoothing_alpha: float = 0.25,
        dead_zone: float = 2.0,
        enabled: bool = True,
        invert: bool = True,
    ):
        self.cc_number = cc_number
        self.zone_top = zone_top
        self.zone_bottom = zone_bottom
        self.enabled = enabled
        self.invert = invert

        self._ema = ExponentialMovingAverage(alpha=smoothing_alpha, dead_zone=dead_zone)
        self._last_cc_value: int = 0
        self._current_cc_value: int = 0

        axis = "Y(inv)" if invert else "X"
        logger.info(
            f"ExpressionController: CC{cc_number} {axis}, "
            f"zone=[{zone_top:.2f}-{zone_bottom:.2f}], "
            f"alpha={smoothing_alpha}, dead_zone={dead_zone}"
        )

    @property
    def cc_value(self) -> int:
        """Current CC value (0-127)."""
        return self._current_cc_value

    @property
    def cc_normalized(self) -> float:
        """Current CC as 0.0-1.0 for UI display."""
        return self._current_cc_value / 127.0

    def update(self, hand_pos: Optional[float]) -> Optional[int]:
        """
        Process one frame of hand position data.

        Args:
            hand_pos: Normalized position (0.0-1.0). For Y: 0=top, 1=bottom.
                For X: 0=left, 1=right. None if no hand detected.

        Returns:
            CC value (0-127) if it changed, None if no update needed.
        """
        if not self.enabled or hand_pos is None:
            return None

        range_size = self.zone_bottom - self.zone_top
        if range_size < 0.01:
            return None

        normalized = (hand_pos - self.zone_top) / range_size
        normalized = max(0.0, min(1.0, normalized))

        # Invert: Y axis (hand high = max), don't invert: X axis (hand right = max)
        if self.invert:
            normalized = 1.0 - normalized

        raw_cc = normalized * 127.0

        # Smooth
        smoothed = self._ema.update(raw_cc)
        if smoothed is None:
            return None  # No meaningful change

        # Convert to integer CC
        cc_int = max(0, min(127, int(round(smoothed))))
        self._current_cc_value = cc_int

        if cc_int != self._last_cc_value:
            self._last_cc_value = cc_int
            return cc_int

        return None

    def reset(self) -> None:
        self._ema.reset()
        self._last_cc_value = 0
        self._current_cc_value = 0