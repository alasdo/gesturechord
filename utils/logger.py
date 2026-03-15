"""
Structured logging for GestureChord.

Why structured logging:
    When debugging gesture detection, you need to know frame-by-frame what
    happened: what landmarks were detected, what finger states were computed,
    what the filter outputs were, and what the state machine decided. Plain
    print() statements are unusable at 30 FPS. Structured logging with levels
    lets you:
    - Run normally with WARNING level (only errors and state transitions)
    - Debug with INFO level (gesture events, chord triggers)
    - Deep debug with DEBUG level (per-frame landmark data, filter states)

    Logs include timestamps for latency analysis.
"""

import logging
import sys
from typing import Optional


def setup_logger(
    name: str = "gesturechord",
    level: int = logging.INFO,
    log_file: Optional[str] = None,
) -> logging.Logger:
    """
    Create and configure a logger instance.

    Args:
        name: Logger name (used as prefix in log messages).
        level: Logging level. Use logging.DEBUG for frame-by-frame diagnostics,
            logging.INFO for gesture events, logging.WARNING for production.
        log_file: If provided, also write logs to this file (useful for
            post-session analysis of gesture accuracy).

    Returns:
        Configured logger instance.
    """
    logger = logging.getLogger(name)

    # Avoid duplicate handlers if called multiple times
    if logger.handlers:
        return logger

    logger.setLevel(level)

    # Format includes milliseconds — critical for latency debugging
    formatter = logging.Formatter(
        fmt="%(asctime)s.%(msecs)03d [%(levelname)s] %(name)s.%(module)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    # Console handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(level)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    # Optional file handler
    if log_file:
        file_handler = logging.FileHandler(log_file, mode="w")
        file_handler.setLevel(logging.DEBUG)  # File always gets full detail
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

    return logger