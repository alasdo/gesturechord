# GestureChord

**Hand-gesture-to-MIDI chord controller for FL Studio.**

Turn your webcam into a harmonic performance instrument. Hold up fingers to play
diatonic chords in any key — think in Roman numerals (I, ii, iii, IV, V), perform
with your hands.

---

## Status: Phase 1 — Vision Pipeline

Phase 1 proves the core vision pipeline: webcam capture → hand tracking → finger
counting → visual overlay. No MIDI output yet — this phase validates that gesture
detection is fast and reliable enough to build a musical instrument on top of.

---

## Setup (Windows)

### Prerequisites

- Python 3.9 or newer (3.10+ recommended)
- A webcam (built-in laptop camera works fine)
- pip (comes with Python)

### Installation

```bash
# Clone or download this project, then:
cd gesturechord

# Create a virtual environment (recommended)
python -m venv venv
venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt
```

### Running Phase 1

```bash
python main.py
```

A window will open showing your webcam feed with hand tracking overlay.

### Controls

| Key   | Action                              |
|-------|-------------------------------------|
| ESC/Q | Quit                                |
| D     | Toggle debug overlay (finger ratios)|
| R     | Reset gesture filters               |

---

## What You Should See

1. **Hand skeleton** drawn over your hand in cyan
2. **Fingertip dots**: green = extended, red = curled
3. **Finger count** (large number, top-left) — this is the filtered count
4. **Stability bar** — green when detection is stable
5. **Debug panel** (bottom-left) — per-finger extension ratios with mini bar charts
6. **FPS and inference time** (bottom-right)

---

## Phase 1 Testing Guide

Test these scenarios and note any problems:

### Basic Detection
- [ ] Hold up 1 finger (index) — does it show 1?
- [ ] Hold up 2 fingers (index + middle) — does it show 2?
- [ ] Hold up 3, 4, 5 fingers — correct counts?
- [ ] Make a fist — does it show 0?
- [ ] Thumb only — does it show 1? (thumb is hardest to detect)

### Stability
- [ ] Hold a gesture steady for 3 seconds — does the count stay stable?
- [ ] Transition between gestures — how quickly does the count update?
- [ ] Is there flickering when you hold a borderline gesture?

### Robustness
- [ ] Move your hand slowly across the frame — does tracking follow?
- [ ] Move your hand quickly — does it lose tracking? How fast does it recover?
- [ ] Try different distances from camera (near, mid, far)
- [ ] Try different lighting (bright, dim, backlit)
- [ ] Try with background clutter behind your hand

### Performance
- [ ] What FPS are you getting? (should be 25+ for usable latency)
- [ ] What inference time? (should be under 30ms)
- [ ] Is there visible lag between your hand movement and the overlay?

### Edge Cases
- [ ] What happens when you remove your hand from frame?
- [ ] What happens when you put it back?
- [ ] What happens with two hands? (should track the right hand)
- [ ] What happens if you cover part of your hand with the other?

---

## Architecture

```
gesturechord/
├── main.py                     # Entry point, main loop
├── config.yaml                 # (Phase 5) All configurable parameters
├── requirements.txt
├── README.md
│
├── vision/
│   ├── camera.py               # Webcam capture, FPS measurement
│   ├── hand_tracker.py         # MediaPipe wrapper → HandData
│   └── gesture_recognizer.py   # Landmark analysis → GestureResult
│
├── engine/                     # (Phase 2-3) State machine, music theory
│   ├── state_machine.py
│   ├── music_theory.py
│   └── chord_mapper.py
│
├── midi/                       # (Phase 4) MIDI output to FL Studio
│   └── midi_output.py
│
├── ui/
│   └── overlay.py              # OpenCV visual feedback
│
└── utils/
    ├── filters.py              # Hysteresis, rolling mode filters
    └── logger.py               # Structured logging
```

**Key design principle:** Each module has a single responsibility and clean
interfaces. Vision knows nothing about music. Engine knows nothing about cameras.
MIDI knows nothing about gestures. You can swap any component independently.

---

## Roadmap

| Phase | Focus                    | Status  |
|-------|--------------------------|---------|
| 1     | Vision pipeline          | ← HERE  |
| 2     | State machine + debounce | Next    |
| 3     | Music theory engine      | —       |
| 4     | MIDI output + FL Studio  | —       |
| 5     | Config + polish          | —       |
| 6     | Expressive controls      | —       |

---

## Troubleshooting

**"Could not download hand landmarker model":**
- The system auto-downloads a ~12 MB model file on first run
- If download fails (firewall, proxy, etc.), download manually from:
  https://storage.googleapis.com/mediapipe-models/hand_landmarker/hand_landmarker/float16/1/hand_landmarker.task
- Place the file `hand_landmarker.task` in your project directory (same folder as `main.py`)

**Camera won't open:**
- Check that no other app is using your webcam (Zoom, Discord, OBS, etc.)
- Try changing `CAMERA_INDEX` in `main.py` to 1 or 2 if you have multiple cameras

**Low FPS (<20):**
- Close other heavy applications
- Ensure your laptop is plugged in (battery mode may throttle CPU)

**Finger count is wrong:**
- Press D to see per-finger extension ratios
- Check if the problematic finger's ratio is hovering near the threshold
- Try adjusting `HYSTERESIS_HIGH` and `HYSTERESIS_LOW` in `main.py`
- Ensure your palm faces the camera, not sideways

**Tracking keeps dropping:**
- Improve lighting (face a window or desk lamp)
- Reduce background clutter
- Keep your hand within the center 80% of the frame
- Try lowering `DETECTION_CONFIDENCE` to 0.5