#!/usr/bin/env python3
"""Compile MIDI notes into 50 Hz AY-3-8912 register frames.

The AY only has square-wave tone generators, but a 50 Hz stream can still
give General MIDI families recognisably different attacks, decays and pitch
movement.  All patch envelopes are software-driven so the three voices remain
independent despite the chip having only one shared hardware envelope.
"""
from __future__ import annotations
import argparse
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
import math

AY_CLOCK = 1773400
FRAME_RATE = 50


@dataclass
class Note:
    start: int
    end: int
    pitch: int
    velocity: int
    channel: int
    program: int

def vlq(data, p):
    v=0
    while True:
        b=data[p]; p+=1; v=(v<<7)|(b&127)
        if b<128: return v,p

def read_midi(path):
    d=Path(path).read_bytes()
    if d[:4]!=b"MThd": raise ValueError("not a Standard MIDI file")
    h=int.from_bytes(d[4:8],"big"); fmt=int.from_bytes(d[8:10],"big")
    count=int.from_bytes(d[10:12],"big"); division=int.from_bytes(d[12:14],"big")
    if fmt not in (0,1) or division&0x8000: raise ValueError("only format 0/1 PPQN MIDI is supported")
    p=8+h; notes=[]; tempos=[]
    for _ in range(count):
        if d[p:p+4]!=b"MTrk": raise ValueError("invalid MIDI track")
        n=int.from_bytes(d[p+4:p+8],"big"); t=d[p+8:p+8+n]; p+=8+n
        q=0; tick=0; running=None; active=defaultdict(list); programs=[0]*16
        while q<len(t):
            delta,q=vlq(t,q); tick+=delta; status=t[q]
            if status<128:
                if running is None: raise ValueError("invalid running status")
                status=running
            else:
                q+=1; running=status
            typ=status&240; ch=status&15
            if status==255:
                meta=t[q]; q+=1; size,q=vlq(t,q); payload=t[q:q+size]; q+=size
                if meta==81 and len(payload)==3: tempos.append((tick,int.from_bytes(payload,"big")))
                continue
            if status in (240,247):
                size,q=vlq(t,q); q+=size; continue
            size=1 if typ in (192,208) else 2
            payload=t[q:q+size]; q+=size
            if typ==192: programs[ch]=payload[0]
            elif typ==144 and payload[1]:
                active[(ch,payload[0])].append((tick,payload[1],programs[ch]))
            elif typ in (128,144):
                key=(ch,payload[0])
                if active[key]:
                    start,vel,prog=active[key].pop(0)
                    if tick>start: notes.append(Note(start,tick,payload[0],vel,ch,prog))
    if not notes: raise ValueError("MIDI contains no closed note events")
    return division,notes,tempos

def period(pitch):
    return max(1,min(4095,round(AY_CLOCK/(16*(440*2**((pitch-69)/12))))))


def family(program):
    """Map a zero-based General MIDI program to an AY-friendly family."""
    program=max(0,min(127,program))
    if program < 8: return "piano"
    if program < 16: return "chromatic"
    if program < 24: return "organ"
    if program < 32: return "guitar"
    if program < 40: return "bass"
    if program < 48: return "strings"
    if program < 56: return "ensemble"
    if program < 64: return "brass"
    if program < 72: return "reed"
    if program < 80: return "pipe"
    if program < 88: return "lead"
    if program < 96: return "pad"
    if program < 104: return "effects"
    if program < 112: return "ethnic"
    if program < 120: return "percussive"
    if program < 128: return "sfx"
    return "tone"


@dataclass(frozen=True)
class Patch:
    """A deliberately small 50 Hz software synth patch.

    Times are video frames rather than MIDI ticks, so the sound is independent
    of the MIDI PPQN and tempo. Pitch values are cents. ``noise_period`` mixes
    the AY's shared noise generator into the voice when percussion is absent.
    """

    attack: int
    decay: int
    sustain: float
    release: int
    attack_floor: float = 0.25
    vibrato_cents: int = 0
    vibrato_delay: int = 0
    vibrato_step: int = 4
    tremolo: float = 0.0
    tremolo_step: int = 4
    scoop_cents: int = 0
    scoop_frames: int = 0
    noise_period: int | None = None


# These do not claim to reproduce sampled General MIDI instruments. They are
# compact AY patches chosen to preserve the tune while giving each family a
# useful chip-synth identity. Slow modulation is intentional: changing tone
# periods every video frame makes banked TAP streams unnecessarily large.
PATCHES = {
    "piano":      Patch(0, 10, 0.38, 3, 1.00),
    "chromatic":  Patch(0, 22, 0.18, 5, 1.00, 4, 8, 5, 0.05, 6),
    "organ":      Patch(2, 4, 0.94, 3, 0.45, 3, 8, 5, 0.12, 5),
    "guitar":     Patch(0, 14, 0.40, 4, 1.00, 5, 10, 5),
    "bass":       Patch(0, 6, 0.80, 3, 1.00, 0, 0, 4, 0.03, 6, -24, 5),
    "strings":    Patch(8, 8, 0.80, 10, 0.18, 9, 12, 4, 0.07, 5),
    "ensemble":   Patch(12, 8, 0.74, 12, 0.14, 11, 10, 4, 0.10, 5),
    "brass":      Patch(1, 6, 0.82, 5, 0.65, 4, 8, 5, 0.04, 6, -28, 5),
    "reed":       Patch(2, 5, 0.84, 5, 0.40, 8, 9, 4, 0.05, 5),
    "pipe":       Patch(2, 4, 0.90, 4, 0.35, 10, 6, 4, 0.04, 6),
    "lead":       Patch(0, 3, 0.92, 4, 1.00, 13, 7, 4, 0.04, 5, -12, 3),
    "pad":        Patch(15, 10, 0.72, 15, 0.10, 8, 16, 5, 0.12, 6),
    "effects":    Patch(0, 9, 0.58, 8, 1.00, 24, 0, 3, 0.18, 4, 30, 10, 8),
    "ethnic":     Patch(0, 16, 0.42, 5, 1.00, 6, 8, 5, 0.04, 6),
    "percussive": Patch(0, 8, 0.16, 2, 1.00, 0, 0, 4, 0.00, 4, 0, 0, 13),
    "sfx":        Patch(0, 6, 0.52, 5, 1.00, 32, 0, 3, 0.18, 4, 40, 10, 6),
    "tone":       Patch(0, 4, 0.88, 3, 1.00),
}

PLAIN_PATCH = Patch(0, 0, 1.0, 0, 1.0)
LFO = (0.0, 0.5, 1.0, 0.5, 0.0, -0.5, -1.0, -0.5)


def selected_patch(note, instrument_mode="auto"):
    if instrument_mode == "plain":
        return PLAIN_PATCH
    if instrument_mode != "auto":
        raise ValueError("instrument mode must be auto or plain")
    return PATCHES[family(note.program)]


def note_frame_position(note, frame_start, frame_end, frame_ticks):
    """Return elapsed, remaining and total 50 Hz frames for a note."""
    age=max(0,(frame_start-note.start)//frame_ticks)
    remaining=max(0,math.ceil((note.end-frame_end)/frame_ticks))
    duration=max(1,math.ceil((note.end-note.start)/frame_ticks))
    return age,remaining,duration


def patch_volume(note, frame_start, frame_end, frame_ticks=1, instrument_mode="auto"):
    patch=selected_patch(note,instrument_mode)
    age,remaining,duration=note_frame_position(note,frame_start,frame_end,frame_ticks)
    # Compress slow envelopes around short source notes. A one-frame melody
    # note must still speak immediately; a long strings/pad note keeps the
    # full slow attack that gives the family its character.
    attack=min(patch.attack,max(0,duration//4))
    decay=min(patch.decay,max(0,duration-attack-1))
    release=min(patch.release,max(0,duration//4))
    if attack and age < attack:
        level=patch.attack_floor+(1.0-patch.attack_floor)*(age+1)/attack
    elif decay and age < attack+decay:
        decay_age=age-attack
        level=1.0-(1.0-patch.sustain)*decay_age/decay
    else:
        level=patch.sustain
    if patch.tremolo:
        phase=(age//max(1,patch.tremolo_step))%len(LFO)
        level*=1.0-patch.tremolo*(0.5+0.5*LFO[phase])
    if release and remaining < release:
        level=min(level,patch.sustain*max(0.25,remaining/release))
    volume = max(1, min(15, round(note.velocity * 15 / 127 * level)))
    # One-step volume changes on a 50 Hz AY stream cost another event record
    # but are barely audible. Quantising the envelope keeps long songs inside
    # the 128K TAP memory budget while retaining eight useful levels.
    if volume > 1:
        volume = min(15, ((volume + 1) // 2) * 2)
    return volume


def patch_period(note, frame_start, frame_end, frame_ticks=1, instrument_mode="auto"):
    patch=selected_patch(note,instrument_mode)
    age,_,_=note_frame_position(note,frame_start,frame_end,frame_ticks)
    cents=0.0
    if patch.scoop_frames and age < patch.scoop_frames:
        cents+=patch.scoop_cents*(patch.scoop_frames-age)/patch.scoop_frames
    if patch.vibrato_cents and age >= patch.vibrato_delay:
        phase=((age-patch.vibrato_delay)//max(1,patch.vibrato_step))%len(LFO)
        cents+=patch.vibrato_cents*LFO[phase]
    return period(note.pitch+cents/100.0)

def drum_profile(pitch):
    """Return noise period, optional hybrid tone pitch, and decay style."""
    if pitch in (35, 36): return 24, max(24, pitch - 12), "kick"
    if pitch in (38, 40): return 10, None, "snare"
    if pitch in (42, 44): return 5, None, "closed_hat"
    if pitch == 46: return 3, None, "open_hat"
    if pitch in (41, 43, 45, 47, 48, 50): return 16, max(28, pitch - 12), "tom"
    if pitch in (49, 52, 55, 57): return 2, None, "crash"
    if pitch in (51, 53, 59): return 4, None, "ride"
    return max(1, min(31, 31 - round((pitch - 35) * 0.45))), None, "percussion"

def drum_volume(note, frame_start, frame_end, style):
    duration = max(1, note.end - note.start)
    age = max(0, frame_start - note.start)
    remaining = max(0, note.end - frame_end)
    if style in ("closed_hat", "snare", "kick"):
        decay = min(duration, 45)
    elif style in ("crash", "ride", "open_hat"):
        decay = min(duration, 180)
    else:
        decay = min(duration, 90)
    level = max(0.18, 1.0 - age / max(1, decay))
    if remaining <= 0:
        level *= 0.35
    volume = max(1, min(15, round(note.velocity * 15 / 127 * level)))
    if volume > 1:
        volume = min(15, ((volume + 1) // 2) * 2)
    return volume

def _choose_voices(tones, lead_mode="smart", previous_lead=None, previous_middle=None):
    """Route a polyphonic tone set to AY accompaniment, lead and bass.

    Smart mode combines pitch continuity with note shape, velocity and
    instrument family. Shorter, more expressive notes are favoured over a
    sustained backing note that happens to remain close to the old pitch.
    This matters for merged arrangements such as Popcorn, where the chorus
    lead sits over a long strings part.
    """
    by_pitch = {}
    for note in tones:
        current = by_pitch.get(note.pitch)
        if current is None or note.velocity > current.velocity:
            by_pitch[note.pitch] = note
    pitches = sorted(by_pitch)
    if not pitches:
        return [None, None, None], None, None

    bass_pitch = pitches[0]
    bass = by_pitch[bass_pitch]
    lead_candidates = pitches[1:] or pitches

    if lead_mode == "top":
        lead_pitch = lead_candidates[-1]
    elif lead_mode == "middle":
        lead_pitch = lead_candidates[(len(lead_candidates) - 1) // 2]
    elif lead_mode == "smart":
        def lead_score(pitch):
            note = by_pitch[pitch]
            family_bonus = {
                "lead": 500,
                "brass": 220,
                "strings": 100,
                "tone": 0,
                "pad": -180,
                "bass": -100,
            }.get(family(note.program), 0)
            # A short note is more likely to be the melody than a held pad or
            # strings note. Cap this term so continuity still matters.
            short_note_bonus = 2 * max(0, 300 - min(300, note.end - note.start))
            continuity = -3 * abs(pitch - previous_lead) if previous_lead is not None else 0
            return family_bonus + short_note_bonus + note.velocity + continuity

        lead_pitch = max(lead_candidates, key=lead_score)
    else:
        raise ValueError("lead mode must be smart, top or middle")

    lead = by_pitch[lead_pitch]
    middle_candidates = [pitch for pitch in pitches if pitch not in {bass_pitch, lead_pitch}]
    if not middle_candidates:
        middle = lead
        middle_pitch = lead_pitch
    elif previous_middle in middle_candidates:
        middle_pitch = previous_middle
        middle = by_pitch[middle_pitch]
    else:
        middle_pitch = middle_candidates[-1]
        middle = by_pitch[middle_pitch]

    return [middle, lead, bass], lead_pitch, middle_pitch


def frames_for(notes, division, drum_mode, tempo=500000, lead_mode="smart",
               instrument_mode="auto"):
    if notes is None: return b"",tempo
    end=max(n.end for n in notes)
    # MIDI ticks per second = division * 1_000_000 / tempo (microseconds per
    # quarter note); dividing by 50 gives ticks per AY frame. tempo belongs
    # in the denominator - it was multiplied in instead, which shrank the
    # step as tempo got faster and generated far too many 50 Hz frames per
    # bar, stretching every note out over many more real frames than it
    # should play for.
    step=max(1,round(division*1000000/(tempo*FRAME_RATE)))
    total=max(1,math.ceil((end+step)/step))
    out=bytearray()
    previous_lead = None
    previous_middle = None
    for frame in range(total):
        a=frame*step; b=a+step
        live=[n for n in notes if n.start<b and n.end>a]
        drums=[n for n in live if n.channel==9] if drum_mode!="off" else []
        tones=[n for n in live if n.channel!=9]
        voices, selected_lead, selected_middle = _choose_voices(tones, lead_mode, previous_lead, previous_middle)
        middle, lead, bass = voices
        if tones:
            previous_lead = selected_lead
            previous_middle = selected_middle
        else:
            previous_lead = None
            previous_middle = None
        regs=[0]*14; mixer=63
        for i,n in enumerate(voices):
            if n is not None:
                per=patch_period(n,a,b,step,instrument_mode)
                regs[i*2]=per&255; regs[i*2+1]=(per>>8)&15
                vol=patch_volume(n,a,b,step,instrument_mode)
                regs[8+i]=vol
                mixer &= ~(1<<i)
        # A few General MIDI families benefit from a shared noise texture.
        # Real channel-10 percussion owns the noise generator whenever it is
        # present, so an effects patch can never change a drum's character.
        patch_noise=[]
        if not drums and instrument_mode=="auto":
            for i,n in enumerate(voices):
                if n is None: continue
                noise_period=selected_patch(n,instrument_mode).noise_period
                if noise_period is not None:
                    patch_noise.append(noise_period)
                    mixer &= ~(1<<(i+3))
        if patch_noise:
            regs[6]=min(patch_noise)
        if drums:
            # AY has one shared noise generator. Choose the loudest hit for
            # this frame, then map its MIDI percussion note to a recognisable
            # kick, snare, hat, tom, crash or ride character.
            d=max(drums, key=lambda n: n.velocity)
            noise_period, tone_pitch, style = drum_profile(d.pitch)
            regs[6]=noise_period
            regs[10]=max(regs[10], drum_volume(d, a, b, style))
            mixer &= ~(1<<5)  # noise on channel C
            if drum_mode=="hybrid" and tone_pitch is not None:
                per=period(tone_pitch)
                regs[4]=per&255; regs[5]=(per>>8)&15
                regs[10]=max(regs[10], min(15, drum_volume(d, a, b, style)+2))
                mixer &= ~(1<<2)  # tone on channel C

        regs[7]=mixer
        out.extend(regs)
    out.append(255)
    return bytes(out), round(60000000/(tempo if tempo else 500000))

def compile_ay(input_path, output_path, drum_mode="off", lead_mode="smart",
               instrument_mode="auto"):
    division,notes,tempos=read_midi(input_path)
    tempo=tempos[0][1] if tempos else 500000
    frames,_=frames_for(notes,division,drum_mode,tempo,lead_mode,instrument_mode)
    Path(output_path).write_bytes(frames)
    print(f"wrote {output_path} ({len(frames)} bytes, {len(frames)//14} AY frames, "
          f"drums={drum_mode}, lead={lead_mode}, instruments={instrument_mode})")

if __name__=="__main__":
    p=argparse.ArgumentParser()
    p.add_argument("midi",type=Path); p.add_argument("output",type=Path)
    p.add_argument("--drums",choices=("off","noise","hybrid"),default="off")
    p.add_argument("--lead-mode",choices=("smart","top","middle"),default="smart")
    p.add_argument("--instruments",choices=("auto","plain"),default="auto")
    a=p.parse_args(); compile_ay(a.midi,a.output,a.drums,a.lead_mode,a.instruments)
