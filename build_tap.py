#!/usr/bin/env python3
"""Build a self-starting ZX Spectrum TAP from AY register frames."""
from pathlib import Path
import struct

BASE=32768
DATA=BASE+512
WAIT=32600
PTR=32602
PLAYING=32604
MASK=32606
REG=32608
TMP=32609

def word(n): return bytes((n&255,n>>8))
def op(*b): return bytes(b)
def ld_hl(n): return op(0x21)+word(n)
def ld_mem_hl(n): return op(0x22)+word(n)
def ld_hl_mem(n): return op(0x2a)+word(n)
def ld_mem_a(n): return op(0x32)+word(n)
def ld_a_mem(n): return op(0x3a)+word(n)
def ld_de_mem(n): return op(0xed,0x5b)+word(n)
def ld_mem_de(n): return op(0xed,0x53)+word(n)

def player(data_address):
    b=bytearray()
    labels={}
    rel=[]
    def mark(name): labels[name]=len(b)
    def jr(code,name): b.extend((code,0)); rel.append((len(b)-1,name))
    mark("start")
    # 128 BASIC leaves ROM 1 paged in with interrupt mode 2 active. ROM 1's
    # interrupt handler pages RAM banks in and out of 0xC000-0xFFFF for its
    # own housekeeping, which collides with the event data we load into that
    # same range and leaves interrupts permanently disabled after the first
    # tick (CPU parked on the HALT below forever). Page in ROM 0 and force
    # IM 1 so the plain, non-paging 0x0038 handler is used instead.
    b+=op(0xf3) # DI while we repoint ROM/paging and interrupt mode
    b+=op(0x3e,0x00) # LD A,0 (ROM 0, RAM bank 0, normal screen, paging enabled)
    b+=op(0x01)+word(0x7ffd) # LD BC,0x7ffd
    b+=op(0xed,0x79) # OUT (C),A
    b+=op(0xed,0x56) # IM 1
    b+=op(0xcd)+word(BASE+0x20) # init
    mark("main"); b+=op(0x76) # HALT, 50 Hz ROM interrupt
    # Keep the tick entry point clear of the init routine.  Init now contains
    # EI, so it is longer than the original 0x30-byte slot.
    b+=op(0xcd)+word(BASE+0x40)
    b+=ld_a_mem(PLAYING)+op(0xb7); jr(0x20,"main"); b+=op(0xc9)
    while len(b)<0x20: b.append(0)
    mark("init"); b+=ld_hl(data_address)+ld_mem_hl(PTR)
    b+=op(0xaf)+ld_mem_a(PLAYING)
    b+=op(0x3e,1)+ld_mem_a(PLAYING)
    b+=op(0x21,0,0)+ld_mem_hl(WAIT)+op(0xfb,0xc9)
    while len(b)<0x40: b.append(0)
    mark("tick"); b+=ld_a_mem(PLAYING)+op(0xb7); jr(0x28,"tick_end")
    b+=ld_hl_mem(WAIT)+op(0x7c,0xb5); jr(0x28,"process")
    b+=op(0x2b)+ld_mem_hl(WAIT)+op(0xc9)
    mark("process"); b+=ld_hl_mem(PTR)
    b+=op(0x5e,0x23,0x56,0x23)+op(0xed,0x53)+word(MASK)+ld_mem_hl(PTR)
    b+=op(0xaf,0x32)+word(REG)
    mark("reg_loop")
    b+=op(0xed,0x6b)+word(MASK) # HL=mask
    b+=op(0xcb,0x3c,0xcb,0x1d) # SRL H; RR L; carry is original bit 0
    b+=op(0xed,0x63)+word(MASK)
    jr(0x30,"next_reg")
    mark("read_value")
    b+=ld_hl_mem(PTR)+op(0x7e,0x23,0x22)+word(PTR)+op(0x32)+word(TMP)
    b+=op(0x3a)+word(REG)+op(0x06,0xff,0x0e,0xfd,0xed,0x79)
    b+=op(0x3a)+word(TMP)+op(0x06,0xbf,0xed,0x79)
    mark("next_reg")
    b+=op(0x3a)+word(REG)+op(0x3c,0x32)+word(REG)+op(0xfe,14); jr(0x20,"reg_loop")
    b+=ld_hl_mem(PTR)
    b+=op(0x5e,0x23,0x56,0x23)+ld_mem_de(WAIT)+ld_mem_hl(PTR)
    # The stream terminator is the two-byte value FF FF.  Both bytes must
    # match before stopping; checking only D leaves PLAYING set forever.
    b+=op(0x7a,0xfe,0xff); jr(0x20,"tick_end")
    b+=op(0x7b,0xfe,0xff); jr(0x20,"tick_end")
    jr(0x18,"finish")
    mark("tick_end"); b+=op(0xc9)
    mark("finish"); b+=op(0xaf)+ld_mem_a(PLAYING)+op(0xc9)
    for pos,name in rel:
        target=labels[name]
        delta=target-(pos+1)
        if not -128<=delta<=127: raise ValueError("relative jump out of range")
        b[pos]=delta&255
    return bytes(b)

def compress(frames):
    # The player consumes records as: mask, changed values, wait-to-next.
    # Keep the wait after the values so the first record can be applied
    # immediately after the initial interrupt.
    events=[]; previous=bytes(14)
    for number,frame in enumerate(frames):
        if frame!=previous:
            mask=0; values=bytearray()
            for i,(before,after) in enumerate(zip(previous,frame)):
                if before!=after:
                    mask|=1<<i; values.append(after)
            events.append((number,mask,bytes(values)))
            previous=frame
    out=bytearray()
    for index,(number,mask,values) in enumerate(events):
        out+=struct.pack("<H",mask)+values
        if index+1<len(events):
            out+=struct.pack("<H",events[index+1][0]-number-1)
        else:
            out+=b"\xff\xff"
    return bytes(out)

def float5(n):
    if n==0: return b"\0"*5
    e=n.bit_length()
    mant=round((n/(1<<(e-1))-1)*(1<<31))
    return bytes((e+128,))+mant.to_bytes(4,"big")

TOK={"LOAD":0xef,"CODE":0xaf,"RANDOMIZE":0xf9,"USR":0xc0}
def basic_loader(name="POPCORN"):
    def num(n): return str(n).encode()+b"\x0e"+float5(n)
    lines=[]
    filename=name[:10].encode("ascii")
    for no,body in ((10,bytes((TOK["LOAD"],))+b' "'+filename+b'" '+bytes((TOK["CODE"],))),
                    (20,bytes((TOK["RANDOMIZE"],))+b" "+bytes((TOK["USR"],))+b" "+num(BASE))):
        body+=b"\r"; lines.append(struct.pack(">H",no)+struct.pack("<H",len(body))+body)
    # The BASIC program terminator is a zero line number: two zero bytes.
    # Leaving out the second byte makes the interpreter read into the next
    # byte of memory and can produce C Nonsense in BASIC at the last line.
    return b"".join(lines)+b"\0\0"

def block(payload):
    body=struct.pack("<H",len(payload)+1)+payload
    checksum=0
    for byte in payload:
        checksum ^= byte
    return body+bytes((checksum,))
def tap(name,code):
    basic=basic_loader(name)
    def header(kind,length,param1,param2):
        return bytes((0,kind))+name[:10].encode("ascii").ljust(10,b" ")+struct.pack("<HH",length,param1)+struct.pack("<H",param2)
    result=bytearray()
    # The Program header's second parameter is the offset from the start of
    # the program to the start of variables, NOT an absolute address; the
    # ROM computes VARS = PROG + param2 itself.  Passing PROG+len(basic)
    # here made the ROM add PROG twice, corrupting VARS/E_LINE right after
    # the loader block finished loading and crashing the tape immediately.
    # No variables are saved, so this must equal the program's own length.
    result+=block(header(0,len(basic),10,len(basic))); result+=block(b"\xff"+basic)
    result+=block(header(3,len(code),BASE,len(code))); result+=block(b"\xff"+code)
    return bytes(result)

def build(ay_path,out_path,name="POPCORN"):
    raw=Path(ay_path).read_bytes()
    frames=[raw[i:i+14] for i in range(0,len(raw)-1,14) if len(raw[i:i+14])==14]
    events=compress(frames)
    code=bytearray(player(DATA))
    code.extend(b"\0"*(DATA-(BASE+len(code))))
    code.extend(events)
    # Ensure the event stream and player remain below the 128K linear space.
    if len(code)>65536-BASE: raise ValueError("music does not fit in 48K above BASIC")
    Path(out_path).write_bytes(tap(name,bytes(code)))
    print(f"wrote {out_path}: {len(code)} bytes machine code/data, {len(events)} bytes events")

if __name__=="__main__":
    import argparse
    p=argparse.ArgumentParser(); p.add_argument("ay"); p.add_argument("tap")
    a=p.parse_args(); build(a.ay,a.tap)
