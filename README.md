# GestureChord

**A two-hand gesture-to-MIDI chord controller for FL Studio.**

Turn your webcam into a musical instrument. Right hand selects chords, left hand shapes them and controls effects — all in real time with rhythmic expression.

## Quick Start

```bash
# 1. Clone and setup
git clone https://github.com/YOUR_USER/gesturechord.git
cd gesturechord
setup.bat                        # Windows: creates venv + installs deps

# 2. Install MIDI driver (one-time)
# Download loopBe1: https://www.nerds.de/en/loopbe1.html
# Reboot after installing

# 3. Configure FL Studio
# Options → MIDI Settings → enable loopBe input → load a synth plugin

# 4. Run
run.bat                          # Or: venv\Scripts\activate && python main.py
```

The hand tracking model (~12 MB) downloads automatically on first run.

---

## How It Works

### Right Hand — Chord Selection

Hold up fingers to play chords. Fist = silence.

| Fingers | Default (left 0) | SHIFT (left 4) | SHIFT+7 (left 5) |
|---------|-------------------|-----------------|-------------------|
| 1 | I | vi | vi7 |
| 2 | ii | vii° | vii°7 |
| 3 | iii | I (octave up) | Imaj7 (oct up) |
| 4 | IV | ii (octave up) | ii7 (oct up) |
| 5 | V | iii (octave up) | iii7 (oct up) |
| Fist | Silence | Silence | Silence |

All 7 scale degrees are accessible using the SHIFT modifier.

### Left Hand — Three Roles Simultaneously

**1. Finger count → chord modifier**

| Fingers | Modifier |
|---------|----------|
| 0 / absent | Basic triad |
| 1 | 7th chord |
| 2 | Suspended 4th |
| 3 | 9th chord |
| 4 | SHIFT (access vi, vii) |
| 5 | SHIFT + 7th |

**2. Hand height (Y) → CC1 expression** (toggle: E)

Move hand up/down to control any FL Studio parameter (filter, reverb, delay, etc.) via MIDI CC1.

**3. Hand horizontal (X) → CC2 expression** (toggle: W)

Move hand left/right to control a second parameter via MIDI CC74. Both CC channels work simultaneously.

### Rhythm System

Three rhythm modes (one active at a time):

**Pump retrigger (R)** — Pump your right hand up/down while holding a chord. Each downward pump retriggers the chord. Pump faster = faster rhythm. Pump harder = louder.

**Groove patterns (G)** — Automatic rhythmic patterns. You select chords, the groove handles timing. 8 patterns: four_floor, syncopated, trap, half_time, offbeat, waltz, shuffle, sparse.

**Arpeggiator (A)** — Plays chord notes one at a time in sequence. 4 patterns: up, down, up-down, random.

### Other Features

- **16 scales** — major, minor, all modes, pentatonic, blues, exotic
- **Inversions** — root, 1st, 2nd inversion (I key)
- **Dynamic velocity** — hand speed controls volume (V key)
- **Humanized voicing** — micro-stagger timing + velocity variation
- **Configurable** — all parameters in config.yaml
- **Low latency** — pipeline optimized for ~25-35ms response

---

## Keyboard Controls

### Core
| Key | Action |
|-----|--------|
| ESC / Q | Quit |
| SPACE | Panic — stop all MIDI notes |
| X | Full reset (all state + MIDI) |
| D | Toggle debug / performance overlay |
| T | Send test note (verify MIDI routing) |

### Music
| Key | Action |
|-----|--------|
| K | Cycle key root (C → C# → D → ...) |
| M | Toggle major / natural minor |
| S | Cycle through all 16 scales |
| + / - | Octave up / down |
| I | Cycle inversion (root → 1st → 2nd) |

### Expression
| Key | Action |
|-----|--------|
| E | Toggle CC1 (hand height → Mod Wheel) |
| W | Toggle CC2 (hand X position → CC74) |
| V | Toggle dynamic velocity |

### Rhythm
| Key | Action |
|-----|--------|
| R | Toggle pump retrigger |
| G | Toggle groove patterns |
| F | Cycle groove pattern |
| A | Toggle arpeggiator |
| P | Cycle arp pattern |
| [ / ] | BPM down / up |

---

## Setup

### Requirements
- Python 3.9+
- Webcam
- Windows (tested), macOS/Linux (should work with minor adjustments)
- FL Studio or any DAW that accepts MIDI input

### Installation

**Windows (recommended):**
```bash
cd gesturechord
setup.bat
```

**Manual:**
```bash
cd gesturechord
python -m venv venv
venv\Scripts\activate            # Windows
# source venv/bin/activate       # macOS/Linux
pip install -r requirements.txt
```

### MIDI Routing

**Windows:**
1. Install **loopBe1**: https://www.nerds.de/en/loopbe1.html
2. **Reboot** after installing (required for the driver to load)
3. In FL Studio: **Options → MIDI Settings** → enable the loopBe input port
4. Load any synth plugin (FL Keys for testing)

**macOS:**
Use the built-in IAC Driver (Audio MIDI Setup → IAC Driver → enable).

**Linux:**
Use JACK or ALSA virtual MIDI ports.

### Running
```bash
run.bat                          # Windows quick launcher
# Or:
venv\Scripts\activate
python main.py
```

---

## Linking Effects in FL Studio

### Method 1: Right-click (native plugins)
1. Right-click a knob in a native FL Studio plugin
2. Select "Link to controller..."
3. Move your left hand — FL Studio detects the CC
4. Click "Accept"

### Method 2: Ctrl+J Multilink (any plugin including third-party)
1. Press **Ctrl+J** in FL Studio (Multilink mode)
2. Wiggle the knob you want to control
3. Move your left hand
4. FL Studio links them automatically
5. Press Ctrl+J again to exit linking mode

This works with Splice, Serum, Vital, and any third-party plugin.

---

## Configuration

All settings live in `config.yaml`. Delete it to regenerate defaults.

### Key Settings

| Setting | What it does | Default |
|---------|-------------|---------|
| `music.key` | Root note | C |
| `music.scale` | Scale type | major |
| `music.octave` | Base octave (3-6) | 4 |
| `display.scale` | Window size multiplier | 1.5 |
| `expression.cc_number` | CC1 number | 1 (Mod Wheel) |
| `expression2.cc_number` | CC2 number | 74 (Cutoff) |
| `rhythm.velocity_threshold` | Pump sensitivity | 0.010 |
| `groove.bpm` | Groove tempo | 120 |
| `groove.gate_length` | Note sustain (0.5-1.0) | 0.85 |
| `groove.humanize_ms` | Timing variation | 10 |
| `arpeggiator.bpm` | Arp speed | 160 |

---

## Architecture

```
gesturechord/
├── main.py                      # Main loop — full pipeline
├── config.yaml                  # All settings (YAML)
├── setup.bat                    # Windows setup script
├── run.bat                      # Quick launcher
├── requirements.txt             # Python dependencies
├── vision/
│   ├── camera.py                # Webcam capture + buffer flush
│   ├── hand_tracker.py          # MediaPipe HandLandmarker (2-hand)
│   └── gesture_recognizer.py    # Y-position finger detection
├── engine/
│   ├── state_machine.py         # Settle-then-confirm debouncing
│   ├── music_theory.py          # 16 scales, chords, intervals
│   ├── chord_mapper.py          # Right degree + left modifier + shift
│   ├── expression.py            # Hand position → smoothed MIDI CC
│   ├── velocity.py              # Hand speed → MIDI velocity
│   ├── arpeggiator.py           # Sequential note playback
│   ├── rhythm_engine.py         # Pump retrigger detection
│   └── groove_patterns.py       # Automatic rhythm patterns
├── midi/
│   └── midi_output.py           # MIDI notes + CC + humanized voicing
├── ui/
│   └── overlay.py               # Animated performance/debug overlay
└── utils/
    ├── filters.py               # Hysteresis, rolling mode, EMA
    ├── config.py                # YAML config loader with typed dataclasses
    └── logger.py                # Structured logging
```

### Pipeline (per frame)
```
Camera → MediaPipe → Gesture Recognition → State Machine → MIDI Output → Overlay
  ~2ms     ~18ms          ~1ms                ~1ms           ~0ms         ~4ms
```

MIDI fires BEFORE overlay rendering for minimum latency.

---

## Troubleshooting

**No sound from FL Studio:**
- Press **T** to send a test note — if you hear it, MIDI is working
- Check FL Studio: Options → MIDI Settings → loopBe must be enabled as Input
- Make sure a synth plugin is loaded and selected on a channel
- If you just installed loopBe1, you MUST reboot

**Finger detection inaccurate:**
- Press **D** for debug mode — check per-finger ratios
- Face your palm toward the camera
- Improve lighting (avoid backlight)
- Keep hands within the zone (above the zone line)

**Chords cascade (1→2→3 when going to 3):**
- This is handled by settle-then-confirm debouncing
- If still happening, increase `state_machine.settle_frames` in config.yaml

**Expression CC jittery:**
- Lower `expression.smoothing` (e.g., 0.15) for more smoothing
- Increase `expression.dead_zone` (e.g., 3.0)

**Third-party plugin CC not linking:**
- Use **Ctrl+J** (Multilink) instead of right-click "Link to controller"

**FPS too low:**
- Close other apps using the webcam
- Lower `tracking.detection_confidence` to 0.5 in config.yaml
- Press **D** to check timing breakdown

**Hands swapped (left/right confused):**
- Keep hands on their respective sides of the frame
- The system uses X-position as a tiebreaker

---

## License

MIT