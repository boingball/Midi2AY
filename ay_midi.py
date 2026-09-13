#!/usr/bin/env python3
"""Compile MIDI notes into 50 Hz AY-3-8912 register frames."""
from __future__ import annotations
import argparse
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
import math

AY_CLOCK = 1773400
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
    p=8+h; notes=[]; tempos=[]; programs=[0]*16
    for _ in range(count):
        if d[p:p+4]!=b"MTrk": raise ValueError("invalid MIDI track")
        n=int.from_bytes(d[p+4:p+8],"big"); t=d[p+8:p+8+n]; p+=8+n
        q=0; tick=0; running=None; active=defaultdict(list)
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
    if program in range(32,40): return "bass"
    if program in range(40,52): return "strings"
    if program in range(56,64): return "brass"
    if program in range(80,88): return "lead"
    if program in range(88,96): return "pad"
    return "tone"

def _choose_voices(tones, lead_mode="smart", previous_lead=None, previous_middle=None):
    """Route a polyphonic tone set to AY accompaniment, lead and bass.

    Smart mode keeps the lead voice moving by pitch proximity between frames.
    This avoids the old top-note rule jumping between chord tones and losing
    the actual melody in merged piano MIDI files.
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
        if previous_lead in lead_candidates:
            lead_pitch = previous_lead
        elif previous_lead is None:
            lead_pitch = lead_candidates[-1]
        else:
            lead_pitch = min(
                lead_candidates,
                key=lambda pitch: (
                    abs(pitch - previous_lead),
                    -by_pitch[pitch].velocity,
                    -pitch,
                ),
            )
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


def frames_for(notes, division, drum_mode, tempo=500000, lead_mode="smart"):
    if notes is None: return b"",tempo
    end=max(n.end for n in notes)
    # MIDI ticks per second = division * 1_000_000 / tempo (microseconds per
    # quarter note); dividing by 50 gives ticks per AY frame. tempo belongs
    # in the denominator - it was multiplied in instead, which shrank the
    # step as tempo got faster and generated far too many 50 Hz frames per
    # bar, stretching every note out over many more real frames than it
    # should play for.
    step=max(1,round(division*1000000/(tempo*50)))
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
                per=period(n.pitch); regs[i*2]=per&255; regs[i*2+1]=(per>>8)&15
                vol=max(1,min(15,round(n.velocity*15/127)))
                f=family(n.program)
                if f=="bass": vol=max(1,vol-1)
                if f in ("strings","pad"): vol=max(1,vol-3)
                regs[8+i]=vol
                mixer &= ~(1<<i)
        if drums:
            d=max(drums,key=lambda n:n.velocity)
            # AY noise period: low notes are kick/tom-like; high notes are hats.
            regs[6]=max(1,min(31,31-round((d.pitch-35)*0.45)))
            regs[10]=max(regs[10],min(15,round(d.velocity*15/127)))
            mixer &= ~(1<<5)  # noise on channel C
            if drum_mode=="hybrid" and regs[4]==0 and d.pitch<50:
                per=period(max(24,d.pitch-12)); regs[4]=per&255; regs[5]=(per>>8)&15
                regs[10]=max(regs[10],10); mixer &= ~(1<<2)
        regs[7]=mixer
        # Simple patch shaping: envelope flag for sustained pad/string voices.
        if any(n and family(n.program) in ("strings","pad") for n in voices):
            regs[11]=24; regs[12]=0; regs[13]=9
        out.extend(regs)
    out.append(255)
    return bytes(out), round(60000000/(tempo if tempo else 500000))

def compile_ay(input_path, output_path, drum_mode="off", lead_mode="smart"):
    division,notes,tempos=read_midi(input_path)
    tempo=tempos[0][1] if tempos else 500000
    frames,_=frames_for(notes,division,drum_mode,tempo,lead_mode)
    Path(output_path).write_bytes(frames)
    print(f"wrote {output_path} ({len(frames)} bytes, {len(frames)//14} AY frames, drums={drum_mode}, lead={lead_mode})")

if __name__=="__main__":
    p=argparse.ArgumentParser()
    p.add_argument("midi",type=Path); p.add_argument("output",type=Path)
    p.add_argument("--drums",choices=("off","noise","hybrid"),default="off")
    p.add_argument("--lead-mode",choices=("smart","top","middle"),default="smart")
    a=p.parse_args(); compile_ay(a.midi,a.output,a.drums,a.lead_mode)
