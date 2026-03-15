"""
Camera capture wrapper.

Design decisions:
    - Uses cv2.VideoCapture with DirectShow backend on Windows (CAP_DSHOW)
      because the default MSMF backend has known issues with some webcams
      (slow startup, frame drops, incorrect resolution negotiation).
    - Requests 640x480 @ 30 FPS as default. Higher resolution doesn't help
      MediaPipe (it resizes internally) and costs more CPU. Lower resolution
      may hurt landmark accuracy.
    - Measures actual FPS for performance monitoring. If FPS drops below 20,
      the gesture engine should know to increase filter window sizes.
    - Provides frame flipping (horizontal mirror) because webcam input is
      mirrored — your right hand appears on the left side of the image.
      MediaPipe's handedness labels assume a mirrored image, so we flip
      BEFORE processing. This is important: if you skip the flip, left/right
      hand classification will be wrong.

Future considerations:
    - Could add resolution auto-detection
    - Could add camera selection UI if multiple cameras exist
    - Could add frame skipping under high CPU load
"""

import time
import logging
from typing import Optional, Tuple

import cv2
import numpy as np


logger = logging.getLogger("gesturechord.vision.camera")


class Camera:
    """
    Webcam capture with FPS tracking and automatic frame mirroring.

    Usage:
        camera = Camera(device_index=0)
        camera.open()

        while True:
            frame = camera.read()
            if frame is None:
                break
            # process frame...

        camera.release()

    Or as a context manager:
        with Camera() as camera:
            while True:
                frame = camera.read()
                ...
    """

    # Default capture settings
    DEFAULT_WIDTH = 640
    DEFAULT_HEIGHT = 480
    DEFAULT_FPS = 30

    def __init__(
        self,
        device_index: int = 0,
        width: int = DEFAULT_WIDTH,
        height: int = DEFAULT_HEIGHT,
        target_fps: int = DEFAULT_FPS,
        mirror: bool = True,
    ):
        """
        Args:
            device_index: Camera device index. 0 = default/first camera.
                If you have multiple cameras, try 1, 2, etc.
            width: Requested frame width in pixels.
            height: Requested frame height in pixels.
            target_fps: Requested capture frame rate.
            mirror: If True, flip frames horizontally. Should be True for
                front-facing webcams so the user sees a natural mirror image
                and MediaPipe handedness works correctly.
        """
        self.device_index = device_index
        self.requested_width = width
        self.requested_height = height
        self.target_fps = target_fps
        self.mirror = mirror

        self._cap: Optional[cv2.VideoCapture] = None
        self._frame_count: int = 0
        self._fps_start_time: float = 0.0
        self._current_fps: float = 0.0
        self._last_frame_time: float = 0.0

    @property
    def is_open(self) -> bool:
        """Whether the camera is currently open and available."""
        return self._cap is not None and self._cap.isOpened()

    @property
    def fps(self) -> float:
        """Measured frames per second (updated every second)."""
        return self._current_fps

    @property
    def actual_resolution(self) -> Tuple[int, int]:
        """Actual capture resolution (may differ from requested)."""
        if not self.is_open:
            return (0, 0)
        w = int(self._cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        h = int(self._cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        return (w, h)

    def open(self) -> bool:
        """
        Open the camera device.

        Returns:
            True if camera opened successfully, False otherwise.

        Why CAP_DSHOW on Windows:
            The default MSMF (Media Foundation) backend in OpenCV has known
            issues: slow initialization (2-5 second delay), frame drops with
            some USB cameras, and resolution negotiation failures. DirectShow
            (CAP_DSHOW) is older but more reliable across webcam models.
        """
        logger.info(
            f"Opening camera {self.device_index} "
            f"(requested: {self.requested_width}x{self.requested_height} @ {self.target_fps}fps)"
        )

        # Use DirectShow on Windows for reliability
        self._cap = cv2.VideoCapture(self.device_index, cv2.CAP_DSHOW)

        if not self._cap.isOpened():
            # Fallback: try without specifying backend
            logger.warning("DirectShow failed, trying default backend...")
            self._cap = cv2.VideoCapture(self.device_index)

        if not self._cap.isOpened():
            logger.error(
                f"Failed to open camera {self.device_index}. "
                "Check that your webcam is connected and not in use by another app."
            )
            return False

        # Configure capture properties
        self._cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.requested_width)
        self._cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.requested_height)
        self._cap.set(cv2.CAP_PROP_FPS, self.target_fps)

        # Reduce buffer size to minimize latency
        # Default buffer is often 4+ frames, meaning you see 100+ ms old frames.
        # Setting to 1 means you always get the latest frame.
        self._cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)

        actual_w, actual_h = self.actual_resolution
        actual_fps = self._cap.get(cv2.CAP_PROP_FPS)

        logger.info(
            f"Camera opened: {actual_w}x{actual_h} @ {actual_fps:.0f}fps "
            f"(buffer size: {int(self._cap.get(cv2.CAP_PROP_BUFFERSIZE))})"
        )

        if actual_w != self.requested_width or actual_h != self.requested_height:
            logger.warning(
                f"Camera resolution differs from requested: "
                f"got {actual_w}x{actual_h}, wanted {self.requested_width}x{self.requested_height}"
            )

        # Initialize FPS counter
        self._fps_start_time = time.perf_counter()
        self._frame_count = 0
        self._last_frame_time = time.perf_counter()

        return True

    def read(self) -> Optional[np.ndarray]:
        """
        Read a single frame from the camera.

        Returns:
            BGR frame as numpy array, or None if read failed.
            Frame is horizontally flipped if mirror=True.
        """
        if not self.is_open:
            return None

        success, frame = self._cap.read()

        if not success or frame is None:
            logger.warning("Failed to read frame from camera")
            return None

        # Mirror for natural webcam interaction
        if self.mirror:
            frame = cv2.flip(frame, 1)

        # Update FPS counter
        self._frame_count += 1
        now = time.perf_counter()
        elapsed = now - self._fps_start_time

        if elapsed >= 1.0:
            self._current_fps = self._frame_count / elapsed
            self._frame_count = 0
            self._fps_start_time = now
            logger.debug(f"Camera FPS: {self._current_fps:.1f}")

        self._last_frame_time = now
        return frame

    def release(self) -> None:
        """Release the camera device."""
        if self._cap is not None:
            self._cap.release()
            self._cap = None
            logger.info("Camera released")

    def __enter__(self) -> "Camera":
        if not self.open():
            raise RuntimeError(
                f"Could not open camera {self.device_index}. "
                "Ensure your webcam is connected and not used by another application."
            )
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.release()