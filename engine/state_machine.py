"""
Gesture state machine v2 — with transition suppression.

The v1 problem: going from 1 finger to 3 fingers, the user briefly shows 2
fingers (for ~50-100ms) while their middle finger is still extending. The old
state machine confirmed that intermediate "2" and triggered it as a chord
change before "3" arrived.

Solution: SETTLE-THEN-CONFIRM pattern.

When a gesture change is detected, we now require TWO phases:

1. SETTLE phase: The finger count must stop changing. We track the count
   from the previous frame. If it differs from the current frame, the settle
   counter resets. Only when the count has been identical for `settle_frames`
   consecutive frames do we advance to...

2. CONFIRM phase: Same as before — count must stay the same for
   `change_frames` more frames.

Total latency for a chord change = settle_frames + change_frames.
With settle=3 and change=3 at 30 FPS = 200ms total.

This eliminates cascade triggers because the intermediate finger counts
(the "2" in a 1→3 transition) never survive the settle phase — they only
last 1-2 frames before the final count arrives.

Additionally, the fist (0 fingers) is handled specially: it's treated as
an IMMEDIATE note-off with no confirmation. Musical silence should be instant.
"""

import logging
import time
from dataclasses import dataclass
from enum import Enum, auto
from typing import Optional


logger = logging.getLogger("gesturechord.engine.state_machine")


class State(Enum):
    IDLE = auto()
    DETECTING = auto()
    CONFIRMING = auto()
    ACTIVE = auto()
    CHANGING = auto()
    RELEASING = auto()


class EventType(Enum):
    CHORD_ON = auto()
    CHORD_OFF = auto()
    CHORD_CHANGE = auto()
    NONE = auto()


@dataclass
class ChordEvent:
    event_type: EventType
    finger_count: int = 0
    previous_finger_count: int = 0
    state: State = State.IDLE
    confirmation_progress: float = 0.0


class GestureStateMachine:
    """
    Converts per-frame gesture data into debounced chord events.

    v2 improvements:
    - Settle-then-confirm pattern prevents cascade triggers (1→2→3)
    - Fist (0 fingers) is immediate note-off, no confirmation delay
    - Tracks frame-over-frame count changes to detect transitions in progress

    Args:
        confirmation_frames: Frames to confirm a NEW chord from idle/detecting.
        change_frames: Frames to confirm a chord CHANGE while playing.
        settle_frames: Frames the count must be stable before change confirmation
            even begins. This is the key anti-cascade parameter.
        release_grace_ms: Grace period before note-off on hand loss.
        idle_gesture: Finger count meaning silence (0 = fist).
    """

    def __init__(
        self,
        confirmation_frames: int = 5,
        change_frames: int = 4,
        settle_frames: int = 3,
        release_grace_ms: int = 200,
        idle_gesture: int = 0,
    ):
        self.confirmation_frames = confirmation_frames
        self.change_frames = change_frames
        self.settle_frames = settle_frames
        self.release_grace_ms = release_grace_ms
        self.idle_gesture = idle_gesture

        # Core state
        self._state = State.IDLE
        self._active_finger_count = 0
        self._pending_finger_count = 0
        self._confirm_counter = 0
        self._settle_counter = 0
        self._release_start_time = 0.0

        # Transition tracking — previous frame's count
        self._prev_finger_count: Optional[int] = None

        total_change_ms = int((settle_frames + change_frames) / 30.0 * 1000)
        logger.info(
            f"StateMachine v2 config: confirm={confirmation_frames}f, "
            f"change={change_frames}f, settle={settle_frames}f "
            f"(~{total_change_ms}ms change latency), "
            f"release_grace={release_grace_ms}ms"
        )

    @property
    def state(self) -> State:
        return self._state

    @property
    def active_finger_count(self) -> int:
        return self._active_finger_count

    @property
    def is_playing(self) -> bool:
        return self._state == State.ACTIVE

    def update(self, finger_count: Optional[int], is_stable: bool) -> ChordEvent:
        """Process one frame and return a chord event."""

        # Track frame-over-frame changes
        count_changed_this_frame = (
            finger_count is not None
            and self._prev_finger_count is not None
            and finger_count != self._prev_finger_count
        )

        if finger_count is not None:
            self._prev_finger_count = finger_count

        # No hand
        if finger_count is None:
            result = self._handle_no_hand()
            return result

        # Fist = immediate silence (no confirmation needed)
        if finger_count == self.idle_gesture:
            return self._handle_fist()

        # Dispatch to state handler
        if self._state == State.IDLE:
            return self._handle_idle(finger_count, is_stable)
        elif self._state == State.DETECTING:
            return self._handle_detecting(finger_count, is_stable)
        elif self._state == State.CONFIRMING:
            return self._handle_confirming(finger_count, is_stable)
        elif self._state == State.ACTIVE:
            return self._handle_active(finger_count, is_stable, count_changed_this_frame)
        elif self._state == State.CHANGING:
            return self._handle_changing(finger_count, is_stable, count_changed_this_frame)
        elif self._state == State.RELEASING:
            return self._handle_releasing(finger_count, is_stable)

        return self._no_event()

    def reset(self) -> ChordEvent:
        """Force reset. Returns CHORD_OFF if playing."""
        was_playing = self._state == State.ACTIVE
        old_count = self._active_finger_count

        self._state = State.IDLE
        self._active_finger_count = 0
        self._pending_finger_count = 0
        self._confirm_counter = 0
        self._settle_counter = 0
        self._prev_finger_count = None

        logger.info("StateMachine reset to IDLE")

        if was_playing:
            return ChordEvent(EventType.CHORD_OFF, finger_count=old_count, state=State.IDLE)
        return self._no_event()

    # ── Fist handling (immediate, no confirmation) ──

    def _handle_fist(self) -> ChordEvent:
        """Fist = silence. Always immediate."""
        if self._state == State.ACTIVE or self._state == State.CHANGING:
            old_count = self._active_finger_count
            self._state = State.IDLE
            self._active_finger_count = 0
            self._confirm_counter = 0
            self._settle_counter = 0
            logger.info(f"Fist → CHORD_OFF (was {old_count})")
            return ChordEvent(EventType.CHORD_OFF, finger_count=old_count, state=State.IDLE)

        # In any other state, just go idle
        self._state = State.IDLE
        self._confirm_counter = 0
        self._settle_counter = 0
        return self._no_event()

    # ── No hand ──

    def _handle_no_hand(self) -> ChordEvent:
        if self._state == State.IDLE:
            return self._no_event()

        if self._state in (State.DETECTING, State.CONFIRMING, State.CHANGING):
            # Not fully committed — if CHANGING, keep the old chord until grace expires
            if self._state == State.CHANGING:
                self._state = State.RELEASING
                self._release_start_time = time.perf_counter()
                return self._no_event()
            self._state = State.IDLE
            self._confirm_counter = 0
            self._settle_counter = 0
            return self._no_event()

        if self._state == State.ACTIVE:
            self._state = State.RELEASING
            self._release_start_time = time.perf_counter()
            return self._no_event()

        if self._state == State.RELEASING:
            elapsed_ms = (time.perf_counter() - self._release_start_time) * 1000
            if elapsed_ms >= self.release_grace_ms:
                old_count = self._active_finger_count
                self._state = State.IDLE
                self._active_finger_count = 0
                self._confirm_counter = 0
                self._settle_counter = 0
                logger.info(f"Grace expired → CHORD_OFF (was {old_count})")
                return ChordEvent(EventType.CHORD_OFF, finger_count=old_count, state=State.IDLE)

        return self._no_event()

    # ── IDLE ──

    def _handle_idle(self, finger_count: int, is_stable: bool) -> ChordEvent:
        self._state = State.DETECTING
        self._pending_finger_count = finger_count
        self._confirm_counter = 0
        self._settle_counter = 0
        return self._no_event()

    # ── DETECTING ──

    def _handle_detecting(self, finger_count: int, is_stable: bool) -> ChordEvent:
        if finger_count == self._pending_finger_count:
            if is_stable:
                self._state = State.CONFIRMING
                self._confirm_counter = 1
        else:
            self._pending_finger_count = finger_count
            self._confirm_counter = 0

        return ChordEvent(
            EventType.NONE, finger_count=finger_count,
            state=self._state, confirmation_progress=0.0,
        )

    # ── CONFIRMING ──

    def _handle_confirming(self, finger_count: int, is_stable: bool) -> ChordEvent:
        if finger_count != self._pending_finger_count:
            self._state = State.DETECTING
            self._pending_finger_count = finger_count
            self._confirm_counter = 0
            return self._no_event()

        self._confirm_counter += 1
        progress = min(1.0, self._confirm_counter / self.confirmation_frames)

        if self._confirm_counter >= self.confirmation_frames:
            self._state = State.ACTIVE
            self._active_finger_count = finger_count
            self._confirm_counter = 0
            self._settle_counter = 0
            logger.info(f"Confirmed → CHORD_ON (fingers={finger_count})")
            return ChordEvent(
                EventType.CHORD_ON, finger_count=finger_count,
                state=State.ACTIVE, confirmation_progress=1.0,
            )

        return ChordEvent(
            EventType.NONE, finger_count=finger_count,
            state=State.CONFIRMING, confirmation_progress=progress,
        )

    # ── ACTIVE ──

    def _handle_active(
        self, finger_count: int, is_stable: bool, count_changed: bool
    ) -> ChordEvent:
        if finger_count == self._active_finger_count:
            return ChordEvent(
                EventType.NONE, finger_count=finger_count,
                state=State.ACTIVE, confirmation_progress=1.0,
            )

        # Gesture is different — enter CHANGING with settle phase
        self._state = State.CHANGING
        self._pending_finger_count = finger_count
        self._settle_counter = 0  # Must settle first
        self._confirm_counter = 0
        logger.debug(f"Gesture changing: {self._active_finger_count} → {finger_count}")

        return ChordEvent(
            EventType.NONE, finger_count=finger_count,
            state=State.CHANGING, confirmation_progress=0.0,
        )

    # ── CHANGING (the key anti-cascade state) ──

    def _handle_changing(
        self, finger_count: int, is_stable: bool, count_changed: bool
    ) -> ChordEvent:
        # If user went back to the original chord, resume instantly
        if finger_count == self._active_finger_count:
            self._state = State.ACTIVE
            self._settle_counter = 0
            self._confirm_counter = 0
            logger.debug(f"Returned to original → ACTIVE ({finger_count})")
            return ChordEvent(
                EventType.NONE, finger_count=finger_count,
                state=State.ACTIVE, confirmation_progress=1.0,
            )

        # ── SETTLE PHASE ──
        # The count must be the same as pending AND must not have changed
        # from the previous frame. If it changed this frame, reset settle.

        if finger_count != self._pending_finger_count:
            # Different from what we were tracking — restart settle
            self._pending_finger_count = finger_count
            self._settle_counter = 0
            self._confirm_counter = 0
            return ChordEvent(
                EventType.NONE, finger_count=finger_count,
                state=State.CHANGING, confirmation_progress=0.0,
            )

        if count_changed:
            # Count just changed THIS frame to match pending — that means
            # we just arrived here. Reset settle to ensure we wait.
            self._settle_counter = 0
            self._confirm_counter = 0
            return ChordEvent(
                EventType.NONE, finger_count=finger_count,
                state=State.CHANGING, confirmation_progress=0.0,
            )

        # Same count as pending, and it didn't change this frame — settling
        self._settle_counter += 1

        if self._settle_counter < self.settle_frames:
            # Still settling
            total_needed = self.settle_frames + self.change_frames
            progress = min(1.0, self._settle_counter / total_needed)
            return ChordEvent(
                EventType.NONE, finger_count=finger_count,
                state=State.CHANGING, confirmation_progress=progress,
            )

        # ── CONFIRM PHASE (settle complete) ──
        self._confirm_counter += 1
        total_needed = self.settle_frames + self.change_frames
        progress = min(1.0, (self._settle_counter + self._confirm_counter) / total_needed)

        if self._confirm_counter >= self.change_frames:
            # Fully confirmed — change chord
            old_count = self._active_finger_count
            self._state = State.ACTIVE
            self._active_finger_count = finger_count
            self._settle_counter = 0
            self._confirm_counter = 0
            logger.info(f"Change confirmed: {old_count} → {finger_count}")
            return ChordEvent(
                EventType.CHORD_CHANGE, finger_count=finger_count,
                previous_finger_count=old_count,
                state=State.ACTIVE, confirmation_progress=1.0,
            )

        return ChordEvent(
            EventType.NONE, finger_count=finger_count,
            state=State.CHANGING, confirmation_progress=progress,
        )

    # ── RELEASING ──

    def _handle_releasing(self, finger_count: int, is_stable: bool) -> ChordEvent:
        if finger_count == self._active_finger_count:
            self._state = State.ACTIVE
            logger.debug(f"Hand returned same gesture → ACTIVE ({finger_count})")
            return ChordEvent(
                EventType.NONE, finger_count=finger_count,
                state=State.ACTIVE, confirmation_progress=1.0,
            )

        # Different gesture — enter changing
        self._state = State.CHANGING
        self._pending_finger_count = finger_count
        self._settle_counter = 0
        self._confirm_counter = 0
        return ChordEvent(
            EventType.NONE, finger_count=finger_count,
            state=State.CHANGING, confirmation_progress=0.0,
        )

    # ── Helpers ──

    def _no_event(self) -> ChordEvent:
        return ChordEvent(
            EventType.NONE,
            finger_count=self._active_finger_count,
            state=self._state,
        )