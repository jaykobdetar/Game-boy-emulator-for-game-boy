"""A small guest program for GBinGB: text, a frame counter, a sprite moved with
the D-pad, a beep on A.  It uses what games use: the VBlank interrupt, HALT,
OAM DMA from HRAM, the joypad, direct video RAM writes.  Runs natively too.

usage: python3 demo.py OUT.gb
"""
from __future__ import annotations

import sys
from pathlib import Path

import font5x7
import sm83asm


def font_bytes() -> list[int]:
    out = []
    for code in range(0x20, 0x60):
        ch = chr(code)
        out += font5x7.rows(ch) if ch in font5x7.GLYPHS else [0] * 8
    return out


SRC = r"""
vbflag  equ $C000
frame   equ $C001       ; 2 bytes
keys    equ $C003
oldkeys equ $C004
spx     equ $C005
spy     equ $C006
oam     equ $C100       ; shadow sprite table
hdma    equ $FF80

        org $0040
        jp vblank
        org $0100
        nop
        jp main
        org $0104
        db $CE,$ED,$66,$66,$CC,$0D,$00,$0B,$03,$73,$00,$83,$00,$0C,$00,$0D
        db $00,$08,$11,$1F,$88,$89,$00,$0E,$DC,$CC,$6E,$E6,$DD,$DD,$D9,$99
        db $BB,$BB,$67,$63,$6E,$0E,$EC,$CC,$DD,$DC,$99,$9F,$BB,$B9,$33,$3E
        db "GBINGB DEMO"
        ds 5, 0
        db 0, 0, 0, 0, 0, 0, 1, $33, 0, 0, 0, 0

        org $0150
main:   di
        ld sp,$E000
.wv:    ldh a,[$44]
        cp 144
        jr c,.wv
        xor a
        ldh [$40],a             ; display off while loading
        ld hl,$C000             ; clear variables and the shadow sprite table
.cl:    xor a
        ld [hl+],a
        ld a,h
        cp $C2
        jr nz,.cl
        ld hl,font              ; font: one bit per pixel -> both bit planes
        ld de,$8200             ; tile number = character code
        ld bc,font_end-font
.cf:    ld a,[hl+]
        ld [de],a
        inc de
        ld [de],a
        inc de
        dec bc
        ld a,b
        or c
        jr nz,.cf
        ld hl,$9800             ; clear the tile map
.cm:    ld a,$20
        ld [hl+],a
        ld a,h
        cp $9C
        jr nz,.cm
        ld hl,msg1
        ld de,$9800+32*1+2
        call print
        ld hl,msg2
        ld de,$9800+32*3+2
        call print
        ld hl,msg3
        ld de,$9800+32*5+2
        call print
        ld hl,msg4
        ld de,$9800+32*16+1
        call print
        ld hl,dmacode           ; the sprite-table copy must run from HRAM
        ld de,hdma
        ld b,dmaend-dmacode
.cd:    ld a,[hl+]
        ld [de],a
        inc de
        dec b
        jr nz,.cd
        ld a,80
        ld [spx],a
        ld [spy],a
        ld a,$E4
        ldh [$47],a
        ldh [$48],a
        ld a,$80                ; sound on
        ldh [$26],a
        ld a,$FF
        ldh [$25],a
        ld a,$77
        ldh [$24],a
        ld a,$93                ; display on: background, sprites
        ldh [$40],a
        ld a,1
        ldh [$FF],a
        ei
endframe:
        nop                     ; (a place for a test harness to look at a finished frame)
loop:   halt
        nop
        ld a,[vbflag]
        or a
        jr z,loop
        xor a
        ld [vbflag],a
        call joypad
        ld a,[keys]             ; D-pad moves the sprite
        ld b,a
        ld hl,spx
        bit 0,b
        jr z,.nr
        inc [hl]
.nr:    bit 1,b
        jr z,.nl
        dec [hl]
.nl:    ld hl,spy
        bit 2,b
        jr z,.nu
        dec [hl]
.nu:    bit 3,b
        jr z,.nd
        inc [hl]
.nd:    ld a,[oldkeys]          ; A pressed: beep
        cpl
        and b
        bit 4,a
        jr z,.nb
        ld a,$80
        ldh [$11],a
        ld a,$F3
        ldh [$12],a
        ld a,$C1
        ldh [$13],a
        ld a,$87
        ldh [$14],a
.nb:    ld a,b
        ld [oldkeys],a
        ld hl,oam               ; one sprite: the letter O
        ld a,[spy]
        ld [hl+],a
        ld a,[spx]
        ld [hl+],a
        ld a,'O'
        ld [hl+],a
        xor a
        ld [hl],a
        ld de,$9800+32*3+9      ; keys as two hex digits
        ld a,[keys]
        call hex2
        ld hl,frame             ; count the frame last: when the count changes,
        inc [hl]                ; everything else for this frame is done
        jr nz,.nc
        inc hl
        inc [hl]
.nc:    ld de,$9800+32*5+9
        ld a,[frame+1]
        call hex2
        ld a,[frame]
        call hex2
        jr endframe

vblank: push af
        push bc
        push de
        push hl
        call hdma
        ld a,1
        ld [vbflag],a
        pop hl
        pop de
        pop bc
        pop af
        reti

joypad: ld a,$20                ; D-pad
        ldh [$00],a
        ldh a,[$00]
        ldh a,[$00]
        cpl
        and $0F
        ld b,a
        ld a,$10                ; buttons
        ldh [$00],a
        ldh a,[$00]
        ldh a,[$00]
        cpl
        and $0F
        swap a
        or b
        ld [keys],a
        ld a,$30
        ldh [$00],a
        ret

print:  ld a,[hl+]              ; zero-terminated text at HL -> tile map at DE
        or a
        ret z
        ld [de],a
        inc de
        jr print

hex2:   push af                 ; A as two hex digits at DE
        swap a
        call .digit
        pop af
.digit: and $0F
        add '0'
        cp '9'+1
        jr c,.put
        add 7
.put:   ld [de],a
        inc de
        ret

dmacode:
        ld a,HIGH(oam)
        ldh [$46],a
        ld a,$28
.w:     dec a
        jr nz,.w
        ret
dmaend:

msg1:   db "GB IN GB DEMO", 0
msg2:   db "KEYS:", 0
msg3:   db "FRAME:", 0
msg4:   db "D-PAD MOVES O", 0
font:
@FONT@
font_end:
"""


def build_sym() -> tuple[bytes, dict]:
    fb = font_bytes()
    rows = "\n".join("        db " + ",".join(f"${b:02X}" for b in fb[i : i + 16]) for i in range(0, len(fb), 16))
    a = sm83asm.assemble(SRC.replace("@FONT@", rows), 0x8000)
    rom = bytearray(a.rom)
    x = 0
    for i in range(0x134, 0x14D):
        x = (x - rom[i] - 1) & 0xFF
    rom[0x14D] = x
    return bytes(rom), a.sym


def build() -> bytes:
    return build_sym()[0]


if __name__ == "__main__":
    Path(sys.argv[1]).write_bytes(build())
