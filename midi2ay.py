#!/usr/bin/env python3
"""Small standard-library MIDI to ZX Spectrum 128 AY BASIC converter.

This is deliberately dependency-free so the first prototype is easy to run and
audit.  It reads format 0/1 MIDI files, reduces polyphony into three AY roles,
and emits BASIC strings suitable for PLAY A$,B$,C$.
"""

from __future__ import annotations

import argparse
import math
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Note:
    start: int
    end: int
    pitch: int
    velocity: int


def read_vlq(data: bytes, pos: int) -> tuple[int, int]:
    value = 0
    while True:
        byte = data[pos]
        pos += 1
        value = (value << 7) | (byte & 0x7F)
        if byte < 0x80:
            return value, pos


def parse_midi(path: Path) -> tuple[int, list[Note], list[int]]:
    data = path.read_bytes()
    if data[:4] != b"MThd":
        raise ValueError("not a Standard MIDI file")
    header_length = int.from_bytes(data[4:8], "big")
    fmt = int.from_bytes(data[8:10], "big")
    tracks = int.from_bytes(data[10:12], "big")
    division = int.from_bytes(data[12:14], "big")
    if division & 0x8000:
        raise ValueError("SMPTE MIDI timing is not supported in this prototype")

    pos = 8 + header_length
    notes: list[Note] = []
    tempos: list[int] = []

    for _ in range(tracks):
        if data[pos:pos + 4] != b"MTrk":
            raise ValueError("invalid MIDI track header")
        length = int.from_bytes(data[pos + 4:pos + 8], "big")
        track = data[pos + 8:pos + 8 + length]
        pos += 8 + length
        cursor = 0
        tick = 0
        running_status = None
        active: dict[tuple[int, int], list[tuple[int, int]]] = defaultdict(list)

        while cursor < len(track):
            delta, cursor = read_vlq(track, cursor)
            tick += delta
            status = track[cursor]
            if status < 0x80:
                if running_status is None:
                    raise ValueError("running status without a previous event")
                status = running_status
            else:
                cursor += 1
                running_status = status

            event_type = status & 0xF0
            channel = status & 0x0F

            if status == 0xFF:
                meta_type = track[cursor]
                cursor += 1
                size, cursor = read_vlq(track, cursor)
                payload = track[cursor:cursor + size]
                cursor += size
                if meta_type == 0x51 and len(payload) == 3:
                    tempos.append(int.from_bytes(payload, "big"))
                continue
            if status in (0xF0, 0xF7):
                size, cursor = read_vlq(track, cursor)
                cursor += size
                continue

            length = 1 if event_type in (0xC0, 0xD0) else 2
            payload = track[cursor:cursor + length]
            cursor += length
            if event_type == 0x90 and payload[1]:
                active[(channel, payload[0])].append((tick, payload[1]))
            elif event_type in (0x80, 0x90):
                key = (channel, payload[0])
                if active[key]:
                    start, velocity = active[key].pop(0)
                    if tick > start:
                        notes.append(Note(start, tick, payload[0], velocity))

    if fmt not in (0, 1):
        raise ValueError(f"MIDI format {fmt} is not supported")
    if not notes:
        raise ValueError("MIDI contains no closed note events")
    return division, notes, tempos


NOTE_NAMES = ("C", "#C", "D", "#D", "E", "F", "#F", "G", "#G", "A", "#A", "B")


def spectrum_pitch(pitch: int) -> tuple[int, str]:
    """Return an O command and note name for a MIDI pitch."""
    octave = pitch // 12 - 1
    if not 0 <= octave <= 8:
        raise ValueError(f"MIDI pitch {pitch} is outside Spectrum PLAY range")
    return octave, NOTE_NAMES[pitch % 12].lower()


def duration_codes(units: int) -> list[int]:
    """Represent sixteenth-note units using Spectrum PLAY duration codes."""
    # 1, 3, 4, 5, 6, 7, 8 and 9 represent 1/16 through a whole note,
    # including the dotted values.  Greedy splitting keeps output compact.
    values = ((16, 9), (12, 8), (8, 7), (6, 6), (4, 5), (3, 4), (2, 3), (1, 1))
    result: list[int] = []
    remaining = max(1, units)
    for length, code in values:
        while remaining >= length:
            result.append(code)
            remaining -= length
    return result


def _quantised_note_cells(notes: list[Note], division: int) -> list[list[Note]]:
    grid = max(1, division // 4)  # sixteenth notes
    end_tick = max(note.end for note in notes)
    steps = math.ceil((end_tick + grid) / grid)

    # Rasterise each note after quantising its own boundaries.  Sampling only
    # at grid points loses short notes whose start and end fall between two
    # samples (the original prototype's main source of missing melody notes).
    active_by_step: list[list[Note]] = [[] for _ in range(steps)]
    for note in notes:
        start_step = max(0, round(note.start / grid))
        end_step = max(start_step + 1, round(note.end / grid))
        for step in range(start_step, min(end_step, steps)):
            active_by_step[step].append(note)
    return active_by_step


def _smart_lead(active_by_step: list[list[Note]]) -> list[int | None]:
    """Follow a likely melody voice through a merged polyphonic MIDI part.

    A piano reduction often puts melody, harmony and bass into one MIDI track.
    The old top-note rule follows the upper chord note, which is commonly an
    arpeggio.  Starting at the middle of the opening chord and then choosing
    the closest continuing voice gives a much better generic lead line.
    """
    result: list[int | None] = []
    previous: int | None = None
    for active_notes in active_by_step:
        pitches = sorted(set(note.pitch for note in active_notes))
        if not pitches:
            result.append(None)
            continue

        if previous in pitches:
            selected = previous
        elif previous is None:
            # Avoid choosing the bass as the opening lead.  The median of the
            # remaining chord is a useful neutral starting voice.
            lead_candidates = [pitch for pitch in pitches if pitch >= 55] or pitches
            selected = lead_candidates[(len(lead_candidates) - 1) // 2]
        else:
            # Favour a stepwise voice, with a small preference for the more
            # expressive/longer note when distances are almost identical.
            selected = max(
                pitches,
                key=lambda pitch: (
                    -abs(pitch - previous),
                    max(note.velocity for note in active_notes if note.pitch == pitch),
                    max(note.end - note.start for note in active_notes if note.pitch == pitch),
                    pitch,
                ),
            )
        result.append(selected)
        previous = selected
    return result


def quantised_roles(notes: list[Note], division: int, lead_mode: str = "smart") -> dict[str, list[int | None]]:
    active_by_step = _quantised_note_cells(notes, division)
    if lead_mode == "smart":
        lead_sequence = _smart_lead(active_by_step)
    elif lead_mode == "top":
        lead_sequence = [max((note.pitch for note in active), default=None) for active in active_by_step]
    elif lead_mode == "middle":
        lead_sequence = [
            (sorted(set(note.pitch for note in active))[len(set(note.pitch for note in active)) // 2] if active else None)
            for active in active_by_step
        ]
    else:
        raise ValueError("lead mode must be smart, top or middle")

    roles: dict[str, list[int | None]] = {"accompaniment": [], "lead": [], "bass": []}
    for active_notes, lead in zip(active_by_step, lead_sequence):
        active = sorted(set(note.pitch for note in active_notes))
        if not active:
            roles["accompaniment"].append(None)
            roles["lead"].append(None)
            roles["bass"].append(None)
        else:
            roles["bass"].append(active[0])
            # A source note window must never disappear because the selected
            # voice could not be resolved.  This is especially important when
            # a merged piano MIDI has only one or two notes in a cell.
            lead = lead if lead is not None else active[-1]
            roles["lead"].append(lead)
            remaining = [pitch for pitch in active if pitch not in {active[0], lead}]
            # Keep all three AY voices occupied while the source is active.
            # Reusing a pitch is preferable to creating a silent hole; real
            # rests are still preserved when the MIDI has no active notes.
            roles["accompaniment"].append(max(remaining) if remaining else lead)
    return roles


def render_role(sequence: list[int | None]) -> str:
    out: list[str] = []
    pos = 0
    previous_octave = None
    while pos < len(sequence):
        pitch = sequence[pos]
        run = 1
        while pos + run < len(sequence) and sequence[pos + run] == pitch:
            run += 1

        codes = duration_codes(run)
        if pitch is None:
            for code in codes:
                out.append(f"N{code}&")
        else:
            octave, note_name = spectrum_pitch(pitch)
            if octave != previous_octave:
                out.append(f"O{octave}")
                previous_octave = octave
            # A tied chain holds a selected pitch instead of retriggering it.
            out.append("N" + "_".join(str(code) for code in codes) + note_name)
        pos += run
    return "".join(out)


def wrap_basic(strings: dict[str, str], title: str, tempo: int) -> str:
    lines = [
        f"10 REM {title.upper()} - MIDI TO ZX SPECTRUM 128 AY",
        "20 REM A=ACCOMPANIMENT  B=LEAD  C=BASS",
        "30 REM PRESS BREAK TO STOP",
        '40 LET A$=""',
        '50 LET B$=""',
        '60 LET C$=""',
    ]
    line = 70
    for variable, key in (("A", "accompaniment"), ("B", "lead"), ("C", "bass")):
        value = f"T{tempo}N" + strings[key][1:] if strings[key].startswith("N") else f"T{tempo}" + strings[key]
        chunks = [value[i:i + 170] for i in range(0, len(value), 170)] or [""]
        for chunk in chunks:
            lines.append(f'{line} LET {variable}$={variable}$+"{chunk}"')
            line += 10
    lines.extend([f"{line} PLAY A$,B$,C$", f"{line + 10} STOP"])
    return "\n".join(lines) + "\n"


def convert(input_path: Path, output_path: Path, title: str, lead_mode: str = "smart") -> None:
    division, notes, tempos = parse_midi(input_path)
    roles = quantised_roles(notes, division, lead_mode)
    strings = {name: render_role(sequence) for name, sequence in roles.items()}
    tempo = round(60_000_000 / (tempos[0] if tempos else 500_000))
    tempo = max(60, min(240, tempo))
    output_path.write_text(wrap_basic(strings, title, tempo), encoding="ascii")
    print(f"converted {len(notes)} notes; tempo {tempo} BPM; output {output_path}")
    print("lengths:", ", ".join(f"{k}={len(v)}" for k, v in strings.items()))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("midi", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--title", default=None)
    parser.add_argument("--lead-mode", choices=("smart", "top", "middle"), default="smart")
    parser.add_argument("--mode", choices=("basic", "ay", "tap"), default="basic")
    parser.add_argument("--drums", choices=("off", "noise", "hybrid"), default="off")
    parser.add_argument("--image", type=Path, default=None, help="PNG/JPG artwork shown before playback (--mode tap only)")
    args = parser.parse_args()
    if args.mode == "ay":
        from ay_midi import compile_ay
        compile_ay(args.midi, args.output, args.drums, args.lead_mode)
    elif args.mode == "tap":
        from ay_midi import compile_ay
        from build_tap import build
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".ay") as temp:
            compile_ay(args.midi, temp.name, args.drums)
            build(temp.name, args.output, (args.title or args.midi.stem).upper(), image_path=args.image)
    else:
        convert(args.midi, args.output, args.title or args.midi.stem, args.lead_mode)


if __name__ == "__main__":
    main()
