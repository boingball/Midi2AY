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

This is an experimental converter, not a General MIDI player. It supports three AY tone voices, MIDI velocity/program tracking, smart lead routing, channel-10 percussion, machine-code frame output and self-starting TAP packaging.

No copyrighted MIDI files or song-specific generated output are included in this repository. Use input files you are licensed to use, and keep generated song files outside the public repository unless you have permission to redistribute them.

## Enhanced AY output

The optional machine-code path preserves MIDI channel/program information and writes 50 Hz AY register frames for `ay_player.asm`. Enhanced output includes independent frame-level AY patches for bass, bell, brass, lead, organ, pad, pluck, strings and general tones. MIDI channel 10 can be rendered through a mapped AY drum kit:

```
python3 midi2ay.py song.mid song.ay --mode ay --drums noise
python3 midi2ay.py song.mid song.ay --mode ay --drums hybrid --lead-mode smart
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

The image is converted to a native Spectrum screen via `spectrum_screen.py`. The fixed player loads first, followed by the artwork. For banked songs, the loader saves a pristine copy of the 6,912-byte screen in RAM bank 0; after the remaining tape blocks have loaded, it restores the picture from RAM immediately before playback. This removes the ROM’s `Bytes:` messages without storing the artwork twice on tape.

### Scope analyser

The player draws a live 3-channel oscilloscope-style trace in a thin strip across the bottom of the screen (the last 3 character rows), overlaid on the artwork. Each channel's trace reacts to its own AY volume (vertical position within its band) and tone period (animation speed) every tick. This is a stylised, reactive visualisation rather than a sample-accurate waveform - simulating the real ~1.77MHz AY output sample-by-sample inside a 50Hz interrupt isn't possible on a 3.5MHz Z80. Measured worst-case cost is well under half of the available 50Hz interrupt budget, alongside the register player and the ROM's own interrupt overhead.


## Smart enhanced-AY routing

Enhanced AY and TAP modes default to smart voice routing. The converter follows a likely lead line by pitch continuity between frames, keeps bass at the low end, and uses the remaining voice for accompaniment. This is intended for merged piano MIDI files where the melody is not stored on its own track.

For comparison, the routing can be selected explicitly:

```
python3 midi2ay.py song.mid song.ay --mode ay --lead-mode smart
python3 midi2ay.py song.mid song.ay --mode ay --lead-mode top
python3 midi2ay.py song.mid song.ay --mode ay --lead-mode middle
```

MIDI channel 10 is treated as percussion when drums are enabled. The drum mapper recognises kick, snare, closed/open hi-hat, tom, crash and ride notes. `noise` uses the AY noise generator and `hybrid` adds pitched tone attacks for kick and tom hits. Instrument patches use frame-level attack, sustain and release shaping because the AY has one shared hardware envelope. The BASIC mode remains unchanged and continues to use editable `PLAY A$,B$,C$` output.


## Instrument patches

PR5 applies small AY-friendly envelopes to enhanced tone voices. General MIDI program families are mapped to bass, bell, organ, pluck, strings, brass, lead, pad and effects-style patches. The patches do not pretend to create independent analogue waveforms: the AY provides three square-wave tone generators, one shared noise generator and one shared envelope, so the converter shapes each voice with register-volume changes at 50 Hz.

For drum-enabled output, channel 10 is mapped by MIDI percussion key:

| MIDI key | Sound | AY treatment |
| ---: | --- | --- |
| 35-36 | Kick | Low noise burst; hybrid adds a low tone |
| 38, 40 | Snare | Shorter, brighter noise burst |
| 42, 44, 46 | Closed/open hat | High-frequency noise with different decay |
| 41, 43, 45, 47, 48, 50 | Toms | Tuned noise; hybrid adds a tone |
| 49, 52, 55, 57 | Crash | Bright, longer noise |
| 51, 53, 59 | Ride | Bright sustained noise |

Example:

```
python3 midi2ay.py song.mid song.tap --mode tap --lead-mode smart --drums hybrid
```


## PR6 bank-switched TAP storage

Long TAP songs automatically split their compressed AY event stream between the fixed player area and the safe pageable 16K RAM banks `1, 3, 4, 6, 7` on a 128K Spectrum. Banks 2 and 5 are excluded because they are permanently mapped at `0x8000` and `0x4000`; loading music through those aliases would overwrite the player or display.

The BASIC loader uses `CLEAR 30000`, pages each extra bank before loading it, and the machine-code player follows transition markers during playback. RAM bank 0 temporarily holds a clean artwork copy while those blocks load. Tape headers use the MIDI filename (or `--title`), uppercased and trimmed to the Spectrum’s 10-character limit; direct `build_tap.py` use defaults to the output filename. This keeps drum-enabled output practical for longer songs while preserving artwork and the fixed player.

Example:

```
python3 midi2ay.py "No Surprises.mid" no_surprises.tap --mode tap --drums hybrid --lead-mode smart --image cover.jpg
```
