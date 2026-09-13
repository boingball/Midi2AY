#!/usr/bin/env python3
"""Build a self-starting ZX Spectrum TAP from AY register frames."""
from pathlib import Path
import struct

BASE=32768
WAIT=32600
PTR=32602
PLAYING=32604
MASK=32606
REG=32608
TMP=32609
# Shadow copies of the six AY registers the scope analyser cares about (tone
# period low byte and volume, per channel), kept in sync whenever the
# corresponding AY register is actually written, plus a running phase
# counter per channel and two scratch cells reused across channels while
# drawing.  The AY chip itself remembers its own register values; we only
# need shadow copies because the scope has to redraw every tick even on
# ticks where nothing changed.
PER_A,PER_B,PER_C,VOL_A,VOL_B,VOL_C=range(32610,32616)
PHASE_A,PHASE_B,PHASE_C=range(32616,32619)
ROWSEL=32619
WAVESRC=32620

def word(n): return bytes((n&255,n>>8))
def op(*b): return bytes(b)
def ld_hl(n): return op(0x21)+word(n)
def ld_mem_hl(n): return op(0x22)+word(n)
def ld_hl_mem(n): return op(0x2a)+word(n)
def ld_mem_a(n): return op(0x32)+word(n)
def ld_a_mem(n): return op(0x3a)+word(n)
def ld_de_mem(n): return op(0xed,0x5b)+word(n)
def ld_mem_de(n): return op(0xed,0x53)+word(n)

def screen_addr(y, x_byte=0):
    """Address of pixel row y, column-byte x_byte, in the Spectrum's
    interleaved display file layout."""
    return 16384 + (y & 0xC0)*32 + (y & 0x07)*256 + (y & 0x38)*4 + x_byte

# Bottom three character rows (21-23) of the display, one 8-pixel-tall band
# per AY channel, overlaid on whatever artwork is already on screen.
CHANNEL_ROWS=[[screen_addr(char_row*8+line) for line in range(8)] for char_row in (21,22,23)]
# Attribute cells for that same 3-row strip (96 contiguous bytes) - the scope
# only ever writes to bitmap memory, so without this its trace would render
# in whatever ink/paper colours the artwork happens to have there.
ATTR_BASE=0x5800+21*32
ATTR_LEN=32*3
SCOPE_ATTR=0x44 # bright green ink on black paper

# Eight column-rotations of a two-level square wave (period 8 columns, tiled
# 4x across the 32-byte-wide screen), used as the scope's "waveform" shape.
# Rotation is driven by a per-channel phase counter so each channel's trace
# visibly animates at a rate reacting to its current pitch.
def wave_rotation(r):
    return bytes(0xff if ((col+r)%8)<4 else 0x00 for col in range(32))
WAVE_TABLE=b"".join(wave_rotation(r) for r in range(8))

# Maps AY register index (0-13) to a slot in the 6-byte shadow area above
# (PER_A..VOL_C), or 0xff for registers the scope doesn't track.
REG_TO_SLOT=bytes((0,0xff,1,0xff,2,0xff,0xff,0xff,3,4,5,0xff,0xff,0xff))

def player(data_address):
    b=bytearray()
    labels={}
    rel=[]
    abs_refs=[]
    def mark(name): labels[name]=len(b)
    def jr(code,name): b.extend((code,0)); rel.append((len(b)-1,name))
    def imm16_label(name):
        pos=len(b); b.extend((0,0)); abs_refs.append((pos,name))
    def ld_hl_label(name): b.append(0x21); imm16_label(name)
    def ld_de_label(name): b.append(0x11); imm16_label(name)
    def call_label(name): b.append(0xcd); imm16_label(name)
    def jp_label(name): b.append(0xc3); imm16_label(name)
    _skip=[0]
    def jr_far(code,name):
        # Conditional branch to a label that may be farther than the +-127
        # bytes a JR can reach: invert the condition to skip over an
        # unconditional JP (always in range, absolute) to the real target.
        if code==0x18: jp_label(name); return
        inverse={0x20:0x28,0x28:0x20,0x30:0x38,0x38:0x30}[code]
        skip=f"__skip{_skip[0]}"; _skip[0]+=1
        jr(inverse,skip); jp_label(name); mark(skip)
    mark("start")
    # 128 BASIC leaves ROM 1 paged in with interrupt mode 2 active, whose
    # interrupt handler doesn't reliably re-enable interrupts around our
    # HALT loop. Force ROM 0 + IM 1 so the plain, non-paging 0x0038 handler
    # is used instead - but only flip the ROM-select bit (4). The event
    # data above 0xC000 was loaded into whichever RAM bank port 0x7ffd
    # already selects; forcing a specific bank here would make our own code
    # read a different bank than the one LOAD actually wrote into.
    #
    # The ROM's own interrupt handler reads BANK_M every tick to decide what
    # to write back to port 0x7ffd (it toggles the ROM bit on and off each
    # frame to poll the extended keyboard). Writing the port ourselves
    # without also updating BANK_M leaves that shadow variable stale, so the
    # ROM's next toggle XORs the wrong base value and can scramble the RAM
    # bank bits too - silently switching away the bank our event data lives
    # in a few dozen frames in. Keep BANK_M in sync with what we write.
    b+=op(0xf3) # DI while we repoint ROM/interrupt mode
    b+=op(0x3a)+word(0x5b5c) # LD A,(BANK_M) - the shadow of the last port 0x7ffd write
    b+=op(0xe6,0xef) # AND 0xef - clear only the ROM-select bit, keep the current RAM bank
    b+=op(0x32)+word(0x5b5c) # LD (BANK_M),A - keep the ROM's shadow copy in sync
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
    b+=op(0x21,0,0)+ld_mem_hl(WAIT)
    call_label("attr_init")
    b+=op(0xfb,0xc9)
    while len(b)<0x40: b.append(0)
    mark("tick"); b+=ld_a_mem(PLAYING)+op(0xb7); jr_far(0x28,"tick_end")
    # Animate the scope every tick, not just on ticks with a register change -
    # its phase counters need to keep advancing even while WAIT is counting
    # down, or the trace would visibly stall between events.
    call_label("scope_draw")
    b+=ld_hl_mem(WAIT)+op(0x7c,0xb5); jr(0x28,"process")
    b+=op(0x2b)+ld_mem_hl(WAIT)+op(0xc9)
    mark("process"); b+=ld_hl_mem(PTR)
    b+=op(0x5e,0x23,0x56,0x23)+op(0xed,0x53)+word(MASK)+ld_mem_hl(PTR)
    b+=op(0xaf,0x32)+word(REG)
    mark("reg_loop")
    b+=op(0xed,0x6b)+word(MASK) # HL=mask
    b+=op(0xcb,0x3c,0xcb,0x1d) # SRL H; RR L; carry is original bit 0
    b+=op(0xed,0x63)+word(MASK)
    jr_far(0x30,"next_reg")
    mark("read_value")
    b+=ld_hl_mem(PTR)+op(0x7e,0x23,0x22)+word(PTR)+op(0x32)+word(TMP)
    # Scope shadow update: shadow[reg_to_slot[REG]] = TMP, for the six
    # registers (period-low, volume x3 channels) the scope tracks. Runs only
    # on the register indices that actually changed this tick, not every
    # tick, so it costs almost nothing on average.
    b+=ld_a_mem(REG)+op(0x26,0)+op(0x6f) # LD A,(REG); LD H,0; LD L,A
    ld_de_label("reg_to_slot"); b+=op(0x19) # LD DE,reg_to_slot; ADD HL,DE
    b+=op(0x7e,0xfe,0xff); jr(0x28,"no_shadow") # LD A,(HL); CP 0xff; JR Z,no_shadow
    b+=op(0x26,0)+op(0x6f) # LD H,0; LD L,A
    b+=op(0x11)+word(PER_A)+op(0x19) # LD DE,PER_A; ADD HL,DE
    b+=ld_a_mem(TMP)+op(0x77) # LD A,(TMP); LD (HL),A
    mark("no_shadow")
    b+=op(0x3a)+word(REG)+op(0x06,0xff,0x0e,0xfd,0xed,0x79)
    b+=op(0x3a)+word(TMP)+op(0x06,0xbf,0xed,0x79)
    mark("next_reg")
    b+=op(0x3a)+word(REG)+op(0x3c,0x32)+word(REG)+op(0xfe,14); jr_far(0x20,"reg_loop")
    b+=ld_hl_mem(PTR)
    b+=op(0x5e,0x23,0x56,0x23)+ld_mem_de(WAIT)+ld_mem_hl(PTR)
    # The stream terminator is the two-byte value FF FF.  Both bytes must
    # match before stopping; checking only D leaves PLAYING set forever.
    b+=op(0x7a,0xfe,0xff); jr_far(0x20,"tick_end")
    b+=op(0x7b,0xfe,0xff); jr_far(0x20,"tick_end")
    jr(0x18,"finish")
    mark("tick_end"); b+=op(0xc9)
    mark("finish"); b+=op(0xaf)+ld_mem_a(PLAYING)+op(0xc9)

    # One-off: force the scope strip's attributes so the trace is always
    # visible regardless of what colour the artwork underneath it used.
    # Classic self-propagating fill: write the first byte, then LDIR with
    # the source trailing one behind the destination copies it forward.
    mark("attr_init")
    b+=op(0x21)+word(ATTR_BASE) # LD HL,ATTR_BASE
    b+=op(0x3e,SCOPE_ATTR)+op(0x77) # LD A,SCOPE_ATTR; LD (HL),A
    b+=op(0x11)+word(ATTR_BASE+1) # LD DE,ATTR_BASE+1
    b+=op(0x01)+word(ATTR_LEN-1) # LD BC,ATTR_LEN-1
    b+=op(0xed,0xb0)+op(0xc9) # LDIR; RET

    # --- 3-channel oscilloscope-style scope analyser -----------------------
    # draw_row: DE=dest screen address, A=row index (0-7). Fills that row
    # with the current wave pattern if it's the channel's selected row
    # (volume-driven), otherwise blanks it. Shared by all 24 row draws below
    # instead of unrolling them, to keep the code compact.
    mark("draw_row")
    b+=op(0x47) # LD B,A
    b+=ld_a_mem(ROWSEL)
    b+=op(0xb8) # CP B
    jr(0x20,"draw_row_blank")
    b+=ld_hl_mem(WAVESRC)
    jr(0x18,"draw_row_copy")
    mark("draw_row_blank")
    ld_hl_label("zero32")
    mark("draw_row_copy")
    b+=op(0x01)+word(32)+op(0xed,0xb0)+op(0xc9) # LD BC,32; LDIR; RET

    mark("scope_draw")
    for ch,(per_var,vol_var,phase_var,rows) in enumerate(zip(
            (PER_A,PER_B,PER_C),(VOL_A,VOL_B,VOL_C),(PHASE_A,PHASE_B,PHASE_C),CHANNEL_ROWS)):
        # rowsel = volume ? volume>>1 : 8 (8 is out of range: blanks the
        # whole band, i.e. a silent channel shows no trace at all).
        b+=ld_a_mem(vol_var)+op(0xb7) # LD A,(VOL_x); OR A
        jr(0x28,f"vol_zero_{ch}")
        b+=op(0xcb,0x3f) # SRL A
        jr(0x18,f"rowsel_done_{ch}")
        mark(f"vol_zero_{ch}"); b+=op(0x3e,8) # LD A,8
        mark(f"rowsel_done_{ch}"); b+=ld_mem_a(ROWSEL)
        # Advance this channel's phase by (255 - period_low): a coarse,
        # cheap stand-in for "faster wiggle at higher pitch" - not an
        # audio-accurate frequency, just a reactive, animated trace, since
        # simulating the real ~1.77MHz AY waveform sample-by-sample inside a
        # 50Hz interrupt isn't possible on a 3.5MHz Z80.
        b+=ld_a_mem(per_var)+op(0x2f)+op(0x47) # LD A,(PER_x); CPL; LD B,A
        b+=ld_a_mem(phase_var)+op(0x80) # LD A,(PHASE_x); ADD A,B
        b+=ld_mem_a(phase_var)
        b+=op(0xe6,7)+op(0x26,0)+op(0x6f) # AND 7; LD H,0; LD L,A
        b+=op(0x29)*5 # ADD HL,HL x5 (phase_index * 32)
        ld_de_label("wave_table"); b+=op(0x19) # LD DE,wave_table; ADD HL,DE
        b+=ld_mem_hl(WAVESRC)
        for r,addr in enumerate(rows):
            b+=op(0x11)+word(addr)+op(0x3e,r) # LD DE,addr; LD A,r
            call_label("draw_row")
    b+=op(0xc9) # RET

    mark("reg_to_slot"); b+=REG_TO_SLOT
    mark("wave_table"); b+=WAVE_TABLE
    mark("zero32"); b+=bytes(32)

    for pos,name in rel:
        target=labels[name]
        delta=target-(pos+1)
        if not -128<=delta<=127: raise ValueError("relative jump out of range")
        b[pos]=delta&255
    for pos,name in abs_refs:
        target=BASE+labels[name]
        b[pos]=target&255; b[pos+1]=(target>>8)&255
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

TOK={"LOAD":0xef,"CODE":0xaf,"RANDOMIZE":0xf9,"USR":0xc0,"SCREEN$":0xaa,"PAUSE":0xf2}
def basic_loader(name="POPCORN", has_image=False):
    def num(n): return str(n).encode()+b"\x0e"+float5(n)
    lines=[]
    filename=name[:10].encode("ascii")
    bodies=[]
    if has_image:
        # SCREEN$ is just CODE 16384 with the length implied; the ROM streams
        # the picture into the display file live as it loads, then PAUSE 0
        # waits for a keypress (indefinitely - 0 means "no timeout") before
        # the second LOAD brings in the player and starts the music.
        bodies.append(bytes((TOK["LOAD"],))+b' "'+filename+b'" '+bytes((TOK["SCREEN$"],)))
        bodies.append(bytes((TOK["PAUSE"],))+b" "+num(0))
    bodies.append(bytes((TOK["LOAD"],))+b' "'+filename+b'" '+bytes((TOK["CODE"],)))
    bodies.append(bytes((TOK["RANDOMIZE"],))+b" "+bytes((TOK["USR"],))+b" "+num(BASE))
    for no,body in enumerate(bodies, start=1):
        body=body+b"\r"; lines.append(struct.pack(">H",no*10)+struct.pack("<H",len(body))+body)
    # The ROM finds the end of a BASIC program via the VARS system variable,
    # not an in-band sentinel. An appended zero line number is not a real
    # line: the interpreter tries to parse it as one once it steps past the
    # last real line, which is what produced "Nonsense in BASIC, 0:1".
    return b"".join(lines)

def block(payload):
    body=struct.pack("<H",len(payload)+1)+payload
    checksum=0
    for byte in payload:
        checksum ^= byte
    return body+bytes((checksum,))
SCREEN_ADDR=16384
SCREEN_LEN=6912
def tap(name,code,screen=None):
    basic=basic_loader(name, has_image=screen is not None)
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
    if screen is not None:
        if len(screen)!=SCREEN_LEN: raise ValueError(f"screen must be {SCREEN_LEN} bytes")
        result+=block(header(3,len(screen),SCREEN_ADDR,len(screen))); result+=block(b"\xff"+screen)
    result+=block(header(3,len(code),BASE,len(code))); result+=block(b"\xff"+code)
    return bytes(result)

def build(ay_path,out_path,name="POPCORN",image_path=None):
    raw=Path(ay_path).read_bytes()
    frames=[raw[i:i+14] for i in range(0,len(raw)-1,14) if len(raw[i:i+14])==14]
    events=compress(frames)
    # player()'s length doesn't depend on the data address embedded in it
    # (any 16-bit immediate is 2 bytes either way), so size it once with a
    # placeholder address, then round up to place the actual data area.
    player_len=len(player(0))
    data_address=BASE+((player_len+0xff)//0x100)*0x100
    code=bytearray(player(data_address))
    code.extend(b"\0"*(data_address-(BASE+len(code))))
    code.extend(events)
    # Ensure the event stream and player remain below the 128K linear space.
    if len(code)>65536-BASE: raise ValueError("music does not fit in 48K above BASIC")
    screen=None
    if image_path is not None:
        import spectrum_screen, tempfile
        with tempfile.NamedTemporaryFile(suffix=".scr") as tmp:
            spectrum_screen.convert(image_path, tmp.name)
            screen=Path(tmp.name).read_bytes()
    Path(out_path).write_bytes(tap(name,bytes(code),screen))
    print(f"wrote {out_path}: {len(code)} bytes machine code/data, {len(events)} bytes events"
          +(f", {len(screen)} byte screen" if screen else ""))

if __name__=="__main__":
    import argparse
    p=argparse.ArgumentParser(); p.add_argument("ay"); p.add_argument("tap")
    p.add_argument("--image", help="PNG/JPG artwork to show before playback")
    a=p.parse_args(); build(a.ay,a.tap,image_path=a.image)
