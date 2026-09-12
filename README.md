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
