; Minimal ZX Spectrum 128 AY frame player.
; Assemble with sjasmplus. Each frame is 14 AY register bytes (R0..R13).
; Call ay_init once, then ay_tick from a 50 Hz interrupt, ay_stop to silence.
AY_SEL EQU 65533
AY_DAT EQU 49149
ORG 32768

ay_init:
  di
  ld hl,frames
  ld (frame_ptr),hl
  xor a
  ld (playing),a
  call ay_stop
  ei
  ret

ay_start:
  ld hl,frames
  ld (frame_ptr),hl
  ld a,1
  ld (playing),a
  ret

ay_stop:
  xor a
  ld (playing),a
  ld hl,zero_regs
  jp ay_write

ay_tick:
  ld a,(playing)
  or a
  ret z
  ld hl,(frame_ptr)
  ld a,(hl)
  cp 255
  jr nz,ay_tick_write
  call ay_stop
  ret
ay_tick_write:
  call ay_write
  ld hl,(frame_ptr)
  ld bc,14
  add hl,bc
  ld (frame_ptr),hl
  ret

ay_write:
  ld e,0
  ld b,14
.loop:
  push bc
  ld a,e
  ld b,255
  ld c,253
  out (c),a
  ld a,(hl)
  ld b,191
  out (c),a
  inc hl
  inc e
  pop bc
  djnz .loop
  ret

playing:   db 0
frame_ptr: dw frames
zero_regs: defs 13,0
           db 0
frames:
  defs 14,0
  db 255
