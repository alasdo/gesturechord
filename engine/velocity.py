"""
Velocity controller — maps hand movement speed to MIDI velocity.

How it works:
    Track the right hand wrist position frame-over-frame. Compute the
    distance moved per frame. Map that speed to a velocity range.

    Fast hand movement (quick gesture) → high velocity (loud, punchy)
    Slow/still hand (gentle gesture) → low velocity (soft, subtle)

    This makes the instrument feel expressive — you can play the same
    chord aggressively or gently depending on how you move.

    The velocity is computed at the moment a chord triggers, not continuously.
    It uses the average speed over the last few frames leading up to the trigger.
"""

import logging
import math
from collections import deque
from typing import Optional

from utils.filters import ExponentialMovingAverage


logger = logging.getLogger("gesturechord.engine.velocity")


class VelocityController:
    """
    Maps hand movement speed to MIDI velocity.

    Args:
        min_velocity: Minimum velocity (for still hand). 40-60 is typical.
        max_velocity: Maximum velocity (for fast movement). 110-127.
        speed_low: Normalized speed below which velocity = min. (~0.002)
        speed_high: Normalized speed above which velocity = max. (~0.05)
        smoothing_alpha: EMA smoothing for speed signal.
        window_size: Number of frames to average speed over.
        enabled: Whether dynamic velocity is active.
    """

    def __init__(
        self,
        min_velocity: int = 50,
        max_velocity: int = 120,
        speed_low: float = 0.003,
        speed_high: float = 0.04,
        smoothing_alpha: float = 0.4,
        window_size: int = 5,
        enabled: bool = True,
    ):
        self.min_velocity = min_velocity
        self.max_velocity = max_velocity
        self.speed_low = speed_low
        self.speed_high = speed_high
        self.enabled = enabled

        self._prev_x: Optional[float] = None
        self._prev_y: Optional[float] = None
        self._speed_buffer: deque = deque(maxlen=window_size)
        self._ema = ExponentialMovingAverage(alpha=smoothing_alpha, dead_zone=0.0)
        self._current_velocity: int = 100

        logger.info(f"VelocityController: vel=[{min_velocity}-{max_velocity}], "
                    f"speed=[{speed_low}-{speed_high}], enabled={enabled}")

    @property
    def velocity(self) -> int:
        """Current computed velocity."""
        return self._current_velocity

    def update(self, wrist_x: Optional[float], wrist_y: Optional[float]) -> None:
        """
        Feed the current wrist position. Call every frame.

        Args:
            wrist_x: Normalized wrist X (0.0-1.0), or None if no hand.
            wrist_y: Normalized wrist Y (0.0-1.0), or None if no hand.
        """
        if not self.enabled or wrist_x is None or wrist_y is None:
            self._prev_x = None
            self._prev_y = None
            self._speed_buffer.clear()
            self._current_velocity = (self.min_velocity + self.max_velocity) // 2
            return

        if self._prev_x is not None and self._prev_y is not None:
            dx = wrist_x - self._prev_x
            dy = wrist_y - self._prev_y
            speed = math.sqrt(dx * dx + dy * dy)
            self._speed_buffer.append(speed)

        self._prev_x = wrist_x
        self._prev_y = wrist_y

    def get_trigger_velocity(self) -> int:
        """
        Get velocity for a chord trigger. Call at the moment of CHORD_ON/CHANGE.

        Returns the velocity based on recent hand speed, then keeps that
        value stable until the next trigger.
        """
        if not self.enabled or not self._speed_buffer:
            return (self.min_velocity + self.max_velocity) // 2

        # Average speed over recent frames
        avg_speed = sum(self._speed_buffer) / len(self._speed_buffer)

        # Map to velocity range
        speed_range = self.speed_high - self.speed_low
        if speed_range < 0.0001:
            normalized = 0.5
        else:
            normalized = (avg_speed - self.speed_low) / speed_range
            normalized = max(0.0, min(1.0, normalized))

        vel = int(self.min_velocity + normalized * (self.max_velocity - self.min_velocity))
        self._current_velocity = vel
        return vel

    def reset(self):
        self._prev_x = None
        self._prev_y = None
        self._speed_buffer.clear()
        self._current_velocity = (self.min_velocity + self.max_velocity) // 2