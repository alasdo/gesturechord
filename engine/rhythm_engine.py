"""
Rhythm engine — pump retrigger detection.

Detects downward hand pumping and emits retrigger events. This turns
sustained chords into rhythmic stabs.

How it works:
    1. Track wrist Y position frame-over-frame
    2. Compute Y velocity (delta per frame)
    3. Smooth velocity with EMA to filter noise
    4. Detect downward peaks: velocity was increasing (hand accelerating down),
       then starts decreasing (hand decelerating) → trigger at the peak
    5. Apply cooldown to prevent double-triggers
    6. Map peak velocity magnitude to MIDI velocity

The trigger fires at the PEAK of downward motion — not at the start of
movement, not at the bottom. This feels musical because:
- It fires when the hand has maximum energy (like a drumstick hitting)
- It's immune to slow drifts (only fast motion triggers)
- One clean trigger per pump, no stuttering

Thresholds:
    - Velocity threshold: minimum downward speed to consider a pump
      (filters out hand tremor and slow drift)
    - Cooldown: minimum time between triggers (prevents double-fire)
    - Both configurable in config.yaml
"""

import logging
import time
from dataclasses import dataclass
from typing import Optional
from collections import deque

from utils.filters import ExponentialMovingAverage


logger = logging.getLogger("gesturechord.engine.rhythm")


@dataclass
class PumpEvent:
    """Emitted when a pump retrigger is detected."""
    velocity: int           # MIDI velocity (0-127) from pump amplitude
    raw_speed: float        # Raw peak velocity for debugging
    is_retrigger: bool      # True = retrigger existing chord (not a new chord)


class RhythmEngine:
    """
    Detects hand pump gestures for chord retriggering.

    Feed it the right hand wrist Y position every frame. When it detects
    a downward pump, it returns a PumpEvent. Otherwise returns None.

    The state machine controls WHAT chord plays.
    The rhythm engine controls WHEN it retriggers.

    Args:
        velocity_threshold: Minimum downward speed to count as a pump.
            Normalized units: delta-Y per frame. At 30 FPS with normalized
            0-1 coordinates, a visible pump is ~0.015-0.04 per frame.
            Hand tremor is ~0.002-0.005. Default 0.010 sits between.
        cooldown_ms: Minimum time between triggers in milliseconds.
            80ms = max 12.5 triggers/sec. 60ms for faster response.
        smoothing_alpha: EMA smoothing for velocity signal. Lower = smoother.
        min_velocity: Minimum MIDI velocity for weakest detectable pump.
        max_velocity: Maximum MIDI velocity for strongest pump.
        speed_for_max: Downward speed that maps to max velocity.
        enabled: Whether pump detection is active.
    """

    def __init__(
        self,
        velocity_threshold: float = 0.010,
        cooldown_ms: float = 80.0,
        smoothing_alpha: float = 0.5,
        min_velocity: int = 45,
        max_velocity: int = 120,
        speed_for_max: float = 0.045,
        enabled: bool = True,
    ):
        self.velocity_threshold = velocity_threshold
        self.cooldown_ms = cooldown_ms
        self.min_velocity = min_velocity
        self.max_velocity = max_velocity
        self.speed_for_max = speed_for_max
        self.enabled = enabled

        self._prev_y: Optional[float] = None
        self._ema = ExponentialMovingAverage(alpha=smoothing_alpha, dead_zone=0.0)
        self._prev_velocity: float = 0.0  # Smoothed Y velocity from last frame
        self._last_trigger_time: float = 0.0
        self._peak_velocity: float = 0.0  # Track the peak for this pump
        self._in_pump: bool = False  # True while velocity is above threshold

        logger.info(
            f"RhythmEngine: threshold={velocity_threshold}, "
            f"cooldown={cooldown_ms}ms, vel=[{min_velocity}-{max_velocity}], "
            f"enabled={enabled}"
        )

    @property
    def is_pumping(self) -> bool:
        """True if currently in a pump motion (for UI feedback)."""
        return self._in_pump

    def update(self, wrist_y: Optional[float]) -> Optional[PumpEvent]:
        """
        Feed one frame of wrist Y position. Returns PumpEvent if triggered.

        Args:
            wrist_y: Normalized wrist Y (0.0=top, 1.0=bottom), or None if no hand.

        Returns:
            PumpEvent if a pump retrigger was detected, None otherwise.
        """
        if not self.enabled or wrist_y is None:
            self._prev_y = None
            self._in_pump = False
            self._peak_velocity = 0.0
            return None

        if self._prev_y is None:
            self._prev_y = wrist_y
            return None

        # Compute raw Y velocity (positive = moving DOWN in frame)
        raw_vel = wrist_y - self._prev_y
        self._prev_y = wrist_y

        # Smooth
        smoothed = self._ema.update(raw_vel * 100.0)  # Scale up for better EMA resolution
        if smoothed is None:
            smoothed = raw_vel * 100.0
        smoothed_vel = smoothed / 100.0  # Scale back

        # Only care about downward motion (positive velocity in image coords)
        down_speed = max(0.0, smoothed_vel)

        result = None

        if down_speed > self.velocity_threshold:
            # We're in a pump — track the peak
            self._in_pump = True
            if down_speed > self._peak_velocity:
                self._peak_velocity = down_speed
        elif self._in_pump and self._peak_velocity > self.velocity_threshold:
            # Was pumping, speed has dropped — peak has passed → TRIGGER
            now = time.perf_counter()
            elapsed = (now - self._last_trigger_time) * 1000

            if elapsed >= self.cooldown_ms:
                # Map peak speed to MIDI velocity
                midi_vel = self._speed_to_velocity(self._peak_velocity)
                result = PumpEvent(
                    velocity=midi_vel,
                    raw_speed=self._peak_velocity,
                    is_retrigger=True,
                )
                self._last_trigger_time = now

            # Reset pump state
            self._in_pump = False
            self._peak_velocity = 0.0
        else:
            self._in_pump = False
            self._peak_velocity = 0.0

        self._prev_velocity = smoothed_vel
        return result

    def _speed_to_velocity(self, speed: float) -> int:
        """Map downward speed to MIDI velocity (min_velocity to max_velocity)."""
        # Normalize: threshold = 0%, speed_for_max = 100%
        range_size = self.speed_for_max - self.velocity_threshold
        if range_size < 0.001:
            normalized = 1.0
        else:
            normalized = (speed - self.velocity_threshold) / range_size
            normalized = max(0.0, min(1.0, normalized))

        vel = int(self.min_velocity + normalized * (self.max_velocity - self.min_velocity))
        return max(0, min(127, vel))

    def reset(self):
        self._prev_y = None
        self._prev_velocity = 0.0
        self._peak_velocity = 0.0
        self._in_pump = False
        self._ema.reset()