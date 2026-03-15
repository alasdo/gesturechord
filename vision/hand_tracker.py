"""
Hand tracking via MediaPipe HandLandmarker (Tasks API).

IMPORTANT: MediaPipe >= 0.10.21 removed the legacy mp.solutions.hands API.
This module uses the new Tasks API (mediapipe.tasks.python.vision.HandLandmarker)
which requires downloading a .task model bundle file.

The model file is automatically downloaded on first run if not present.
Download URL: https://storage.googleapis.com/mediapipe-models/hand_landmarker/hand_landmarker/float16/1/hand_landmarker.task

Design decisions:
    - Uses VIDEO running mode (not LIVE_STREAM) because VIDEO mode is
      synchronous — we call detect_for_video() and get results immediately.
      LIVE_STREAM mode is async with callbacks, which adds complexity for
      no benefit in our single-threaded pipeline.
    - VIDEO mode requires monotonically increasing timestamps, which we
      generate from time.perf_counter().
    - Wraps MediaPipe completely: no MediaPipe types leak into the rest of
      the codebase. This means we can swap to Leap Motion, a custom model,
      or any other tracking backend by changing only this file.
    - Returns HandData dataclass with normalized landmarks, pixel landmarks,
      handedness, and confidence. Downstream code works with these.

Handedness note:
    The new Tasks API handedness classification assumes the input image is
    NOT mirrored. Since our Camera class flips the frame for display, we
    need to invert the handedness label. If camera mirror is True, a
    "Left" result from MediaPipe means the user's RIGHT hand.
"""

import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Tuple

import cv2
import numpy as np
import mediapipe as mp
from mediapipe.tasks.python import BaseOptions
from mediapipe.tasks.python.vision import (
    HandLandmarker,
    HandLandmarkerOptions,
    RunningMode,
)


logger = logging.getLogger("gesturechord.vision.hand_tracker")


# Model download URL and local path
MODEL_URL = "https://storage.googleapis.com/mediapipe-models/hand_landmarker/hand_landmarker/float16/1/hand_landmarker.task"
MODEL_FILENAME = "hand_landmarker.task"


# MediaPipe hand landmark indices (same as legacy API)
# Reference: https://ai.google.dev/edge/mediapipe/solutions/vision/hand_landmarker
class LandmarkIndex:
    """Named constants for MediaPipe hand landmark indices."""
    WRIST = 0
    THUMB_CMC = 1
    THUMB_MCP = 2
    THUMB_IP = 3
    THUMB_TIP = 4
    INDEX_MCP = 5
    INDEX_PIP = 6
    INDEX_DIP = 7
    INDEX_TIP = 8
    MIDDLE_MCP = 9
    MIDDLE_PIP = 10
    MIDDLE_DIP = 11
    MIDDLE_TIP = 12
    RING_MCP = 13
    RING_PIP = 14
    RING_DIP = 15
    RING_TIP = 16
    PINKY_MCP = 17
    PINKY_PIP = 18
    PINKY_DIP = 19
    PINKY_TIP = 20


@dataclass
class HandLandmark:
    """Single landmark point with normalized and pixel coordinates."""
    x: float
    y: float
    z: float
    px: int
    py: int


@dataclass
class HandData:
    """
    Complete tracking data for one detected hand.

    This is the interface between the vision system and the gesture recognizer.
    The gesture recognizer should ONLY depend on this dataclass, never on
    MediaPipe types directly.
    """
    landmarks: List[HandLandmark]
    handedness: str  # "Left" or "Right" (user's actual hand after mirror correction)
    confidence: float
    bbox: Tuple[int, int, int, int] = (0, 0, 0, 0)

    def landmark(self, index: int) -> HandLandmark:
        """Get a specific landmark by index. Use LandmarkIndex constants."""
        return self.landmarks[index]

    @property
    def wrist(self) -> HandLandmark:
        return self.landmarks[LandmarkIndex.WRIST]

    @property
    def is_right(self) -> bool:
        return self.handedness == "Right"


@dataclass
class TrackingResult:
    """Result of processing one frame through hand tracking."""
    hands: List[HandData] = field(default_factory=list)
    frame_rgb: Optional[np.ndarray] = None
    inference_time_ms: float = 0.0

    @property
    def hand_count(self) -> int:
        return len(self.hands)

    @property
    def has_hands(self) -> bool:
        return len(self.hands) > 0

    def get_primary_hand(self, prefer_right: bool = True) -> Optional[HandData]:
        """Get the primary (chord) hand. Falls back to any detected hand."""
        hand = self.get_right_hand() if prefer_right else self.get_left_hand()
        if hand:
            return hand
        # Fallback: return whatever we have
        return self.hands[0] if self.hands else None

    def get_right_hand(self) -> Optional[HandData]:
        """
        Get the user's right hand with smart resolution.

        Strategy:
        1. If exactly one hand labeled "Right", return it.
        2. If two hands, and labels differ, return the "Right" one.
        3. If two hands with SAME label (MediaPipe confusion), use X-position:
           the rightmost hand in the mirrored image = user's right hand.
        4. If one hand with ambiguous label, use X-position heuristic:
           right side of frame (x > 0.5) = likely right hand.
        """
        if not self.hands:
            return None

        right_hands = [h for h in self.hands if h.handedness == "Right"]
        left_hands = [h for h in self.hands if h.handedness == "Left"]

        # Clear case: one right, zero or one left
        if len(right_hands) == 1:
            return right_hands[0]

        # Two hands, both labeled same — use X position
        if len(self.hands) == 2:
            # In mirrored image: user's right hand appears on RIGHT side
            # Higher wrist.x = further right in frame
            sorted_by_x = sorted(self.hands, key=lambda h: h.wrist.x, reverse=True)
            return sorted_by_x[0]  # Rightmost = right hand

        # Single hand, labeled "Left" — check if it's on the right side
        if len(self.hands) == 1:
            hand = self.hands[0]
            if hand.wrist.x > 0.5:
                return hand  # Probably right hand despite label
            return None

        return None

    def get_left_hand(self) -> Optional[HandData]:
        """
        Get the user's left hand (modifier/expression hand).

        Same resolution logic as get_right_hand but inverted.
        """
        if not self.hands:
            return None

        left_hands = [h for h in self.hands if h.handedness == "Left"]

        if len(left_hands) == 1:
            return left_hands[0]

        # Two hands — leftmost in frame = user's left hand
        if len(self.hands) == 2:
            sorted_by_x = sorted(self.hands, key=lambda h: h.wrist.x)
            return sorted_by_x[0]  # Leftmost = left hand

        # Single hand, labeled "Right" — check if it's on the left side
        if len(self.hands) == 1:
            hand = self.hands[0]
            if hand.wrist.x < 0.5:
                return hand  # Probably left hand despite label
            return None

        return None


def _find_model_path() -> Optional[str]:
    """
    Find the hand_landmarker.task model file.

    Search order:
    1. Current working directory
    2. ./models/ directory
    3. Same directory as this script's parent (project root)
    """
    search_paths = [
        Path.cwd() / MODEL_FILENAME,
        Path.cwd() / "models" / MODEL_FILENAME,
        Path(__file__).parent.parent / MODEL_FILENAME,
        Path(__file__).parent.parent / "models" / MODEL_FILENAME,
    ]

    for path in search_paths:
        if path.exists():
            return str(path)

    return None


def download_model(target_dir: str = ".") -> str:
    """
    Download the hand_landmarker.task model if not already present.

    Args:
        target_dir: Directory to save the model file.

    Returns:
        Path to the model file.
    """
    target_path = Path(target_dir) / MODEL_FILENAME

    if target_path.exists():
        logger.info(f"Model already exists: {target_path}")
        return str(target_path)

    logger.info(f"Downloading hand landmarker model...")
    logger.info(f"URL: {MODEL_URL}")
    logger.info(f"This only happens once. The model is ~12 MB.")

    try:
        import urllib.request

        # Show download progress
        def _report(block_num, block_size, total_size):
            downloaded = block_num * block_size
            if total_size > 0:
                pct = min(100, downloaded * 100 // total_size)
                mb = downloaded / (1024 * 1024)
                total_mb = total_size / (1024 * 1024)
                print(f"\r  Downloading: {mb:.1f}/{total_mb:.1f} MB ({pct}%)", end="", flush=True)

        urllib.request.urlretrieve(MODEL_URL, str(target_path), reporthook=_report)
        print()  # newline after progress

        size_mb = target_path.stat().st_size / (1024 * 1024)
        logger.info(f"Model downloaded successfully ({size_mb:.1f} MB)")
        return str(target_path)

    except Exception as e:
        # Clean up partial download
        if target_path.exists():
            target_path.unlink()

        logger.error(f"Failed to download model: {e}")
        logger.error(
            f"\nPlease download manually from:\n"
            f"  {MODEL_URL}\n"
            f"and place it at:\n"
            f"  {target_path.absolute()}\n"
        )
        raise RuntimeError(
            f"Could not download hand landmarker model. "
            f"Please download it manually from {MODEL_URL} "
            f"and place it in your project directory."
        ) from e


class HandTracker:
    """
    MediaPipe HandLandmarker wrapper for real-time hand landmark detection.

    Uses the new Tasks API in VIDEO running mode for synchronous
    per-frame detection with temporal tracking.

    Args:
        max_hands: Maximum number of hands to detect (1 or 2).
        detection_confidence: Minimum confidence for initial hand detection.
        tracking_confidence: Minimum confidence for frame-to-frame tracking.
        model_path: Path to hand_landmarker.task file. Auto-downloads if None.
        camera_mirrored: Set True if the camera image is horizontally flipped.
            This inverts the handedness labels to match the user's actual hands.
    """

    def __init__(
        self,
        max_hands: int = 2,
        detection_confidence: float = 0.6,
        tracking_confidence: float = 0.5,
        model_path: Optional[str] = None,
        camera_mirrored: bool = True,
    ):
        self.max_hands = max_hands
        self.detection_confidence = detection_confidence
        self.tracking_confidence = tracking_confidence
        self.camera_mirrored = camera_mirrored
        self._model_path = model_path

        self._landmarker: Optional[HandLandmarker] = None
        self._is_initialized = False
        self._start_time: float = 0.0

        logger.info(
            f"HandTracker config: max_hands={max_hands}, "
            f"detection_conf={detection_confidence}, "
            f"tracking_conf={tracking_confidence}, "
            f"mirrored={camera_mirrored}"
        )

    def initialize(self) -> None:
        """
        Initialize the HandLandmarker.

        Downloads the model file if not found, then creates the detector.
        May take 1-2 seconds for model loading.
        """
        start = time.perf_counter()

        # Resolve model path
        if self._model_path is None:
            self._model_path = _find_model_path()

        if self._model_path is None or not Path(self._model_path).exists():
            self._model_path = download_model(target_dir=".")

        logger.info(f"Loading model from: {self._model_path}")

        options = HandLandmarkerOptions(
            base_options=BaseOptions(model_asset_path=self._model_path),
            running_mode=RunningMode.VIDEO,
            num_hands=self.max_hands,
            min_hand_detection_confidence=self.detection_confidence,
            min_tracking_confidence=self.tracking_confidence,
        )

        self._landmarker = HandLandmarker.create_from_options(options)
        self._start_time = time.perf_counter()

        elapsed = (time.perf_counter() - start) * 1000
        self._is_initialized = True
        logger.info(f"HandLandmarker initialized in {elapsed:.0f}ms")

    def process_frame(self, frame_bgr: np.ndarray) -> TrackingResult:
        """
        Process a single BGR frame and return hand tracking results.

        Args:
            frame_bgr: OpenCV BGR frame from camera.

        Returns:
            TrackingResult with detected hands and metadata.
        """
        if not self._is_initialized:
            raise RuntimeError(
                "HandTracker.initialize() must be called before process_frame()"
            )

        start = time.perf_counter()
        h, w, _ = frame_bgr.shape

        # Convert BGR → RGB for MediaPipe
        frame_rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)

        # Create MediaPipe Image wrapper
        mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=frame_rgb)

        # Compute timestamp in ms (must be monotonically increasing)
        timestamp_ms = int((time.perf_counter() - self._start_time) * 1000)

        # Run detection
        try:
            results = self._landmarker.detect_for_video(mp_image, timestamp_ms)
        except Exception as e:
            logger.warning(f"Detection failed: {e}")
            return TrackingResult(frame_rgb=frame_rgb, inference_time_ms=0.0)

        inference_ms = (time.perf_counter() - start) * 1000

        tracking_result = TrackingResult(
            frame_rgb=frame_rgb,
            inference_time_ms=inference_ms,
        )

        # No hands detected
        if not results.hand_landmarks:
            return tracking_result

        # Convert each detected hand to our HandData format
        for hand_idx in range(len(results.hand_landmarks)):
            hand_lms = results.hand_landmarks[hand_idx]
            handedness_categories = results.handedness[hand_idx]

            landmarks = []
            min_x, min_y = w, h
            max_x, max_y = 0, 0

            for lm in hand_lms:
                px = int(lm.x * w)
                py = int(lm.y * h)

                landmarks.append(HandLandmark(
                    x=lm.x,
                    y=lm.y,
                    z=lm.z,
                    px=min(max(px, 0), w - 1),
                    py=min(max(py, 0), h - 1),
                ))

                min_x = min(min_x, px)
                min_y = min(min_y, py)
                max_x = max(max_x, px)
                max_y = max(max_y, py)

            # Extract handedness label and confidence
            hand_label = handedness_categories[0].category_name  # "Left" or "Right"
            hand_confidence = handedness_categories[0].score

            # Correct handedness for mirrored camera input.
            # When camera flips the image, MediaPipe sees your right hand
            # as left and vice versa. We invert to match the user's perspective.
            if self.camera_mirrored:
                hand_label = "Right" if hand_label == "Left" else "Left"

            # Bounding box with padding
            pad = 20
            bbox = (
                max(0, min_x - pad),
                max(0, min_y - pad),
                min(w, max_x + pad),
                min(h, max_y + pad),
            )

            tracking_result.hands.append(HandData(
                landmarks=landmarks,
                handedness=hand_label,
                confidence=hand_confidence,
                bbox=bbox,
            ))

        logger.debug(
            f"Tracking: {tracking_result.hand_count} hand(s), "
            f"inference={inference_ms:.1f}ms"
        )

        return tracking_result

    def release(self) -> None:
        """Release MediaPipe resources."""
        if self._landmarker is not None:
            self._landmarker.close()
            self._landmarker = None
            self._is_initialized = False
            logger.info("HandTracker released")

    def __enter__(self) -> "HandTracker":
        self.initialize()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.release()