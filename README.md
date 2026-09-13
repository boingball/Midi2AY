# Midi2AY

A small dependency-free MIDI-to-ZX Spectrum 128 AY BASIC converter.

It reads a Standard MIDI file, quantises note timing, reduces polyphonic material into three AY roles, and emits Sinclair BASIC strings for:

```
PLAY A$,B$,C$
```

## Usage

```
python3 midi2ay.py "song.mid" song_ay.bas --title "Song title" --lead-mode smart
```

Lead modes are:

- `smart`: follows a continuous melodic voice through merged piano chords and fills all AY voices while MIDI notes are active.
- `top`: uses the highest active note.
- `middle`: uses the middle active note.

The output uses Spectrum 128 PLAY syntax, including tempo, octave, duration separators, rests and tied durations. It is designed for a ZX Spectrum 128 emulator or real 128K hardware.

## Scope

This is an experimental converter, not a General MIDI player. It currently supports three AY tone voices, simple tempo handling and BASIC output. Future work can add separate MIDI-track routing, volume envelopes, noise, TAP/TZX packaging and a machine-code AY player.

No copyrighted MIDI files or song-specific generated output are included in this repository. Use input files you are licensed to use, and keep generated song files outside the public repository unless you have permission to redistribute them.

## Enhanced AY output

The optional machine-code path preserves MIDI channel/program information and writes 50 Hz AY register frames for `ay_player.asm`. MIDI channel 10 can be rendered through the AY noise generator:

```
python3 midi2ay.py song.mid song.ay --mode ay --drums noise
python3 midi2ay.py song.mid song.ay --mode ay --drums hybrid
```

`off` ignores percussion, `noise` uses AY noise, and `hybrid` adds a short tone attack for kick-like notes. The current player consumes one 14-register frame per tick; assemble it with a Z80 assembler and call `ay_tick` at 50 Hz.

## Spectrum screens

Convert PNG or JPG artwork into a native 256x192, 6912-byte Spectrum screen:

```
python3 -m pip install Pillow
python3 spectrum_screen.py cover.png cover.scr
```

## TAP machine-code output

The machine-code path can package compressed AY register events into a self-starting TAP. It contains a BASIC loader followed by a Z80 player and the music data:

```
python3 midi2ay.py song.mid song.tap --mode tap --drums noise
```

The resulting TAP loads at 32768, waits on the Spectrum's 50 Hz interrupt and drives the AY directly. The event stream stores only changed AY registers, keeping longer songs practical on a 128K machine.

### Artwork before playback

Pass `--image` with a PNG or JPG to show cover art before the music starts:

```
python3 midi2ay.py song.mid song.tap --mode tap --drums noise --image cover.jpg
```

The image is converted to a native Spectrum screen (via `spectrum_screen.py`) and loaded first with `LOAD "name" SCREEN$`, which streams the picture into the display file live as it loads. A `PAUSE 0` then waits for any keypress before the second `LOAD` brings in the player and starts the music.

### Scope analyser

The player draws a live 3-channel oscilloscope-style trace in a thin strip across the bottom of the screen (the last 3 character rows), overlaid on the artwork. Each channel's trace reacts to its own AY volume (vertical position within its band) and tone period (animation speed) every tick. This is a stylised, reactive visualisation rather than a sample-accurate waveform - simulating the real ~1.77MHz AY output sample-by-sample inside a 50Hz interrupt isn't possible on a 3.5MHz Z80. Measured worst-case cost is well under half of the available 50Hz interrupt budget, alongside the register player and the ROM's own interrupt overhead.
