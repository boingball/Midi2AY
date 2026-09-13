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
# Banked TAP support for long event streams.
BANK_STATE=32619
VISUAL_MODE=32620
VISUAL_LAST=32621
VISUAL_PHASE=32622
PULSE_ROW=32623
DEMO_FRAME=32624
# Four bytes per bouncing ball: character-column X, character-row Y, signed
# X velocity and signed Y velocity.  Character-aligned movement keeps the
# XOR sprite tiny and, crucially, lets it erase itself without another
# screen-sized backing buffer competing with the banked music stream.
BALL_STATES=(32625,32629,32633)
TICK_OFFSET=0x60
BANK_RESET_OFFSET=0x1800
BANK_NEXT_OFFSET=0x1810
SCREEN_BACKUP_OFFSET=0x1840
SCREEN_RESTORE_OFFSET=0x1860
BANK_LOAD_ADDR=0xc000
BANK_SIZE=0x4000
BANK_MARKER=b"\xff\xfe"
SCREEN_ADDR=0x4000
SCREEN_LEN=6912
# Leave ample room between BASIC's stack and the player state at 32600.
BASIC_RAMTOP=30000

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

# A smooth travelling triangle wave, one continuous thin line sweeping the
# full 8-pixel height of a channel's band across the full 256-pixel screen
# width, computed at per-pixel resolution (not per byte-column) so it reads
# as a genuine sloped/curved trace rather than blocky flat-topped steps.
# WAVE_PHASES rotated copies give a smoothly travelling animation; the
# per-channel phase counter (pitch-driven, see scope_draw) picks which one
# plays each tick. Each phase is a full 8-row x 32-byte picture (256 bytes):
# drawing it is then just 8 back-to-back 32-byte block copies, one per row,
# with no per-row decision needed - silence instead draws 8 copies of a
# blank 32-byte row.
WAVE_PHASES=16
WAVE_PERIOD_PX=128
def wave_row_at(x, phase):
    o = (x + phase*(WAVE_PERIOD_PX//WAVE_PHASES)) % WAVE_PERIOD_PX
    half = WAVE_PERIOD_PX//2
    return round(o/half*7) if o<half else round((WAVE_PERIOD_PX-o)/half*7)
def wave_picture(phase):
    rows = [bytearray(32) for _ in range(8)]
    for x in range(256):
        y = wave_row_at(x, phase)
        rows[y][x//8] |= 0x80 >> (x%8)
    return b"".join(bytes(r) for r in rows)
WAVE_TABLE=b"".join(wave_picture(p) for p in range(WAVE_PHASES))
BAR_TABLE=b"".join(bytes((0xff,))*(level*2)+bytes(32-level*2) for level in range(16))
FULL32=bytes((0xff,))*32

# Maps AY register index (0-13) to a slot in the 6-byte shadow area above
# (PER_A..VOL_C), or 0xff for registers the scope doesn't track.
REG_TO_SLOT=bytes((0,0xff,1,0xff,2,0xff,0xff,0xff,3,4,5,0xff,0xff,0xff))
VISUAL_IDS={"scope":1,"bars":2,"pulse":3,"colour":4,"demo":5}

def player(data_address, initial_visual=1, title="MUSIC"):
    if initial_visual not in VISUAL_IDS.values():
        raise ValueError("initial visual mode must be 1-5")
    title="".join(ch if 32<=ord(ch)<127 else "_" for ch in str(title).upper())[:30] or "MUSIC"
    title_x=(32-len(title))//2
    b=bytearray()
    labels={}
    rel=[]
    abs_refs=[]
    def mark(name): labels[name]=len(b)
    def jr(code,name): b.extend((code,0)); rel.append((len(b)-1,name))
    def imm16_label(name,addend=0):
        pos=len(b); b.extend((0,0)); abs_refs.append((pos,name,addend))
    def ld_hl_label(name,addend=0): b.append(0x21); imm16_label(name,addend)
    def ld_de_label(name,addend=0): b.append(0x11); imm16_label(name,addend)
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
    b+=op(0xf3)
    b+=op(0x31)+word(0x7ff0) # keep the return stack in fixed RAM below C000
    b+=op(0xaf)+ld_mem_a(BANK_STATE)
    b+=ld_mem_a(0x5b5c)
    b+=op(0x01)+word(0x7ffd)+op(0xed,0x79)
    b+=op(0xed,0x56) # IM 1
    b+=op(0xcd)+word(BASE+0x22) # init
    mark("main"); b+=op(0x76) # HALT, 50 Hz ROM interrupt
    # Keep the tick entry point clear of the expanded visual-mode init state.
    b+=op(0xcd)+word(BASE+TICK_OFFSET)
    b+=ld_a_mem(PLAYING)+op(0xb7); jr(0x20,"main")
    mark("stopped"); b+=op(0xf3,0x76) # stop safely; BASIC stack was replaced for bank paging
    while len(b)<0x22: b.append(0)
    mark("init"); b+=ld_hl(data_address)+ld_mem_hl(PTR)
    b+=op(0xaf)+ld_mem_a(PLAYING)
    b+=op(0x3e,1)+ld_mem_a(PLAYING)
    b+=op(0x21,0,0)+ld_mem_hl(WAIT)
    b+=op(0x3e,initial_visual)+ld_mem_a(VISUAL_MODE)
    b+=op(0xaf)+ld_mem_a(VISUAL_LAST)+ld_mem_a(VISUAL_PHASE)+ld_mem_a(DEMO_FRAME)
    b+=op(0x3e,0xff)+ld_mem_a(PULSE_ROW)
    call_label("attr_init")
    b+=op(0xfb,0xc9)
    while len(b)<TICK_OFFSET: b.append(0)
    mark("tick"); b+=ld_a_mem(PLAYING)+op(0xb7); jr_far(0x28,"tick_end")
    # Animate the scope every tick, not just on ticks with a register change -
    # its phase counters need to keep advancing even while WAIT is counting
    # down, or the trace would visibly stall between events.
    call_label("visual_draw")
    b+=ld_hl_mem(WAIT)+op(0x7c,0xb5); jr(0x28,"process")
    b+=op(0x2b)+ld_mem_hl(WAIT)+op(0xc9)
    mark("process"); b+=ld_hl_mem(PTR)
    b+=op(0x5e,0x23,0x56,0x23)
    # FF FE is a bank transition marker; it cannot be a valid 14-bit mask.
    b+=op(0x7b,0xfe,0xff); jr_far(0x20,"not_bank_marker")
    b+=op(0x7a,0xfe,0xfe); jr_far(0x20,"not_bank_marker")
    call_label("bank_next")
    b+=ld_hl(0xc000)+ld_mem_hl(PTR)
    jp_label("process")
    mark("not_bank_marker")
    b+=op(0xed,0x53)+word(MASK)+ld_mem_hl(PTR)
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

    # Visual dispatcher. Keys 1-5 select scope, volume bars, artwork pulse,
    # colour bars and a title/bouncing-balls demo. The single keyboard
    # half-row read is adapted from the approach used by Dean Belfield's
    # MIT-licensed lib-spectrum keyboard module; only the 1-5 row is needed.
    mark("visual_draw")
    call_label("keyboard_1_5")
    b+=ld_a_mem(VISUAL_MODE)+op(0x47) # B=requested mode
    b+=ld_a_mem(VISUAL_LAST)+op(0xb8) # CP B
    jr(0x28,"visual_dispatch")
    b+=op(0xfe,3); jr(0x20,"visual_no_pulse_cleanup")
    call_label("pulse_clear")
    mark("visual_no_pulse_cleanup")
    b+=ld_a_mem(VISUAL_LAST)+op(0xfe,5); jr(0x20,"visual_no_demo_cleanup")
    call_label("demo_clear")
    mark("visual_no_demo_cleanup")
    b+=ld_a_mem(VISUAL_MODE)+ld_mem_a(VISUAL_LAST)
    b+=op(0x3e,0xff)+ld_mem_a(PULSE_ROW)
    call_label("visual_clear")
    b+=ld_a_mem(VISUAL_MODE)+op(0xfe,5); jr(0x20,"visual_dispatch")
    call_label("demo_enter")
    mark("visual_dispatch")
    b+=ld_a_mem(VISUAL_MODE)+op(0xfe,1); jr_far(0x28,"scope_draw")
    b+=op(0xfe,2); jr_far(0x28,"bars_draw")
    b+=op(0xfe,3); jr_far(0x28,"pulse_draw")
    b+=op(0xfe,4); jr_far(0x28,"colour_draw")
    jp_label("demo_draw")

    mark("keyboard_1_5")
    b+=op(0x01)+word(0xf7fe)+op(0xed,0x78,0x2f,0xe6,0x1f,0xc8)
    b+=op(0xcb,0x47); jr(0x28,"key_test_2") # BIT 0,A / key 1
    b+=op(0x3e,1)+ld_mem_a(VISUAL_MODE)+op(0xc9)
    mark("key_test_2"); b+=op(0xcb,0x4f); jr(0x28,"key_test_3")
    b+=op(0x3e,2)+ld_mem_a(VISUAL_MODE)+op(0xc9)
    mark("key_test_3"); b+=op(0xcb,0x57); jr(0x28,"key_test_4")
    b+=op(0x3e,3)+ld_mem_a(VISUAL_MODE)+op(0xc9)
    mark("key_test_4"); b+=op(0xcb,0x5f); jr(0x28,"key_test_5")
    b+=op(0x3e,4)+ld_mem_a(VISUAL_MODE)+op(0xc9)
    mark("key_test_5"); b+=op(0xcb,0x67,0xc8)
    b+=op(0x3e,5)+ld_mem_a(VISUAL_MODE)+op(0xc9)

    # Toggle BRIGHT on one attribute row. XOR makes this reversible even when
    # the source artwork already used bright colours.
    mark("pulse_toggle")
    b+=op(0x26,0,0x6f) # H=0, L=row
    for _ in range(5): b+=op(0x29) # row * 32
    b+=op(0x11)+word(0x5800)+op(0x19,0x06,32)
    mark("pulse_toggle_loop")
    b+=op(0x7e,0xee,0x40,0x77,0x23); jr(0x10,"pulse_toggle_loop")
    b+=op(0xc9)

    mark("pulse_clear")
    b+=ld_a_mem(PULSE_ROW)+op(0xfe,0xff,0xc8)
    call_label("pulse_toggle")
    b+=op(0x3e,0xff)+ld_mem_a(PULSE_ROW)
    b+=op(0xaf,0xd3,0xfe,0xc9) # black border

    # Clear only the 24-pixel visual strip, preserving the cover above it.
    mark("visual_clear")
    for rows in CHANNEL_ROWS:
        for addr in rows:
            ld_hl_label("zero32")
            b+=op(0x11)+word(addr)+op(0x01)+word(32)+op(0xed,0xb0)
    call_label("attr_init")
    b+=op(0xc9)

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
    # Each channel's 8-row band is filled with 8 back-to-back 32-byte block
    # copies. For a playing channel the source is one full 256-byte phase
    # picture from WAVE_TABLE (LDIR auto-advances HL by 32 after each row,
    # so the whole picture drops in with no per-row addressing needed);
    # for a silent channel every row instead copies the same blank 32-byte
    # source, i.e. the whole band goes dark.
    mark("scope_draw")
    for ch,(per_var,vol_var,phase_var,rows) in enumerate(zip(
            (PER_A,PER_B,PER_C),(VOL_A,VOL_B,VOL_C),(PHASE_A,PHASE_B,PHASE_C),CHANNEL_ROWS)):
        b+=ld_a_mem(vol_var)+op(0xb7) # LD A,(VOL_x); OR A
        jr(0x28,f"ch_silent_{ch}")
        # Advance this channel's phase by (255 - period_low): a coarse,
        # cheap stand-in for "faster wiggle at higher pitch" - not an
        # audio-accurate frequency, just a reactive, animated trace, since
        # simulating the real ~1.77MHz AY waveform sample-by-sample inside a
        # 50Hz interrupt isn't possible on a 3.5MHz Z80.
        b+=ld_a_mem(per_var)+op(0x2f)+op(0x47) # LD A,(PER_x); CPL; LD B,A
        b+=ld_a_mem(phase_var)+op(0x80) # LD A,(PHASE_x); ADD A,B
        b+=ld_mem_a(phase_var)
        b+=op(0xe6,WAVE_PHASES-1) # AND (WAVE_PHASES-1) -> phase_index
        b+=op(0x67)+op(0x2e,0) # LD H,A; LD L,0  (HL = phase_index*256)
        ld_de_label("wave_table"); b+=op(0x19) # LD DE,wave_table; ADD HL,DE
        for addr in rows:
            b+=op(0x11)+word(addr) # LD DE,addr
            b+=op(0x01)+word(32) # LD BC,32
            b+=op(0xed,0xb0) # LDIR (advances HL by 32 for the next row)
        jr(0x18,f"ch_done_{ch}")
        mark(f"ch_silent_{ch}")
        for addr in rows:
            ld_hl_label("zero32")
            b+=op(0x11)+word(addr) # LD DE,addr
            b+=op(0x01)+word(32) # LD BC,32
            b+=op(0xed,0xb0) # LDIR
        mark(f"ch_done_{ch}")
    b+=op(0xc9) # RET

    # Three horizontal VU bars, one 8-pixel band per AY channel. A compact
    # table turns each 4-bit AY volume directly into a 0-30 byte bar.
    mark("bars_draw")
    for ch,(vol_var,rows,attr) in enumerate(zip(
            (VOL_A,VOL_B,VOL_C),CHANNEL_ROWS,(0x45,0x46,0x43))):
        b+=ld_a_mem(vol_var)+op(0xe6,0x0f,0x26,0,0x6f)
        for _ in range(5): b+=op(0x29)
        ld_de_label("bar_table"); b+=op(0x19)
        for addr in rows:
            b+=op(0xe5)+op(0x11)+word(addr)+op(0x01)+word(32)+op(0xed,0xb0,0xe1)
        attr_addr=ATTR_BASE+ch*32
        b+=ld_hl(attr_addr)+op(0x3e,attr,0x77)+op(0x11)+word(attr_addr+1)
        b+=op(0x01)+word(31)+op(0xed,0xb0)
    b+=op(0xc9)

    # Artwork pulse: border colour follows the combined channel volume while
    # one reversible BRIGHT row sweeps down the untouched part of the cover.
    mark("pulse_draw")
    b+=ld_a_mem(VOL_A)+op(0x47)+ld_a_mem(VOL_B)+op(0x80,0x47)
    b+=ld_a_mem(VOL_C)+op(0x80,0xe6,0x07,0xd3,0xfe)
    b+=ld_a_mem(PULSE_ROW)+op(0xfe,0xff); jr(0x28,"pulse_first_row")
    call_label("pulse_toggle")
    b+=ld_a_mem(PULSE_ROW)+op(0x3c,0xfe,21); jr(0x38,"pulse_store_row")
    b+=op(0xaf); jr(0x18,"pulse_store_row")
    mark("pulse_first_row"); b+=op(0xaf)
    mark("pulse_store_row"); b+=ld_mem_a(PULSE_ROW)
    call_label("pulse_toggle")
    b+=op(0xc9)

    # Animated colour bars inspired by lib-spectrum's colour-table demo, but
    # frame-based rather than cycle-timed so AY playback remains uninterrupted.
    mark("colour_draw")
    for rows in CHANNEL_ROWS:
        for addr in rows:
            ld_hl_label("full32")
            b+=op(0x11)+word(addr)+op(0x01)+word(32)+op(0xed,0xb0)
    b+=ld_a_mem(VISUAL_PHASE)+op(0x3c,0xe6,0x07)+ld_mem_a(VISUAL_PHASE)
    b+=op(0xd3,0xfe,0x5f)+ld_hl(ATTR_BASE)+op(0x06,ATTR_LEN)
    mark("colour_attr_loop")
    b+=op(0x7b,0xe6,0x07,0xf6,0x40,0x77,0x23,0x1c)
    jr(0x10,"colour_attr_loop")
    b+=op(0xc9)

    # --- Demo mode: title card and three bouncing XOR bubbles -------------
    # lib-spectrum includes a complete filled-vector 3D cube demo and a
    # sprite demo. The original 3D renderer continuously clears and copies a
    # 6144-byte buffer at 0xE000, which collides with this player's pageable
    # music data. This small, frame-based adaptation borrows the demo spirit
    # without copying those routines: an artwork-safe title card and one XOR
    # bubble per AY channel, animated at 25 Hz while playback stays at 50 Hz.
    mark("demo_enter")
    # Save the top character row (256 bitmap bytes plus 32 attributes), then
    # replace it with a high-contrast title bar. This is restored byte-for-
    # byte when another visual mode is selected.
    for line in range(8):
        b+=ld_hl(screen_addr(line)); ld_de_label("title_backup",line*32)
        b+=op(0x01)+word(32)+op(0xed,0xb0)
    b+=ld_hl(0x5800); ld_de_label("title_attr_backup")
    b+=op(0x01)+word(32)+op(0xed,0xb0)
    for line in range(8):
        ld_hl_label("zero32")
        b+=op(0x11)+word(screen_addr(line))+op(0x01)+word(32)+op(0xed,0xb0)
    b+=ld_hl(0x5800)+op(0x3e,0x47,0x77)+op(0x11)+word(0x5801)
    b+=op(0x01)+word(31)+op(0xed,0xb0)
    # ROM-font title, centred and capped at 30 characters.
    for x,ch in enumerate(title,title_x):
        b+=op(0x11)+word(0x3c00+(ord(ch)-32)*8)
        b+=ld_hl(screen_addr(0,x))+op(0x06,8)
        mark(f"title_char_{x}")
        b+=op(0x1a,0x77,0x13,0x24); jr(0x10,f"title_char_{x}")
    # Deterministic starting positions keep all bubbles below the title.
    for addr,state in zip(BALL_STATES,((1,3,1,1),(29,8,0xff,1),(12,17,1,0xff))):
        for offset,value in enumerate(state):
            b+=op(0x3e,value)+ld_mem_a(addr+offset)
        b+=op(0xdd,0x21)+word(addr); call_label("ball_xor_ix")
    b+=op(0xaf)+ld_mem_a(DEMO_FRAME)+op(0xc9)

    mark("demo_clear")
    # Erase the XOR bubbles first, then put back the exact artwork/title row.
    for addr in BALL_STATES:
        b+=op(0xdd,0x21)+word(addr); call_label("ball_xor_ix")
    for line in range(8):
        ld_hl_label("title_backup")
        # Advance HL to this row's saved 32-byte slice.
        if line:
            b+=op(0x11)+word(line*32)+op(0x19)
        b+=op(0x11)+word(screen_addr(line))+op(0x01)+word(32)+op(0xed,0xb0)
    ld_hl_label("title_attr_backup")
    b+=op(0x11)+word(0x5800)+op(0x01)+word(32)+op(0xed,0xb0,0xc9)

    mark("demo_draw")
    # Move on alternate ticks: smooth enough for a 1980s demo, with generous
    # time left for dense AY register frames and bank transitions.
    b+=ld_a_mem(DEMO_FRAME)+op(0xee,1)+ld_mem_a(DEMO_FRAME)+op(0xb7,0xc0)
    for addr in BALL_STATES:
        b+=op(0xdd,0x21)+word(addr); call_label("ball_step")
    b+=op(0xc9)

    mark("ball_step")
    call_label("ball_xor_ix") # erase old position
    # X += DX, bounce within columns 0..30 (the ball is two bytes wide).
    b+=op(0xdd,0x7e,0,0xdd,0x86,2,0xfe,31); jr(0x38,"ball_x_ok")
    b+=op(0xfe,0x80); jr(0x30,"ball_x_left")
    b+=op(0x3e,29,0xdd,0x36,2,0xff); jr(0x18,"ball_x_ok")
    mark("ball_x_left"); b+=op(0x3e,1,0xdd,0x36,2,1)
    mark("ball_x_ok"); b+=op(0xdd,0x77,0)
    # Y += DY, bounce within rows 2..18 (the ball is two rows high).
    b+=op(0xdd,0x7e,1,0xdd,0x86,3,0xfe,19); jr(0x38,"ball_y_check_top")
    b+=op(0xfe,0x80); jr(0x30,"ball_y_top")
    b+=op(0x3e,17,0xdd,0x36,3,0xff); jr(0x18,"ball_y_ok")
    mark("ball_y_check_top"); b+=op(0xfe,2); jr(0x30,"ball_y_ok")
    mark("ball_y_top"); b+=op(0x3e,3,0xdd,0x36,3,1)
    mark("ball_y_ok"); b+=op(0xdd,0x77,1)
    jp_label("ball_xor_ix") # draw new position and return to caller

    mark("ball_xor_ix")
    # Convert character-row Y through a compact address table, add byte X,
    # then XOR a chunky 16x16 bubble in two eight-line halves.
    ld_de_label("ball_sprite")
    for half in range(2):
        b+=op(0xd5,0xdd,0x7e,1)
        if half: b+=op(0x3c)
        b+=op(0x87,0x26,0,0x6f,0xe5)
        ld_hl_label("ball_row_table"); b+=op(0xd1,0x19,0x5e,0x23,0x56,0xeb)
        b+=op(0xdd,0x7e,0,0x85,0x6f,0xd1,0x06,8)
        mark(f"ball_xor_loop_{half}")
        b+=op(0x1a,0xae,0x77,0x13,0x23,0x1a,0xae,0x77,0x13,0x2b,0x24)
        jr(0x10,f"ball_xor_loop_{half}")
    # Toggle BRIGHT and the three PAPER bits in the corresponding 2x2
    # attribute cells. The colour block makes the bubbles readable over busy
    # cover art, and XOR means the original attributes still restore exactly.
    b+=op(0xdd,0x7e,1,0x87,0x26,0,0x6f,0xe5)
    ld_hl_label("ball_attr_table"); b+=op(0xd1,0x19,0x5e,0x23,0x56,0xeb)
    b+=op(0xdd,0x7e,0,0x85,0x6f,0x06,2)
    mark("ball_attr_loop")
    b+=op(0x7e,0xee,0x78,0x77,0x23,0x7e,0xee,0x78,0x77)
    b+=op(0x11)+word(31)+op(0x19); jr(0x10,"ball_attr_loop")
    b+=op(0xc9)

    while len(b)<BANK_RESET_OFFSET: b.append(0)
    mark("bank_reset")
    b+=op(0xaf)+ld_mem_a(BANK_STATE); jp_label("bank_next")
    while len(b)<BANK_NEXT_OFFSET: b.append(0)
    mark("bank_next")
    b+=ld_a_mem(BANK_STATE)+op(0x3c)
    # Banks 2 and 5 are permanently mapped at 0x8000 and 0x4000 on a 128K
    # Spectrum. Paging either at 0xc000 aliases and overwrites the fixed
    # player or the screen, so use the safe sequence 1, 3, 4, 6, 7.
    b+=op(0xfe,0x02,0x20,0x01,0x3c) # CP 2; JR NZ,+1; INC A
    b+=op(0xfe,0x05,0x20,0x01,0x3c) # CP 5; JR NZ,+1; INC A
    b+=ld_mem_a(BANK_STATE)
    # Preserve BANK_M bits 3-7 while replacing only RAM-bank bits 0-2.
    # This is essential while called from 128 BASIC: clearing bit 4 would
    # page ROM 0 over ROM 1 before RET, so BASIC resumes in the wrong ROM.
    b+=op(0x5f)+ld_a_mem(0x5b5c)+op(0xe6,0xf8,0xb3)
    b+=ld_mem_a(0x5b5c)+op(0x01)+word(0x7ffd)+op(0xed,0x79)+op(0xc9)

    # Preserve a clean copy of the artwork in otherwise-unused RAM bank 0.
    # Later ROM LOAD messages can then be erased instantly without storing a
    # duplicate 6912-byte screen block on tape.
    if len(b)>SCREEN_BACKUP_OFFSET: raise ValueError("bank routine overlaps screen backup")
    while len(b)<SCREEN_BACKUP_OFFSET: b.append(0)
    mark("screen_backup")
    b+=op(0xf3,0xaf)+ld_mem_a(BANK_STATE)
    b+=ld_a_mem(0x5b5c)+op(0xe6,0xf8)+ld_mem_a(0x5b5c)
    b+=op(0x01)+word(0x7ffd)+op(0xed,0x79)
    b+=ld_hl(SCREEN_ADDR)+op(0x11)+word(BANK_LOAD_ADDR)
    b+=op(0x01)+word(SCREEN_LEN)+op(0xed,0xb0,0xfb,0xc9)

    if len(b)>SCREEN_RESTORE_OFFSET: raise ValueError("screen backup overlaps restore")
    while len(b)<SCREEN_RESTORE_OFFSET: b.append(0)
    mark("screen_restore")
    b+=op(0xf3,0xaf)+ld_mem_a(BANK_STATE)
    b+=ld_a_mem(0x5b5c)+op(0xe6,0xf8)+ld_mem_a(0x5b5c)
    b+=op(0x01)+word(0x7ffd)+op(0xed,0x79)
    b+=ld_hl(BANK_LOAD_ADDR)+op(0x11)+word(SCREEN_ADDR)
    b+=op(0x01)+word(SCREEN_LEN)+op(0xed,0xb0,0xfb,0xc9)

    mark("reg_to_slot"); b+=REG_TO_SLOT
    mark("wave_table"); b+=WAVE_TABLE
    mark("bar_table"); b+=BAR_TABLE
    mark("full32"); b+=FULL32
    mark("zero32"); b+=bytes(32)
    mark("ball_row_table")
    for row in range(20): b+=word(screen_addr(row*8))
    mark("ball_attr_table")
    for row in range(20): b+=word(0x5800+row*32)
    mark("ball_sprite")
    b+=bytes((
        0x07,0xe0, 0x1f,0xf8, 0x3f,0xfc, 0x7f,0xfe,
        0x7c,0x7e, 0xf8,0x3f, 0xf8,0x3f, 0xfc,0x7f,
        0xfc,0x7f, 0xfe,0xff, 0x7f,0xfe, 0x7f,0xfe,
        0x3f,0xfc, 0x1f,0xf8, 0x07,0xe0, 0x00,0x00,
    ))
    mark("title_backup"); b+=bytes(8*32)
    mark("title_attr_backup"); b+=bytes(32)

    for pos,name in rel:
        target=labels[name]
        delta=target-(pos+1)
        if not -128<=delta<=127: raise ValueError("relative jump out of range")
        b[pos]=delta&255
    for pos,name,addend in abs_refs:
        target=BASE+labels[name]+addend
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

def record_length(events, pos):
    mask=struct.unpack_from("<H",events,pos)[0]
    if mask & 0xc000:
        raise ValueError("reserved event mask")
    return 2 + mask.bit_count() + 2

def split_events(events, first_capacity):
    # Split records into a fixed-bank chunk and 16K bank chunks.
    if not events:
        return [b""]
    chunks=[]
    pos=0
    capacity=first_capacity
    while pos<len(events):
        chunk=bytearray()
        while pos<len(events):
            length=record_length(events,pos)
            reserve=2 if pos+length<len(events) else 0
            if len(chunk)+length+reserve>capacity:
                break
            chunk.extend(events[pos:pos+length])
            pos+=length
        if not chunk:
            raise ValueError("event record does not fit in bank")
        if pos<len(events):
            chunk.extend(BANK_MARKER)
            chunk.extend(b"\0"*(capacity-len(chunk)))
            chunks.append(bytes(chunk))
            capacity=BANK_SIZE
        else:
            chunks.append(bytes(chunk))
    return chunks


def float5(n):
    if n==0: return b"\0"*5
    e=n.bit_length()
    mant=round((n/(1<<(e-1))-1)*(1<<31))
    return bytes((e+128,))+mant.to_bytes(4,"big")

TOK={"LOAD":0xef,"CODE":0xaf,"RANDOMIZE":0xf9,"USR":0xc0,"SCREEN$":0xaa,"PAUSE":0xf2,"CLEAR":0xfd}
def basic_loader(name="MUSIC", has_image=False, bank_count=0,
                 bank_reset_address=BASE+BANK_RESET_OFFSET,
                 bank_next_address=BASE+BANK_NEXT_OFFSET,
                 screen_backup_address=BASE+SCREEN_BACKUP_OFFSET,
                 screen_restore_address=BASE+SCREEN_RESTORE_OFFSET):
    def num(n): return str(n).encode()+b"\x0e"+float5(n)
    lines=[]
    filename=name[:10].encode("ascii")
    bodies=[]
    # Keep the BASIC stack below both C000 and the player scratch area at 32600.
    # CLEAR 32767 left only 148 bytes and repeated LOAD/USR calls could
    # overwrite BANK_STATE, causing the next page operation to select garbage.
    bodies.append(bytes((TOK["CLEAR"],))+b" "+num(BASIC_RAMTOP))
    # Load the fixed player first so its screen-copy routines are available.
    bodies.append(bytes((TOK["LOAD"],))+b' "'+filename+b'" '+bytes((TOK["CODE"],)))
    if has_image:
        bodies.append(bytes((TOK["LOAD"],))+b' "'+filename+b'" '+bytes((TOK["SCREEN$"],)))
        if bank_count:
            bodies.append(bytes((TOK["RANDOMIZE"],))+b" "+bytes((TOK["USR"],))+b" "+num(screen_backup_address))
    if bank_count:
        bodies.append(bytes((TOK["RANDOMIZE"],))+b" "+bytes((TOK["USR"],))+b" "+num(bank_reset_address))
        bodies.append(bytes((TOK["LOAD"],))+b' "'+filename+b'" '+bytes((TOK["CODE"],)))
        for _ in range(1,bank_count):
            bodies.append(bytes((TOK["RANDOMIZE"],))+b" "+bytes((TOK["USR"],))+b" "+num(bank_next_address))
            bodies.append(bytes((TOK["LOAD"],))+b' "'+filename+b'" '+bytes((TOK["CODE"],)))
    if has_image and bank_count:
        bodies.append(bytes((TOK["RANDOMIZE"],))+b" "+bytes((TOK["USR"],))+b" "+num(screen_restore_address))
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
def tap(name, code_chunks, screen=None):
    name="".join(ch if 32<=ord(ch)<127 else "_" for ch in str(name).upper())[:10] or "MUSIC"
    if not code_chunks:
        raise ValueError("at least one code chunk is required")
    if len(code_chunks)>6:
        raise ValueError("128K TAP supports at most five safe bank chunks")
    basic=basic_loader(name, has_image=screen is not None,
                       bank_count=len(code_chunks)-1)
    def header(kind,length,param1,param2):
        return bytes((0,kind))+name[:10].encode("ascii").ljust(10,b" ")+struct.pack("<HH",length,param1)+struct.pack("<H",param2)
    result=bytearray()
    result+=block(header(0,len(basic),10,len(basic))); result+=block(b"\xff"+basic)
    # The fixed player must precede the artwork so the loader can call its
    # screen-backup routine before loading the remaining bank chunks.
    code=code_chunks[0]
    if len(code)>0x10000-BASE: raise ValueError("fixed code chunk is too large")
    result+=block(header(3,len(code),BASE,len(code))); result+=block(b"\xff"+code)
    if screen is not None:
        if len(screen)!=SCREEN_LEN: raise ValueError(f"screen must be {SCREEN_LEN} bytes")
        result+=block(header(3,len(screen),SCREEN_ADDR,len(screen))); result+=block(b"\xff"+screen)
    for code in code_chunks[1:]:
        if len(code)>BANK_SIZE: raise ValueError("bank code chunk is too large")
        result+=block(header(3,len(code),BANK_LOAD_ADDR,len(code))); result+=block(b"\xff"+code)
    return bytes(result)

def build(ay_path,out_path,name=None,image_path=None,visual="scope"):
    name=name or Path(out_path).stem
    if visual not in VISUAL_IDS:
        raise ValueError("visual must be scope, bars, pulse, colour or demo")
    initial_visual=VISUAL_IDS[visual]
    raw=Path(ay_path).read_bytes()
    frames=[raw[i:i+14] for i in range(0,len(raw)-1,14) if len(raw[i:i+14])==14]
    events=compress(frames)
    player_len=len(player(0,initial_visual,name))
    data_address=BASE+((player_len+0xff)//0x100)*0x100
    chunks=split_events(events, BANK_LOAD_ADDR-data_address)
    code0=bytearray(player(data_address,initial_visual,name))
    code0.extend(b"\0"*(data_address-(BASE+len(code0))))
    code0.extend(chunks[0])
    if len(chunks)>1 and len(code0)!=BANK_LOAD_ADDR-BASE:
        raise ValueError("first bank chunk was not padded to C000")
    if len(chunks)>6:
        raise ValueError("music needs more than the five safe extra RAM banks")
    code_chunks=[bytes(code0)]+chunks[1:]
    screen=None
    if image_path is not None:
        import spectrum_screen, tempfile
        with tempfile.NamedTemporaryFile(suffix=".scr") as tmp:
            spectrum_screen.convert(image_path, tmp.name)
            screen=Path(tmp.name).read_bytes()
    Path(out_path).write_bytes(tap(name,code_chunks,screen))
    print(f"wrote {out_path}: {len(code0)} byte fixed code/data, {len(events)} byte events, {len(code_chunks)-1} bank chunks"
          +(f", {len(screen)} byte screen" if screen else ""))


if __name__=="__main__":
    import argparse
    p=argparse.ArgumentParser(); p.add_argument("ay"); p.add_argument("tap")
    p.add_argument("--name", help="Spectrum tape name (defaults to output filename)")
    p.add_argument("--image", help="PNG/JPG artwork to show before playback")
    p.add_argument("--visual",choices=tuple(VISUAL_IDS),default="scope")
    a=p.parse_args(); build(a.ay,a.tap,name=a.name,image_path=a.image,visual=a.visual)
