"""
Configuration loader for GestureChord.

Reads config.yaml from the project directory. If the file doesn't exist,
generates it with default values. Provides typed access to all settings.

Usage:
    from utils.config import load_config
    cfg = load_config()

    print(cfg.music.key)          # "C"
    print(cfg.expression.cc)      # 1
    print(cfg.display.scale)      # 1.5
"""

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml


logger = logging.getLogger("gesturechord.utils.config")

CONFIG_FILENAME = "config.yaml"

# Default config as a Python dict (mirrors config.yaml structure)
DEFAULTS = {
    "camera": {"index": 0, "width": 640, "height": 480},
    "display": {"scale": 1.5, "window_name": "GestureChord v2", "start_in_debug": False},
    "tracking": {"max_hands": 2, "detection_confidence": 0.65, "tracking_confidence": 0.55},
    "gesture": {"hysteresis_high": 0.55, "hysteresis_low": 0.35, "rolling_window": 7},
    "state_machine": {
        "confirmation_frames": 5, "change_frames": 4,
        "settle_frames": 3, "release_grace_ms": 250,
    },
    "modifier": {"settle_frames": 4},
    "music": {"key": "C", "scale": "major", "octave": 4, "velocity": 100},
    "expression": {
        "cc_number": 1, "zone_top": 0.15, "zone_bottom": 0.65,
        "smoothing": 0.25, "dead_zone": 2.0, "enabled": True,
    },
    "expression2": {
        "cc_number": 74, "zone_left": 0.60, "zone_right": 0.35,
        "smoothing": 0.3, "dead_zone": 2.0, "enabled": False,
    },
    "midi": {"port_name": "GestureChord", "channel": 0},
    "zone": {"threshold": 0.75, "hand_lost_frames": 15},
    "velocity": {
        "enabled": True, "min_velocity": 50, "max_velocity": 120,
        "speed_low": 0.003, "speed_high": 0.04,
    },
    "arpeggiator": {
        "enabled": False, "bpm": 160.0, "pattern": "up", "octave_range": 1,
    },
    "rhythm": {
        "enabled": True, "velocity_threshold": 0.010, "cooldown_ms": 80.0,
        "smoothing": 0.5, "min_velocity": 45, "max_velocity": 120,
        "speed_for_max": 0.045,
    },
    "groove": {
        "enabled": False, "bpm": 120.0, "pattern": "four_floor",
        "gate_length": 0.85, "humanize_ms": 10.0,
    },
}


# ── Typed config dataclasses ──

@dataclass
class CameraConfig:
    index: int = 0
    width: int = 640
    height: int = 480

@dataclass
class DisplayConfig:
    scale: float = 1.5
    window_name: str = "GestureChord v2"
    start_in_debug: bool = False

@dataclass
class TrackingConfig:
    max_hands: int = 2
    detection_confidence: float = 0.65
    tracking_confidence: float = 0.55

@dataclass
class GestureConfig:
    hysteresis_high: float = 0.55
    hysteresis_low: float = 0.35
    rolling_window: int = 7

@dataclass
class StateMachineConfig:
    confirmation_frames: int = 5
    change_frames: int = 4
    settle_frames: int = 3
    release_grace_ms: int = 250

@dataclass
class ModifierConfig:
    settle_frames: int = 4

@dataclass
class MusicConfig:
    key: str = "C"
    scale: str = "major"
    octave: int = 4
    velocity: int = 100

@dataclass
class ExpressionConfig:
    cc_number: int = 1
    zone_top: float = 0.15
    zone_bottom: float = 0.65
    smoothing: float = 0.25
    dead_zone: float = 2.0
    enabled: bool = True

@dataclass
class Expression2Config:
    cc_number: int = 74
    zone_left: float = 0.60
    zone_right: float = 0.35
    smoothing: float = 0.3
    dead_zone: float = 2.0
    enabled: bool = False

@dataclass
class MidiConfig:
    port_name: str = "GestureChord"
    channel: int = 0

@dataclass
class ZoneConfig:
    threshold: float = 0.75
    hand_lost_frames: int = 15

@dataclass
class VelocityConfig:
    enabled: bool = True
    min_velocity: int = 50
    max_velocity: int = 120
    speed_low: float = 0.003
    speed_high: float = 0.04

@dataclass
class ArpeggiatorConfig:
    enabled: bool = False
    bpm: float = 160.0
    pattern: str = "up"
    octave_range: int = 1

@dataclass
class RhythmConfig:
    enabled: bool = True
    velocity_threshold: float = 0.010
    cooldown_ms: float = 80.0
    smoothing: float = 0.5
    min_velocity: int = 45
    max_velocity: int = 120
    speed_for_max: float = 0.045

@dataclass
class GrooveConfig:
    enabled: bool = False
    bpm: float = 120.0
    pattern: str = "four_floor"
    gate_length: float = 0.85
    humanize_ms: float = 10.0

@dataclass
class Config:
    """Top-level config with typed sections."""
    camera: CameraConfig = field(default_factory=CameraConfig)
    display: DisplayConfig = field(default_factory=DisplayConfig)
    tracking: TrackingConfig = field(default_factory=TrackingConfig)
    gesture: GestureConfig = field(default_factory=GestureConfig)
    state_machine: StateMachineConfig = field(default_factory=StateMachineConfig)
    modifier: ModifierConfig = field(default_factory=ModifierConfig)
    music: MusicConfig = field(default_factory=MusicConfig)
    expression: ExpressionConfig = field(default_factory=ExpressionConfig)
    expression2: Expression2Config = field(default_factory=Expression2Config)
    midi: MidiConfig = field(default_factory=MidiConfig)
    zone: ZoneConfig = field(default_factory=ZoneConfig)
    velocity: VelocityConfig = field(default_factory=VelocityConfig)
    arpeggiator: ArpeggiatorConfig = field(default_factory=ArpeggiatorConfig)
    rhythm: RhythmConfig = field(default_factory=RhythmConfig)
    groove: GrooveConfig = field(default_factory=GrooveConfig)


def _deep_merge(base: dict, override: dict) -> dict:
    """Merge override into base, preserving base values not in override."""
    result = base.copy()
    for key, val in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(val, dict):
            result[key] = _deep_merge(result[key], val)
        else:
            result[key] = val
    return result


def _dict_to_config(data: dict) -> Config:
    """Convert a nested dict to typed Config dataclass."""
    return Config(
        camera=CameraConfig(**data.get("camera", {})),
        display=DisplayConfig(**data.get("display", {})),
        tracking=TrackingConfig(**data.get("tracking", {})),
        gesture=GestureConfig(**data.get("gesture", {})),
        state_machine=StateMachineConfig(**data.get("state_machine", {})),
        modifier=ModifierConfig(**data.get("modifier", {})),
        music=MusicConfig(**data.get("music", {})),
        expression=ExpressionConfig(**data.get("expression", {})),
        expression2=Expression2Config(**data.get("expression2", {})),
        midi=MidiConfig(**data.get("midi", {})),
        zone=ZoneConfig(**data.get("zone", {})),
        velocity=VelocityConfig(**data.get("velocity", {})),
        arpeggiator=ArpeggiatorConfig(**data.get("arpeggiator", {})),
        rhythm=RhythmConfig(**data.get("rhythm", {})),
        groove=GrooveConfig(**data.get("groove", {})),
    )


def _generate_default_config(path: Path) -> None:
    """Write the default config.yaml with comments."""
    # Read the template from the package if available, otherwise write minimal
    template_path = Path(__file__).parent.parent / "config.yaml"
    if template_path.exists():
        # Copy the existing template (which has comments)
        import shutil
        shutil.copy2(template_path, path)
        logger.info(f"Config file copied to: {path}")
    else:
        # Generate from DEFAULTS dict
        with open(path, "w") as f:
            yaml.dump(DEFAULTS, f, default_flow_style=False, sort_keys=False)
        logger.info(f"Default config generated: {path}")


def load_config(config_path: Optional[str] = None) -> Config:
    """
    Load configuration from YAML file.

    Search order:
    1. Explicit config_path argument
    2. config.yaml in current working directory
    3. config.yaml next to this script's parent directory

    If not found, generates a default config.yaml in cwd.

    Returns:
        Typed Config object with all settings.
    """
    search_paths = []

    if config_path:
        search_paths.append(Path(config_path))

    search_paths.extend([
        Path.cwd() / CONFIG_FILENAME,
        Path(__file__).parent.parent / CONFIG_FILENAME,
    ])

    config_file = None
    for p in search_paths:
        if p.exists():
            config_file = p
            break

    if config_file is None:
        # Generate default
        config_file = Path.cwd() / CONFIG_FILENAME
        logger.info(f"No config found, generating default: {config_file}")
        _generate_default_config(config_file)

    # Load YAML
    logger.info(f"Loading config: {config_file}")
    try:
        with open(config_file, "r") as f:
            user_data = yaml.safe_load(f) or {}
    except Exception as e:
        logger.error(f"Failed to read config: {e}. Using defaults.")
        return _dict_to_config(DEFAULTS)

    # Merge user config with defaults (user values override, missing values get defaults)
    merged = _deep_merge(DEFAULTS, user_data)

    cfg = _dict_to_config(merged)

    logger.info(f"Config loaded: key={cfg.music.key} scale={cfg.music.scale} "
                f"octave={cfg.music.octave} display_scale={cfg.display.scale}")

    return cfg