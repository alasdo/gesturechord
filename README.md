# GestureChord

**A two-hand gesture-to-MIDI chord controller for FL Studio.**

Use your webcam as a musical instrument. Your right hand selects chords by holding up fingers, your left hand modifies chord quality and controls effects in real time.

---

## Features

### Right Hand — Chord Selection
| Fingers | Chord |
|---------|-------|
| 1 | I (tonic) |
| 2 | ii |
| 3 | iii |
| 4 | IV |
| 5 | V |
| Fist | Silence |

### Left Hand — Chord Modifiers
| Fingers | Modifier | Right 1-5 maps to |
|---------|----------|-------------------|
| 0 / absent | Basic triad | I, ii, iii, IV, V |
| 1 | 7th chord | I7, ii7, iii7, IV7, V7 |
| 2 | Suspended 4th | Isus4, iisus4, etc. |
| 3 | 9th chord | Imaj9, iim9, etc. |
| 4 | **SHIFT** | vi, vii°, I+, ii+, iii+ |
| 5 | **SHIFT + 7th** | vi7, vii°7, I+7, ii+7, iii+7 |

SHIFT mode remaps the right hand to upper degrees, giving you all 7 scale degrees. Right 3-5 in shift mode play degrees I-III one octave higher.

### Available Scales
Press **S** to cycle through scales, **M** to toggle major/minor:

major, natural minor, harmonic minor, melodic minor, dorian, phrygian, lydian, mixolydian, locrian, pentatonic major, pentatonic minor, blues, phrygian dominant, hungarian minor, whole tone, chromatic

### Inversions (Keyboard Toggle)
Press **I** to cycle through voicings:

| Press I | Voicing | Example (C major) |
|---------|---------|-------------------|
| 0 (default) | Root position | C E G |
| 1 | 1st inversion | E G C |
| 2 | 2nd inversion | G C E |

Inversions apply to all chords until you press I again. Useful for smooth voice leading between chord changes.

### Left Hand — Expression Control
Your left hand's height in the frame sends continuous MIDI CC data:
- **Hand high** = CC 127 (maximum)
- **Hand low** = CC 0 (minimum)
- Smoothed to eliminate jitter
- Map to any FL Studio parameter (filter, reverb, delay, etc.)

### Dynamic Velocity
Hand movement speed controls how loud chords play:
- **Fast gesture** = high velocity (loud, punchy)
- **Slow/still gesture** = low velocity (soft, subtle)
- Toggle with **V** key

### Arpeggiator
Instead of block chords, play notes one at a time in sequence:
- Toggle with **A** key
- **P** = cycle pattern (up, down, up-down, random)
- **[** / **]** = decrease / increase BPM
- Configurable in config.yaml (bpm, pattern, octave range)

### Other Features
- Performance zone — hand must be in upper 75% of frame to trigger
- Settle-then-confirm debouncing — prevents cascade triggers (1→2→3)
- Fist = instant silence (no confirmation delay)
- Smart left/right hand identification using position + labels
- Independent gesture filters per hand
- Performance mode / debug mode overlay (D to toggle)

---

## Setup

### Requirements
- Python 3.9+
- Webcam
- Windows (tested), macOS/Linux (should work)
- FL Studio (or any DAW that accepts MIDI input)

### Installation
```bash
cd gesturechord
python -m venv venv
venv\Scripts\activate          # Windows
# source venv/bin/activate     # macOS/Linux

pip install -r requirements.txt
```

### MIDI Routing (Windows)
1. Install **loopBe1**: https://www.nerds.de/en/loopbe1.html
   (or loopMIDI: https://www.tobias-erichsen.de/software/loopmidi.html)
2. Reboot after installation
3. In FL Studio: **Options → MIDI Settings** → enable the loopBe/loopMIDI port in Input
4. Load any synth plugin (FL Keys for testing)

### Running
```bash
python main.py
```

The model file (`hand_landmarker.task`, ~12 MB) downloads automatically on first run.

---

## Keyboard Controls

| Key | Action |
|-----|--------|
| ESC / Q | Quit |
| SPACE | Panic — stop all MIDI notes |
| K | Cycle key root (C → C# → D → ...) |
| M | Toggle major / natural minor |
| S | Cycle through all scales |
| UP / DOWN | Octave up / down |
| D | Toggle debug overlay |
| E | Toggle expression CC on/off |
| V | Toggle dynamic velocity on/off |
| A | Toggle arpeggiator on/off |
| P | Cycle arp pattern (up/down/up-down/random) |
| [ / ] | Arp BPM down / up (±20) |
| I | Cycle inversion (root → 1st → 2nd → root) |
| T | Send test note (verify MIDI routing) |
| R | Full reset (filters + state + MIDI) |

---

## Linking Expression to FL Studio Effects

1. Run GestureChord and raise your left hand in the frame
2. In FL Studio, right-click any knob or slider in a plugin
3. Select **"Link to controller..."**
4. Move your left hand up and down — FL Studio auto-detects the CC
5. Click **"Accept"**

Now that parameter follows your hand height. Works with any plugin parameter: filter cutoff, reverb wet, delay feedback, volume, etc.

---

## Architecture

```
gesturechord/
├── main.py                      # Main loop, two-hand pipeline
├── config.yaml                  # All settings (edit to customize)
├── vision/
│   ├── camera.py                # Webcam capture
│   ├── hand_tracker.py          # MediaPipe HandLandmarker (2-hand)
│   └── gesture_recognizer.py    # Y-position finger detection
├── engine/
│   ├── state_machine.py         # Settle-then-confirm debouncing
│   ├── music_theory.py          # Scales, chords, intervals
│   ├── chord_mapper.py          # Right degree + left modifier → chord
│   └── expression.py            # Hand Y → smoothed MIDI CC
├── midi/
│   └── midi_output.py           # MIDI notes + CC output
├── ui/
│   └── overlay.py               # Visual feedback overlay
└── utils/
    ├── filters.py               # Hysteresis, rolling mode, EMA
    ├── config.py                # YAML config loader
    └── logger.py                # Structured logging
```

---

## Configuration

All settings live in `config.yaml`. Edit it to customize your setup — changes take effect on next launch. Delete the file to regenerate defaults.

Key settings you might want to change:

| Setting | What it does | Default |
|---------|-------------|---------|
| `music.key` | Root note | C |
| `music.scale` | Scale type | major |
| `music.octave` | Base octave | 4 |
| `display.scale` | Window size multiplier | 1.5 |
| `expression.cc_number` | Which CC to send | 1 (Mod Wheel) |
| `expression.smoothing` | CC smoothness (lower = smoother) | 0.25 |
| `state_machine.settle_frames` | Anti-cascade sensitivity | 3 |
| `gesture.hysteresis_high` | Finger "up" threshold | 0.55 |
| `zone.threshold` | Performance zone cutoff | 0.75 |

---

## Troubleshooting

**No MIDI sound:**
- Press T to send a test note — if you hear it, MIDI routing works
- Check FL Studio MIDI Settings → Input → loopBe must be enabled
- Make sure a synth plugin is loaded on a channel

**Finger detection wrong:**
- Press D to see per-finger ratios in the debug panel
- Ensure palm faces the camera
- Improve lighting

**Chords cascade (1→2→3 when going to 3):**
- The settle-then-confirm system handles this — increase SETTLE_FRAMES in main.py if needed

**Expression CC jittery:**
- Lower EXPRESSION_SMOOTHING (e.g., 0.15) for more smoothing
- Increase EXPRESSION_DEAD_ZONE (e.g., 3.0) for less CC spam

**FPS too low:**
- Close other apps
- Lower DETECTION_CONFIDENCE to 0.5

**Hands misidentified (left/right swapped):**
- Keep hands on their respective sides of the frame
- The system uses X-position as a tiebreaker when labels conflict