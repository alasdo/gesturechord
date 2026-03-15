"""
Signal filtering utilities for gesture stabilization.

Why these exist:
    MediaPipe landmarks jitter frame-to-frame, especially in marginal lighting.
    A finger that is borderline extended/curled will flicker between states.
    Raw finger counts are unusable for musical control — they must be filtered.

Two complementary filters are provided:

1. HysteresisFilter: Per-signal filter that requires a value to cross a higher
   threshold to turn "on" and a lower threshold to turn "off". Prevents rapid
   toggling when a value hovers near a single threshold.

2. RollingModeFilter: Maintains a sliding window of recent values and outputs
   the most common value (statistical mode). Smooths out transient glitches.

Used together: hysteresis first (per-finger), then rolling mode on the
final finger count. This two-stage approach catches both per-finger jitter
and aggregate count instability.
"""

from collections import deque, Counter
from typing import Optional


class HysteresisFilter:
    """
    Prevents rapid toggling of a binary state by requiring the input to cross
    different thresholds for activation vs deactivation.

    Example for finger detection:
        - Finger is "up" when extension ratio > 0.65 (high threshold)
        - Finger is "down" when extension ratio < 0.55 (low threshold)
        - Between 0.55 and 0.65, the previous state holds

    This 0.10 dead zone eliminates flicker when a finger is borderline.

    Args:
        high_threshold: Value must exceed this to transition to True.
        low_threshold: Value must drop below this to transition to False.
            Must be strictly less than high_threshold.
    """

    def __init__(self, high_threshold: float = 0.65, low_threshold: float = 0.55):
        if low_threshold >= high_threshold:
            raise ValueError(
                f"low_threshold ({low_threshold}) must be < high_threshold ({high_threshold}). "
                f"The gap between them is the dead zone that prevents flicker."
            )
        self.high_threshold = high_threshold
        self.low_threshold = low_threshold
        self._state: bool = False

    @property
    def state(self) -> bool:
        """Current filtered state."""
        return self._state

    def update(self, value: float) -> bool:
        """
        Feed a new raw value and get the filtered binary state.

        Args:
            value: Raw continuous value (e.g., finger extension ratio 0.0–1.0).

        Returns:
            Filtered boolean state (True = active/extended, False = inactive/curled).
        """
        if value > self.high_threshold:
            self._state = True
        elif value < self.low_threshold:
            self._state = False
        # else: retain previous state (dead zone)
        return self._state

    def reset(self) -> None:
        """Reset to initial inactive state."""
        self._state = False


class RollingModeFilter:
    """
    Outputs the statistical mode (most frequent value) over a sliding window
    of recent observations.

    Why mode instead of mean/median:
        Finger counts are discrete integers. Mean of [3, 3, 3, 4, 3] = 3.2,
        which you'd round to 3 — but that's fragile. Mode of [3, 3, 3, 4, 3] = 3,
        which is robust and unambiguous. Mode also handles the common case where
        a single frame glitches to a wrong value: the majority rules.

    Tie-breaking: When multiple values share the highest frequency, the most
    recently seen value among the tied candidates wins. This makes the filter
    responsive to genuine transitions while still filtering noise.

    Args:
        window_size: Number of recent values to consider. Larger = more stable
            but slower to respond to real changes. 5–7 is the sweet spot for
            30 FPS input (150–230ms window).
    """

    def __init__(self, window_size: int = 5):
        if window_size < 1:
            raise ValueError(f"window_size must be >= 1, got {window_size}")
        self.window_size = window_size
        self._buffer: deque = deque(maxlen=window_size)
        self._last_value: Optional[int] = None

    @property
    def value(self) -> Optional[int]:
        """Current filtered value, or None if no data yet."""
        return self._last_value

    @property
    def is_stable(self) -> bool:
        """True if buffer is full and all values are identical."""
        return (
            len(self._buffer) == self.window_size
            and len(set(self._buffer)) == 1
        )

    @property
    def confidence(self) -> float:
        """
        Fraction of buffer that agrees with the current mode.
        1.0 = completely stable, 0.2 = very noisy.
        Returns 0.0 if buffer is empty.
        """
        if not self._buffer:
            return 0.0
        if self._last_value is None:
            return 0.0
        count = sum(1 for v in self._buffer if v == self._last_value)
        return count / len(self._buffer)

    def update(self, value: int) -> int:
        """
        Feed a new value and get the filtered output.

        Args:
            value: New discrete value (e.g., finger count 0–5).

        Returns:
            The statistical mode of the sliding window.
        """
        self._buffer.append(value)

        # Find mode with recency-based tie-breaking
        counts = Counter(self._buffer)
        max_count = max(counts.values())
        candidates = [v for v, c in counts.items() if c == max_count]

        if len(candidates) == 1:
            self._last_value = candidates[0]
        else:
            # Tie-break: pick the candidate that appeared most recently
            for recent_value in reversed(self._buffer):
                if recent_value in candidates:
                    self._last_value = recent_value
                    break

        return self._last_value

    def reset(self) -> None:
        """Clear buffer and reset state."""
        self._buffer.clear()
        self._last_value = None


class ExponentialMovingAverage:
    """
    Smooths a continuous signal using exponential moving average.

    EMA formula: output = alpha * new_value + (1 - alpha) * previous_output

    Used for smoothing continuous gesture data (hand Y position → CC value)
    to eliminate frame-to-frame jitter while allowing intentional movement.

    Args:
        alpha: Smoothing factor 0.0-1.0. Higher = less smoothing (more responsive).
            0.1 = very smooth (laggy), 0.3 = moderate, 0.5 = light smoothing.
        dead_zone: Minimum change in output before reporting a new value.
            Prevents micro-jitter from producing MIDI CC spam.
    """

    def __init__(self, alpha: float = 0.3, dead_zone: float = 1.5):
        self.alpha = alpha
        self.dead_zone = dead_zone
        self._value: Optional[float] = None
        self._last_reported: Optional[float] = None

    @property
    def value(self) -> Optional[float]:
        """Current smoothed value."""
        return self._value

    def update(self, raw_value: float) -> Optional[float]:
        """
        Feed a new raw value and get smoothed output.

        Args:
            raw_value: New raw continuous value.

        Returns:
            Smoothed value if it changed beyond the dead zone, None otherwise.
            This lets you skip sending MIDI when nothing meaningful changed.
        """
        if self._value is None:
            self._value = raw_value
            self._last_reported = raw_value
            return raw_value

        # Apply EMA
        self._value = self.alpha * raw_value + (1.0 - self.alpha) * self._value

        # Dead zone: only report if change exceeds threshold
        if self._last_reported is None:
            self._last_reported = self._value
            return self._value

        if abs(self._value - self._last_reported) >= self.dead_zone:
            self._last_reported = self._value
            return self._value

        return None  # No meaningful change

    def reset(self) -> None:
        """Reset to uninitialized state."""
        self._value = None
        self._last_reported = None