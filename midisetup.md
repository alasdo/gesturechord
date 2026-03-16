# MIDI Setup Guide

## Windows — loopBe1 (recommended)

### Step 1: Install loopBe1
1. Download from https://www.nerds.de/en/loopbe1.html
2. Run the installer
3. **Reboot your PC** (the virtual MIDI driver requires a restart)

### Step 2: Verify loopBe1 is running
After reboot, you should see a loopBe1 icon in your system tray (bottom-right).
Right-click it — make sure "Mute" is NOT checked.

### Step 3: Configure FL Studio
1. Open FL Studio
2. Go to **Options → MIDI Settings**
3. In the **Input** section, find "loopBe Internal MIDI"
4. Click on it to select it
5. Set **Port** to 0 (or leave as is)
6. Make sure the **Enable** light is on (green)
7. Close the settings

### Step 4: Load a synth
1. In the Channel Rack, click **+** → add any synth plugin
2. FL Keys, Sytrus, or FLEX work well for testing
3. The plugin should now receive MIDI from GestureChord

### Step 5: Test
1. Run GestureChord: `python main.py`
2. Press **T** to send a test note
3. You should hear the synth play a note
4. If no sound: check Steps 3-4 again

## Alternative: loopMIDI

If loopBe1 doesn't work, try loopMIDI:
1. Download from https://www.tobias-erichsen.de/software/loopmidi.html
2. Create a port named "GestureChord"
3. Follow Steps 3-5 above, selecting the loopMIDI port instead

**Note:** loopMIDI requires driver signing which may fail on some Windows systems. loopBe1 is generally more reliable.

## macOS

Use the built-in IAC Driver:
1. Open **Audio MIDI Setup** (in Applications → Utilities)
2. Show MIDI Studio (Window menu)
3. Double-click **IAC Driver**
4. Check "Device is online"
5. In your DAW, select IAC Driver as MIDI input

## Linux

Use ALSA virtual MIDI or JACK:
```bash
sudo modprobe snd-virmidi
# Or use JACK with a2jmidid for ALSA-JACK bridge
```

## Linking Effects

### Native FL Studio plugins
Right-click any knob → "Link to controller..." → move your hand → Accept

### Third-party plugins (Splice, Serum, Vital, etc.)
1. Press **Ctrl+J** in FL Studio (Multilink mode)
2. Wiggle the target knob in the plugin
3. Move your left hand (height for CC1, horizontal for CC2)
4. FL Studio auto-links them
5. Press Ctrl+J again to exit

### Two simultaneous effects
- **E** key enables CC1 (hand height → CC1/Mod Wheel)
- **W** key enables CC2 (hand horizontal → CC74/Cutoff)
- Link each to different plugin knobs using Ctrl+J