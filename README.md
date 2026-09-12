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
