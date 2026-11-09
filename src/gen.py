"""Generate the assembly source of GBinGB: a Game Boy emulator that runs on a Game Boy.

The host (this program) lives entirely in ROM bank 0.  The guest's ROM is the
rest of the cartridge, mapped a bank at a time into $4000-$7FFF.  See README.md
for the design; the comments in the generated source describe each part.
"""

from __future__ import annotations

# ---------------------------------------------------------------- constants
GBASE = 2  # first cartridge bank of the guest ROM (bank 0 and 1 are the host's)
HOSTSP = 0xD000
XBP = 0xC1  # page of the single-step buffer (it may spill into the next page)
STUBS = 0xC000
TRAP = 0xFD  # an opcode the CPU does not have; ends the single-step buffer
LINE = 114  # machine cycles per scanline

EQUATES = f"""
GBASE   equ {GBASE}
HOSTSP  equ ${HOSTSP:04X}
XBP     equ ${XBP:02X}
TRAP    equ ${TRAP:02X}
LINE    equ {LINE}
; ---- hardware registers
rP1     equ $FF00
rSB     equ $FF01
rSC     equ $FF02
rIF     equ $FF0F
rLCDC   equ $FF40
rSTAT   equ $FF41
rLY     equ $FF44
rDMA    equ $FF46
rIE     equ $FFFF
; ---- host variables (HRAM).  The guest's registers sit in the order POP reads them.
greg    equ $FF82
gF      equ $FF82
gA      equ $FF83
gC      equ $FF84
gB      equ $FF85
gE      equ $FF86
gD      equ $FF87
gL      equ $FF88
gH      equ $FF89
gSP     equ $FF8A   ; 2 bytes
padd    equ $FF8C   ; guest PC high byte = D + padd
hBASE   equ $FF8D   ; first host page of the region the guest PC is in ($FF: single-stepping)
hEND    equ $FF8E   ; host page just past that region
pcbank  equ $FF8F   ; cartridge bank that must be mapped at $4000 for fetches
cd      equ $FF90   ; machine cycles to the next event, minus 1
evspan  equ $FF91   ; machine cycles from the last time sync to that event
gIME    equ $FF92
gIF     equ $FF93
gIE     equ $FF94
eist    equ $FF95   ; EI delay: 1 = EI just ran, 2 = enable after this instruction
vLY     equ $FF96
linerem equ $FF97   ; machine cycles left in the current scanline (1..LINE)
vLCDC   equ $FF98
vSTAT   equ $FF99   ; the guest's STAT interrupt enables (bits 3-6)
vLYC    equ $FF9A
vclk    equ $FF9B   ; 2 bytes: machine cycles; DIV is bits 6-13
tima    equ $FF9D
tma     equ $FF9E
tac     equ $FF9F
gbank   equ $FFA0   ; cartridge bank of the guest's switchable ROM bank
grambank equ $FFA1  ; cartridge RAM bank of the guest's cartridge RAM
gramen  equ $FFA2   ; guest cartridge RAM enabled (and present)
gmbc    equ $FFA3   ; guest mapper: 0 none, 1 MBC1, 3 MBC3, 5 MBC5
m1lo    equ $FFA4
m1hi    equ $FFA5
m1mode  equ $FFA6
tmp     equ $FFA7
gsc     equ $FFA8   ; guest serial control
sercnt  equ $FFA9   ; events until a serial transfer completes (0 = idle)
gdma    equ $FFAA
forced  equ $FFAB   ; an event is wanted at the end of the running instruction
dmastub equ $FFF0   ; OAM DMA must run from HRAM
; ---- RAM stubs (copied from ROM at start; the opcode byte is patched)
cbstub  equ ${STUBS:04X}
cbstub2 equ ${STUBS + 5:04X}
spstub1 equ ${STUBS + 10:04X}
spstub2 equ ${STUBS + 15:04X}
"""

REG = {"b": "gB", "c": "gC", "d": "gD", "e": "gE", "h": "gH", "l": "gL", "a": "gA"}
R8 = ["b", "c", "d", "e", "h", "l", "[hl]", "a"]
PAIR_LO = {"bc": "gC", "de": "gE", "hl": "gL", "sp": "gSP"}
ALU = ["add a,", "adc a,", "sub ", "sbc a,", "and ", "xor ", "or ", "cp "]

FETCH = ["ld a,[de]", "inc e", "call z,xcross"]
SKIP = ["inc e", "call z,xcross"]
GHL = ["ld hl,gL", "ld a,[hl+]", "ld h,[hl]", "ld l,a"]  # HL = guest HL


def pair_to_hl(lo: str) -> list[str]:
    return [f"ld hl,{lo}", "ld a,[hl+]", "ld h,[hl]", "ld l,a"]


def handlers() -> dict[int, list[str]]:
    """Assembly for each opcode's handler.  On entry DE = host pointer just past
    the opcode, B = the opcode.  A handler ends with `jp nextN`, N = the
    instruction's length in machine cycles."""
    h: dict[int, list[str]] = {}
    h[0x00] = ["jp next1"]
    for i, pr in enumerate(["bc", "de", "hl", "sp"]):
        lo = PAIR_LO[pr]
        h[0x01 + 16 * i] = [*FETCH, f"ldh [{lo}],a", *FETCH, f"ldh [{lo}+1],a", "jp next3"]
        h[0x03 + 16 * i] = [f"ld hl,{lo}", "inc [hl]", "jr nz,.s", "inc l", "inc [hl]", ".s:", "jp next2"]
        h[0x0B + 16 * i] = [f"ld hl,{lo}", "ld a,[hl]", "dec [hl]", "or a", "jr nz,.s", "inc l", "dec [hl]", ".s:",
                            "jp next2"]  # fmt: skip
        # ADD HL,rr: Z is kept, so run it natively with the guest's flags loaded
        src = {"bc": ["pop bc"], "de": ["ld sp,greg+4", "pop bc"], "hl": ["ld sp,greg+6", "pop bc"],
               "sp": ["ld hl,gSP", "ld c,[hl]", "inc hl", "ld b,[hl]"]}[pr]  # fmt: skip
        h[0x09 + 16 * i] = ["ld sp,greg", "pop af", *src, "ld sp,greg+6", "pop hl", "add hl,bc", "push hl",
                            "ld sp,greg+2", "push af", "ld sp,HOSTSP", "jp next2"]  # fmt: skip
    for op, pr in ((0x02, "gC"), (0x12, "gE")):
        h[op] = [*pair_to_hl(pr), "ldh a,[gA]", "call gwrite", "jp next2"]
        h[op + 8] = [*pair_to_hl(pr), "call gread", "ldh [gA],a", "jp next2"]
    inc_hl = ["ld a,l", "inc a", "ldh [gL],a", "jr nz,.s", "ld a,h", "inc a", "ldh [gH],a", ".s:"]
    dec_hl = ["ld a,l", "or a", "jr nz,.s", "ld a,h", "dec a", "ldh [gH],a", ".s:", "ld a,l", "dec a", "ldh [gL],a"]
    h[0x22] = [*GHL, *inc_hl, "ldh a,[gA]", "call gwrite", "jp next2"]
    h[0x32] = [*GHL, *dec_hl, "ldh a,[gA]", "call gwrite", "jp next2"]
    h[0x2A] = [*GHL, *inc_hl, "call gread", "ldh [gA],a", "jp next2"]
    h[0x3A] = [*GHL, *dec_hl, "call gread", "ldh [gA],a", "jp next2"]
    for i, r in enumerate(R8):
        for op, ins in ((0x04, "inc"), (0x05, "dec")):
            if r == "[hl]":
                h[op + 8 * i] = [*GHL, "push hl", "call gread", "ldh [tmp],a", "ld sp,greg", "pop af", "ld hl,tmp",
                                 f"{ins} [hl]", "push af", "ld sp,HOSTSP-2", "pop hl", "ldh a,[tmp]", "ld c,3",
                                 "call gwrite_c",
                                 "jp next3"]  # fmt: skip
            elif r == "a":
                h[op + 8 * i] = ["ld sp,greg", "pop af", f"{ins} a", "jp tail_af1"]
            else:
                h[op + 8 * i] = ["ld sp,greg", "pop af", f"ld hl,{REG[r]}", f"{ins} [hl]", "jp tail_af1"]
        if r == "[hl]":
            h[0x36] = [*FETCH, "ld b,a", *GHL, "ld a,b", "ld c,3", "call gwrite_c", "jp next3"]
        else:
            h[0x06 + 8 * i] = [*FETCH, f"ldh [{REG[r]}],a", "jp next2"]
    for op, ins in ((0x07, "rlca"), (0x0F, "rrca"), (0x17, "rla"), (0x1F, "rra"), (0x27, "daa"), (0x2F, "cpl"),
                    (0x37, "scf"), (0x3F, "ccf")):  # fmt: skip
        h[op] = ["ld sp,greg", "pop af", ins, "jp tail_af1"]
    h[0x08] = [*FETCH, "ld l,a", *FETCH, "ld h,a", "push hl", "ldh a,[gSP]", "call gwrite", "pop hl", "inc hl",
               "ldh a,[gSP+1]", "call gwrite", "jp next5"]  # fmt: skip
    h[0x10] = [*SKIP, "jp next1"]  # STOP: treated as a two-byte no-op
    h[0x18] = ["jp jr_go"]
    tests = {  # jump to .nt when the condition is false
        "nz": ["ldh a,[gF]", "add a", "jr c,.nt"],
        "z": ["ldh a,[gF]", "add a", "jr nc,.nt"],
        "nc": ["ldh a,[gF]", "and $10", "jr nz,.nt"],
        "c": ["ldh a,[gF]", "and $10", "jr z,.nt"],
    }
    for i, cc in enumerate(["nz", "z", "nc", "c"]):
        h[0x20 + 8 * i] = [*tests[cc], "jp jr_go", ".nt:", *SKIP, "jp next2"]
        h[0xC0 + 8 * i] = [*tests[cc], "jp ret_go5", ".nt:", "jp next2"]
        h[0xC2 + 8 * i] = [*tests[cc], "jp jp_go", ".nt:", *SKIP, *SKIP, "jp next3"]
        h[0xC4 + 8 * i] = [*tests[cc], "jp call_go", ".nt:", *SKIP, *SKIP, "jp next3"]
    for d, rd in enumerate(R8):
        for s, rs in enumerate(R8):
            op = 0x40 + 8 * d + s
            if op == 0x76:
                h[op] = ["jp halt_go"]
            elif rs == "[hl]":
                h[op] = [*GHL, "call gread", f"ldh [{REG[rd]}],a", "jp next2"]
            elif rd == "[hl]":
                h[op] = [*GHL, f"ldh a,[{REG[rs]}]", "call gwrite", "jp next2"]
            elif rd == rs:
                h[op] = ["jp next1"]
            else:
                h[op] = [f"ldh a,[{REG[rs]}]", f"ldh [{REG[rd]}],a", "jp next1"]
    for a, ins in enumerate(ALU):
        for s, rs in enumerate(R8):
            op = 0x80 + 8 * a + s
            if rs == "[hl]":
                h[op] = [*GHL, "call gread", "ld b,a", "ld sp,greg", "pop af", f"{ins}b", "jp tail_af2"]
            elif rs == "a":
                h[op] = ["ld sp,greg", "pop af", f"{ins}a", "jp tail_af1"]
            else:
                h[op] = ["ld sp,greg", "pop af", f"ld hl,{REG[rs]}", f"{ins}[hl]", "jp tail_af1"]
        h[0xC6 + 8 * a] = [*FETCH, "ld b,a", "ld sp,greg", "pop af", f"{ins}b", "jp tail_af2"]
    for i, (lo, hi) in enumerate((("gC", "gB"), ("gE", "gD"), ("gL", "gH"), ("gF", "gA"))):
        mask = ["and $F0"] if lo == "gF" else []
        h[0xC1 + 16 * i] = ["call gpop_bc", "ld a,c", *mask, f"ldh [{lo}],a", "ld a,b", f"ldh [{hi}],a", "jp next3"]
        h[0xC5 + 16 * i] = [f"ldh a,[{lo}]", "ld c,a", f"ldh a,[{hi}]", "ld b,a", "call gpush_bc", "jp next4"]
    h[0xC3] = ["jp jp_go"]
    h[0xC9] = ["jp ret_go4"]
    h[0xCD] = ["jp call_go"]
    h[0xD9] = ["ld a,1", "ldh [gIME],a", "xor a", "ldh [eist],a", "call gpop_bc", "ld h,b", "ld l,c", "call setpc",
               "call force", "jp next4"]  # fmt: skip
    for i in range(8):
        h[0xC7 + 8 * i] = [f"ld l,{8 * i}", "jp rst_go"]
    h[0xCB] = ["jp cb_go"]
    h[0xE0] = [*FETCH, "ld l,a", "cp $80", "jr c,.io", "inc a", "jr z,.io", "ld h,$DF", "ldh a,[gA]", "ld [hl],a",
               "jp next3", ".io:", "ld h,$FF", "ldh a,[gA]", "ld c,3", "call gwrite_c", "jp next3"]  # fmt: skip
    h[0xF0] = [*FETCH, "ld l,a", "cp $80", "jr c,.io", "inc a", "jr z,.io", "ld h,$DF", "ld a,[hl]", "ldh [gA],a",
               "jp next3", ".io:", "ld h,$FF", "ld c,3", "call gread_c", "ldh [gA],a", "jp next3"]  # fmt: skip
    h[0xE2] = ["ldh a,[gC]", "ld l,a", "ld h,$FF", "ldh a,[gA]", "call gwrite", "jp next2"]
    h[0xF2] = ["ldh a,[gC]", "ld l,a", "ld h,$FF", "call gread", "ldh [gA],a", "jp next2"]
    h[0xEA] = [*FETCH, "ld l,a", *FETCH, "ld h,a", "ldh a,[gA]", "ld c,4", "call gwrite_c", "jp next4"]
    h[0xFA] = [*FETCH, "ld l,a", *FETCH, "ld h,a", "ld c,4", "call gread_c", "ldh [gA],a", "jp next4"]
    h[0xE9] = [*GHL, "call setpc", "jp next1"]
    # ADD SP,e and LD HL,SP+e set flags from the low-byte addition: run LD HL,SP+e
    # natively on the guest's SP (the operand byte is patched into a RAM stub)
    load_sp = ["ld hl,gSP", "ld a,[hl+]", "ld h,[hl]", "ld l,a", "ld sp,hl"]
    h[0xF8] = [*FETCH, "ld [spstub1+1],a", *load_sp, "jp spstub1"]
    h[0xE8] = [*FETCH, "ld [spstub2+1],a", *load_sp, "jp spstub2"]
    h[0xF9] = ["ldh a,[gL]", "ldh [gSP],a", "ldh a,[gH]", "ldh [gSP+1],a", "jp next2"]
    h[0xF3] = ["xor a", "ldh [gIME],a", "ldh [eist],a", "jp next1"]
    h[0xFB] = ["ld a,1", "ldh [eist],a", "call force", "jp next1"]
    h[TRAP] = ["jp trap"]
    return h


CORE = r"""
; ======================================================================
; Cartridge header
; ======================================================================
        org $0100
        nop
        jp start
        org $0104
logo:   db $CE,$ED,$66,$66,$CC,$0D,$00,$0B,$03,$73,$00,$83,$00,$0C,$00,$0D
        db $00,$08,$11,$1F,$88,$89,$00,$0E,$DC,$CC,$6E,$E6,$DD,$DD,$D9,$99
        db $BB,$BB,$67,$63,$6E,$0E,$EC,$CC,$DD,$DC,$99,$9F,$BB,$B9,$33,$3E
        db "GBINGB"
        ds 10, 0
        db $00, $00, $00        ; licensee, SGB flag
        db $1B                  ; MBC5 + RAM + battery
        db $00                  ; ROM size  (patched by mkrom.py)
        db $04                  ; RAM size: 128 KB
        db $01, $33, $00        ; destination, licensee, version
        db $00, $00, $00        ; checksums (patched by mkrom.py)

; ---- guest description, patched by mkrom.py
        org $0150
cfg_mbc:     db 0               ; 0 none, 1 MBC1, 3 MBC3, 5 MBC5
cfg_rommask: db 1               ; guest ROM bank count - 1
cfg_hasram:  db 0
cfg_rammask: db 0               ; guest RAM bank count - 1

; ======================================================================
; Start: the real machine is in its post-boot state.  Interrupts stay off for
; good: the guest's interrupts are delivered by the host in guest time.
; ======================================================================
start:  di
        ; The guest inherits what the boot ROM left: the CPU registers (which tell
        ; a program what model it is running on), work RAM and HRAM.
        ld [$C008],sp
        ld sp,$C008
        push hl
        push de
        push bc
        push af                 ; $C000: F A C B E D L H, then SP
        xor a
        ldh [rIE],a
        ld [$3000],a
        ld [$4000],a            ; cartridge RAM bank 0 = the guest's work RAM
        ld a,$0A
        ld [$0000],a            ; enable cartridge RAM
        ld hl,$C000             ; work RAM -> guest work RAM
        ld de,$A000
.c1:    ld a,[hl+]
        ld [de],a
        inc de
        ld a,h
        cp $E0
        jr nz,.c1
        ld hl,$C00A             ; clear our own RAM (not the saved registers)
.c2:    xor a
        ld [hl+],a
        ld a,h
        cp $E0
        jr nz,.c2
        ld hl,$FF80             ; HRAM -> guest HRAM image at $DF80, then clear ours
        ld de,$DF80
.c3:    ld a,[hl]
        ld [de],a
        xor a
        ld [hl+],a
        inc e
        ld a,l
        cp $FF
        jr nz,.c3
        ld hl,$C000             ; the saved registers become the guest's
        ld de,greg
        ld b,10
.c6:    ld a,[hl]
        ld [de],a
        xor a
        ld [hl+],a
        inc e
        dec b
        jr nz,.c6
        ld hl,$A000             ; and are not part of the guest's work RAM
        ld b,10
.c7:    xor a
        ld [hl+],a
        dec b
        jr nz,.c7
        ld sp,HOSTSP
        ld hl,stub_image        ; RAM stubs
        ld de,cbstub
        ld b,stub_end-stub_image
.c4:    ld a,[hl+]
        ld [de],a
        inc de
        dec b
        jr nz,.c4
        ld hl,dma_image         ; OAM DMA routine in HRAM
        ld de,dmastub
        ld b,dma_end-dma_image
.c5:    ld a,[hl+]
        ld [de],a
        inc de
        dec b
        jr nz,.c5
        ld a,[cfg_mbc]
        ldh [gmbc],a
        ld a,GBASE+1
        ldh [gbank],a
        ld a,1
        ldh [grambank],a
        ldh [m1lo],a
        ldh a,[rLCDC]
        ldh [vLCDC],a
        ld a,LINE
        ldh [linerem],a
        ld a,$7E
        ldh [gsc],a
        ld a,$AB
        ldh [vclk+1],a
        call schedule0
        ld hl,$0100
        call setpc
        jp next

stub_image:
        db $CB,$00
        jp tail_af2
        db $CB,$00
        jp cbmem_ret
        db $F8,$00
        jp f8_ret
        db $F8,$00
        jp e8_ret
stub_end:
dma_image:
        ldh [rDMA],a
        ld a,$28
.w:     dec a
        jr nz,.w
        ret
dma_end:

; ======================================================================
; The dispatch loop.  DE = host pointer to the guest's next instruction byte.
; nextN accounts N machine cycles of guest time, then fetches and dispatches.
; `cd` holds the cycles to the next event minus one, so the subtraction
; borrows exactly when the event is due at or before the end of this instruction.
; ======================================================================
@NEXTS@

; A fetch crossed a 256-byte page.  At the end of a region, translate the guest
; PC again (bank 0 of the ROM runs on into the switchable bank).  Keeps A, BC, HL.
xcross: inc d
        push af
        ldh a,[hEND]
        cp d
        jr z,.re
        pop af
        ret
.re:    push hl
        push bc
        ldh a,[padd]
        add d
        ld h,a
        ld l,0
        call setpc
        pop bc
        pop hl
        pop af
        ret

; ---- an event is due: A = cd after the subtraction (-1 - overshoot)
event:  cpl
        ld b,a                  ; cycles past the event
        ldh a,[evspan]
        add b
        call advance
        xor a
        ldh [forced],a
        call schedule0
        ldh a,[eist]
        or a
        jr z,.noei
        dec a
        jr nz,.ei2
        ld a,2                  ; EI just ran: enable after the next instruction
        ldh [eist],a
        call force
        jp next
.ei2:   xor a
        ldh [eist],a
        inc a
        ldh [gIME],a
.noei:  ldh a,[gIME]
        or a
        jp z,next
        ldh a,[gIF]
        ld hl,gIE
        and [hl]
        and $1F
        jp z,next
        ; ---- deliver the lowest pending interrupt
        ld b,a
        ld c,$40
        ld l,1
.find:  ld a,b
        and l
        jr nz,.got
        sla l
        ld a,c
        add 8
        ld c,a
        jr .find
.got:   ld a,l
        cpl
        ld l,a
        ldh a,[gIF]
        and l
        ldh [gIF],a
        xor a
        ldh [gIME],a
        ld a,c
        push af
        ldh a,[padd]
        add d
        ld b,a
        ld c,e
        call gpush_bc
        pop af
        ld l,a
        ld h,0
        call setpc
        jp next5

; Make the event fire at the end of the instruction now running.
force:  ld a,1
        ldh [forced],a
force_apply:
        ldh a,[cd]
        ld b,a
        ldh a,[evspan]
        sub b
        ldh [evspan],a
        xor a
        ldh [cd],a
        ret

; Bring guest time up to the instruction now running plus C cycles.
; Keeps C and DE.
sync:   ldh a,[gIF]
        push af
        ldh a,[cd]
        ld b,a
        ldh a,[evspan]
        sub b
        dec a
        add c
        call advance
        call schedule_c
        pop af
        ld b,a
        ldh a,[gIF]
        cp b
        ret z
        jr force                ; an interrupt was raised: deliver it after this instruction

; Choose the next event: the end of the scanline, a timer overflow, or 240
; cycles, whichever comes first.  C = cycles of the running instruction that
; have already been counted (0 between instructions).  Keeps C and DE.
schedule0:
        ld c,0
schedule_c:
        ld b,240
        ldh a,[vLCDC]
        add a
        jr nc,.nolcd
        ldh a,[linerem]
        cp b
        jr nc,.nolcd
        ld b,a
.nolcd: ldh a,[tac]
        bit 2,a
        jr z,.done
        and 3
        jr z,.p256
        ; period 4, 16 or 64 cycles: (256 - tima) * period - phase
        add a                   ; shift count 2, 4, 6
        ld l,a
        ldh a,[tima]
        cpl
        inc a                   ; 256 - tima (0 means 256: far away)
        jr z,.done
.sh:    add a
        jr c,.done              ; 256 cycles or more away
        dec l
        jr nz,.sh
        ld h,a                  ; cycles from a period boundary
        call timer_phase        ; A = cycles into the current period
        ld l,a
        ld a,h
        sub l
        cp b
        jr nc,.done
        ld b,a
        jr .done
.p256:  ldh a,[tima]
        inc a
        jr nz,.done
        ldh a,[vclk]
        cpl
        inc a                   ; 256 - low byte
        jr z,.done
        cp b
        jr nc,.done
        ld b,a
.done:  ld a,b
        ldh [evspan],a
        add c
        dec a
        ldh [cd],a
        ldh a,[forced]
        or a
        ret z
        jp force_apply

; A = cycles since the timer's last tick (the low bits of the cycle counter)
timer_phase:
        ldh a,[tac]
        and 3
        jr z,.p256
        dec a
        jr z,.p4
        dec a
        jr z,.p16
        ldh a,[vclk]
        and 63
        ret
.p16:   ldh a,[vclk]
        and 15
        ret
.p4:    ldh a,[vclk]
        and 3
        ret
.p256:  ldh a,[vclk]
        ret

; Apply A machine cycles of guest time: the timer, DIV, the scanline counter
; and the interrupts they raise.  Keeps C and DE.
advance:
        or a
        ret z
        ld b,a
        push bc
        ldh a,[sercnt]          ; a serial transfer completes after a few events
        or a
        jr z,.noser
        dec a
        ldh [sercnt],a
        jr nz,.noser
        ldh a,[gsc]
        and $7F
        ldh [gsc],a
        ldh a,[gIF]
        or 8
        ldh [gIF],a
.noser: ldh a,[tac]
        bit 2,a
        jr z,.notimer
        call timer_phase
        ld l,a
        ld h,0
        ld c,b
        ld b,0
        add hl,bc               ; phase + elapsed
        ldh a,[tac]
        and 3
        jr nz,.small
        ld l,h                  ; period 256: ticks = high byte
        jr .ticks
.small: add a                   ; shift right by 2, 4 or 6
        ld b,a
.shr:   srl h
        rr l
        dec b
        jr nz,.shr
.ticks: ldh a,[tima]
        add l
        jr nc,.tstore
        ld l,a                  ; overflow: reload and raise the interrupt
        ldh a,[gIF]
        or 4
        ldh [gIF],a
        ldh a,[tma]
        add l
.tstore:
        ldh [tima],a
.notimer:
        pop bc
        ldh a,[vclk]
        add b
        ldh [vclk],a
        jr nc,.nc
        ldh a,[vclk+1]
        inc a
        ldh [vclk+1],a
.nc:    ldh a,[vLCDC]
        add a
        ret nc
        ldh a,[linerem]
        sub b
        jr z,.zero
        jr nc,.lstore
.neg:   add LINE
        push af
        call line_event
        pop af
        jr z,.zero
        jr nc,.neg
        jr .lstore
.zero:  call line_event
        ld a,LINE
.lstore:
        ldh [linerem],a
        ret

; A scanline ends.  Keeps BC and DE.
line_event:
        ldh a,[vLY]
        inc a
        cp 154
        jr c,.ok
        xor a
.ok:    ldh [vLY],a
        ld l,a
        cp 144
        jr nz,.notvb
        ldh a,[gIF]
        or 1
        ldh [gIF],a
        ldh a,[vSTAT]
        and $10
        call nz,.stat
        jr .lyc
.notvb: jr nc,.lyc
        ldh a,[vSTAT]
        and $20
        call nz,.stat
.lyc:   ldh a,[vLYC]
        cp l
        ret nz
        ldh a,[vSTAT]
        and $40
        ret z
.stat:  ldh a,[gIF]
        or 2
        ldh [gIF],a
        ret

; ======================================================================
; Guest PC -> host pointer.  HL = guest PC.  Sets DE and the region variables.
; ROM and work RAM are executed in place; anything else one instruction at a
; time from a buffer.  Keeps BC.
; ======================================================================
setpc:  ld a,h
        cp $40
        jr c,.rom0
        cp $80
        jr c,.romn
        cp $C0
        jr c,.slow
        cp $E0
        jr c,.wram
        inc a
        jr nz,.slow
        ld a,l
        cp $80
        jr c,.slow
        ld d,$DF                ; HRAM: its image is at $DF80
        ld e,l
        ld a,$DF
        ldh [hBASE],a
        ld a,$E0
        ldh [hEND],a
        ld a,$20
        ldh [padd],a
        ret
.wram:  sub $20                 ; work RAM is cartridge RAM bank 0 at $A000
        ld d,a
        ld e,l
        ld a,$A0
        ldh [hBASE],a
        ld a,$C0
        ldh [hEND],a
        ld a,$20
        ldh [padd],a
        ret
.rom0:  add $40                 ; guest bank 0, mapped at $4000
        ld d,a
        ld e,l
        ld a,GBASE
        ldh [pcbank],a
        ld [$2000],a
        ld a,$C0
        ldh [padd],a
        jr .romc
.romn:  ld d,a
        ld e,l
        ldh a,[gbank]
        ldh [pcbank],a
        ld [$2000],a
        xor a
        ldh [padd],a
.romc:  ld a,$40
        ldh [hBASE],a
        add a
        ldh [hEND],a
        ret
.slow:  push bc                 ; copy one instruction into the buffer, then a trap
        push hl
        call gread
        ld c,a
        ld b,HIGH(LENTAB)
        ld a,[bc]
        ld b,a                  ; its length
        pop hl
        ld a,h
        sub XBP
        ldh [padd],a
        ld a,$FF
        ldh [hBASE],a
        xor a
        ldh [hEND],a
        ld d,XBP
        ld e,l
        push de
.cp:    push hl
        push bc
        call gread
        pop bc
        pop hl
        ld [de],a
        inc hl
        inc de
        dec b
        jr nz,.cp
        ld a,TRAP
        ld [de],a
        pop de
        pop bc
        ret

; The trap after a single-stepped instruction: translate the PC again.
trap:   ldh a,[hBASE]
        inc a
        jr nz,crash             ; the guest itself executed an undefined opcode
        dec de
        ldh a,[padd]
        add d
        ld h,a
        ld l,e
        call setpc
        jp next
crash:  jr crash                ; the real CPU locks up too

; ======================================================================
; Control flow
; ======================================================================
jr_go:  ld a,[de]
        inc e
        call z,xcross
        ld l,a
        rla
        sbc a
        ld h,a
        add hl,de
        ld a,h
        cp d
        jr nz,.chk
        ldh a,[hBASE]
        inc a
        jr z,.gen               ; single-stepping: always translate
        ld e,l
        jp next3
.chk:   ldh a,[hBASE]
        ld b,a
        ld a,h
        cp b
        jr c,.gen
        ldh a,[hEND]
        ld b,a
        ld a,h
        cp b
        jr nc,.gen
        ld d,h
        ld e,l
        jp next3
.gen:   ldh a,[padd]
        add h
        ld h,a
        call setpc
        jp next3

jp_go:  ld a,[de]
        inc e
        call z,xcross
        ld l,a
        ld a,[de]
        inc e
        call z,xcross
        ld h,a
        call setpc
        jp next4

call_go:
        ld a,[de]
        inc e
        call z,xcross
        ld l,a
        ld a,[de]
        inc e
        call z,xcross
        ld h,a
        push hl
        ldh a,[padd]
        add d
        ld b,a
        ld c,e
        call gpush_bc
        pop hl
        call setpc
        jp next6

rst_go: ld h,0
        push hl
        ldh a,[padd]
        add d
        ld b,a
        ld c,e
        call gpush_bc
        pop hl
        call setpc
        jp next4

ret_go5:
        call gpop_bc
        ld h,b
        ld l,c
        call setpc
        jp next5
ret_go4:
        call gpop_bc
        ld h,b
        ld l,c
        call setpc
        jp next4

; HALT: skip guest time event by event until an enabled interrupt is pending.
halt_go:
        ldh a,[eist]            ; EI then HALT: interrupts are enabled before the wait
        or a
        jr z,.loop0
        xor a
        ldh [eist],a
        inc a
        ldh [gIME],a
.loop0: ld c,0                  ; bring guest time up to this instruction
        call sync
        xor a
        ldh [forced],a
        call schedule0
.loop:  ldh a,[gIF]
        ld hl,gIE
        and [hl]
        and $1F
        jr nz,.wake
        ldh a,[evspan]
        call advance
        call schedule0
        jr .loop
.wake:  call force
        jp next1

; ======================================================================
; CB-prefixed instructions: the native instruction runs on the guest register's
; memory cell (through HL), or on a copy of the guest's memory byte.
; ======================================================================
cb_go:  ld a,[de]
        inc e
        call z,xcross
        ld b,a
        and 7
        cp 6
        jr z,.mem
        cp 7
        jr z,.rega
        xor 1                   ; register number -> its cell: B C D E H L
        add LOW(greg)+2
        ld l,a
        ld h,$FF
        ld a,b
        and $F8
        or 6
        ld [cbstub+1],a
        ld sp,greg
        pop af
        jp cbstub
.rega:  ld a,b
        ld [cbstub+1],a
        ld sp,greg
        pop af
        jp cbstub
.mem:   ld hl,gL
        ld a,[hl+]
        ld h,[hl]
        ld l,a
        push hl
        ld c,3
        call gread_c
        ldh [tmp],a
        ld a,b
        ld [cbstub2+1],a
        ld sp,greg
        pop af
        ld hl,tmp
        jp cbstub2
cbmem_ret:
        push af
        ld sp,HOSTSP-2
        pop hl
        ld a,b
        and $C0
        cp $40
        jp z,next3              ; BIT only reads
        ldh a,[tmp]
        ld c,4
        call gwrite_c
        jp next4

f8_ret: ld sp,greg+8            ; LD HL,SP+e: HL and the flags are the result
        push hl
        ldh a,[gA]
        ld sp,greg+2
        push af
        ld sp,HOSTSP
        jp next3
e8_ret: ldh a,[gA]              ; ADD SP,e
        ld sp,greg+2
        push af
        ld sp,HOSTSP
        ld a,l
        ldh [gSP],a
        ld a,h
        ldh [gSP+1],a
        jp next4

; ======================================================================
; The guest's stack
; ======================================================================
gpush_bc:
        ld hl,gSP+1
        ld a,[hl-]
        cp $C1
        jr c,.slow
        cp $E0
        jr nc,.hi
        sub $20                 ; work RAM
.fast:  ld l,[hl]
        ld h,a
        dec hl
        ld [hl],b
        dec hl
        ld [hl],c
        ld hl,gSP
        ld a,[hl]
        sub 2
        ld [hl+],a
        ret nc
        dec [hl]
        ret
.hi:    inc a
        jr nz,.slow
        ld a,[hl]
        cp $82
        jr c,.slow
        ld a,$DF                ; HRAM image
        jr .fast
.slow:  ld hl,gSP
        ld a,[hl+]
        ld h,[hl]
        ld l,a
        dec hl
        push bc
        push hl
        ld a,b
        call gwrite
        pop hl
        dec hl
        ld a,l
        ldh [gSP],a
        ld a,h
        ldh [gSP+1],a
        pop bc
        ld a,c
        jp gwrite

gpop_bc:
        ld hl,gSP+1
        ld a,[hl-]
        cp $C0
        jr c,.slow
        cp $DF
        jr nc,.hi
        sub $20
.fast:  ld l,[hl]
        ld h,a
        ld c,[hl]
        inc hl
        ld b,[hl]
        ld hl,gSP
        ld a,[hl]
        add 2
        ld [hl+],a
        ret nc
        inc [hl]
        ret
.hi:    inc a
        jr nz,.slow
        ld a,[hl]
        cp $80
        jr c,.slow
        cp $FE
        jr nc,.slow
        ld a,$DF
        jr .fast
.slow:  ld hl,gSP
        ld a,[hl+]
        ld h,[hl]
        ld l,a
        push hl
        call gread
        pop hl
        push af
        inc hl
        push hl
        call gread
        ld b,a
        pop hl
        inc hl
        ld a,l
        ldh [gSP],a
        ld a,h
        ldh [gSP+1],a
        pop af
        ld c,a
        ret

; ======================================================================
; Guest memory.  gread: HL = guest address -> A.  Keeps B and DE.
; C says when in the instruction the access happens, for the timing registers:
; C is the machine cycle of the instruction in which the access happens.  The
; default is the second cycle, right for two-cycle instructions; longer ones
; pass their own.
; ======================================================================
gread:  ld c,2
gread_c:
        ld a,h
        cp $C0
        jr c,.lo
        cp $E0
        jr nc,.hi
        sub $20                 ; work RAM
        ld h,a
        ld a,[hl]
        ret
.lo:    cp $80
        jr c,.rom
        cp $A0
        jr c,.vram
        ldh a,[gramen]          ; cartridge RAM
        or a
        jr z,.open
        ldh a,[grambank]
        ld [$4000],a
        ld l,[hl]
        xor a
        ld [$4000],a
        ld a,l
        ret
.open:  ld a,$FF
        ret
.vram:  ldh a,[rSTAT]           ; the real video RAM: wait until it can be read
        and 2
        jr nz,.vram
        ld a,[hl]
        ret
.rom:   cp $40
        jr c,.rom0
        ldh a,[gbank]
        ld [$2000],a
        jr .romrd
.rom0:  ld a,GBASE
        ld [$2000],a
        set 6,h
.romrd: ld l,[hl]
        ldh a,[pcbank]
        ld [$2000],a
        ld a,l
        ret
.hi:    cp $FE
        jr c,.echo
        jr z,.oam
        ld a,l
        cp $80
        jp c,io_read
        inc a
        jr z,.ie
        ld h,$DF                ; HRAM image
        ld a,[hl]
        ret
.ie:    ldh a,[gIE]
        ret
.echo:  sub $40
        ld h,a
        ld a,[hl]
        ret
.oam:   ld a,l
        cp $A0
        jr nc,.open
        call oam_wait
        ld a,[hl]
        ret

; gwrite: HL = guest address, A = value.  Keeps DE.
gwrite: ld c,2
gwrite_c:
        ld b,a
        ld a,h
        cp $C0
        jr c,.lo
        cp $E0
        jr nc,.hi
        sub $20
        ld h,a
        ld [hl],b
        ret
.lo:    cp $80
        jp c,mbc_write
        cp $A0
        jr c,.vram
        ldh a,[gramen]
        or a
        ret z
        ldh a,[grambank]
        ld [$4000],a
        ld [hl],b
        xor a
        ld [$4000],a
        ret
.vram:  ldh a,[rSTAT]           ; wait for a moment the real video RAM accepts a write
        and 2
        jr nz,.vram
        ld [hl],b
        ret
.hi:    cp $FE
        jr c,.echo
        jr z,.oam
        ld a,l
        cp $80
        jp c,io_write
        inc a
        jr z,.ie
        ld h,$DF
        ld [hl],b
        ret
.ie:    ld a,b
        ldh [gIE],a
        jp force
.echo:  sub $40
        ld h,a
        ld [hl],b
        ret
.oam:   ld a,l
        cp $A0
        ret nc
        call oam_wait
        ld [hl],b
        ret

; Wait until the real sprite memory is accessible with time to spare: the
; display off, or scanlines 144-152.  Keeps everything but A.
oam_wait:
        ldh a,[rLCDC]
        add a
        ret nc
.w:     ldh a,[rLY]
        cp 144
        jr c,.w
        cp 153
        jr nc,.w
        ret

; ---- writes to the ROM area drive the guest's mapper.  A = H, B = value.
mbc_write:
        cp $20
        jr c,.ramen
        cp $40
        jr c,.rombank
        cp $60
        jr c,.rambank
        ldh a,[gmbc]            ; $6000: MBC1 mode
        dec a
        ret nz
        ld a,b
        and 1
        ldh [m1mode],a
        jr .m1
.ramen: ld a,[cfg_hasram]
        or a
        ret z
        ld a,b
        and $0F
        cp $0A
        ld a,0
        jr nz,.re
        inc a
.re:    ldh [gramen],a
        ret
.rombank:
        ldh a,[gmbc]
        or a
        ret z
        cp 5
        jr z,.mbc5
        cp 3
        jr z,.mbc3
        ld a,b
        and $1F
        jr nz,.m1s
        inc a
.m1s:   ldh [m1lo],a
        jr .m1
.mbc3:  ld a,b
        and $7F
        jr nz,.set
        inc a
        jr .set
.mbc5:  ld a,h
        cp $30
        ret nc                  ; the ninth bank bit is not supported
        ld a,b
        jr .set
.rambank:
        ldh a,[gmbc]
        or a
        ret z
        dec a
        jr nz,.rb
        ld a,b
        and 3
        ldh [m1hi],a
.m1:    ldh a,[m1mode]          ; MBC1: RAM bank follows the upper bits in mode 1
        or a
        jr z,.m1r
        ldh a,[m1hi]
.m1r:   ld hl,cfg_rammask
        and [hl]
        inc a
        ldh [grambank],a
        ldh a,[m1hi]
        rrca
        rrca
        rrca
        and $60
        ld l,a
        ldh a,[m1lo]
        or l
.set:   ld hl,cfg_rommask
        and [hl]
        add GBASE
        ldh [gbank],a
        ld b,a
        ldh a,[hBASE]           ; executing from the switchable bank: remap now
        cp $40
        ret nz
        ldh a,[padd]
        or a
        ret nz
        ld a,b
        ldh [pcbank],a
        ld [$2000],a
        ret
.rb:    ld a,b
        ld hl,cfg_rammask
        and [hl]
        inc a
        ldh [grambank],a
        ret

; ======================================================================
; Guest I/O registers.  Timing registers are virtual (guest time); the rest
; (video, sound, joypad, serial data) go to the real hardware.
; io_read: L = register, C = access cycle -> A.  Keeps B and DE.
; ======================================================================
io_read:
        ld a,l
        cp $44
        jr z,.ly
        cp $41
        jr z,.stat
        cp $0F
        jr z,.if
        cp $04
        jr z,.div
        cp $05
        jr z,.tima
        cp $06
        jr z,.tma
        cp $07
        jr z,.tac
        cp $45
        jr z,.lyc
        cp $02
        jr z,.sc
        cp $46
        jr z,.dma
        ld h,$FF
        ld a,[hl]
        ret
.ly:    push bc
        call sync
        pop bc
        ldh a,[vLY]
        ret
.if:    ldh a,[gIF]
        or $E0
        ret
.tma:   ldh a,[tma]
        ret
.tac:   ldh a,[tac]
        or $F8
        ret
.lyc:   ldh a,[vLYC]
        ret
.sc:    ldh a,[gsc]
        ret
.dma:   ldh a,[gdma]
        ret
.tima:  push bc
        call sync
        pop bc
        ldh a,[tima]
        ret
.div:   push bc
        call sync
        pop bc
        ldh a,[vclk]
        ld l,a
        ldh a,[vclk+1]
        ld h,a
        add hl,hl
        add hl,hl
        ld a,h
        ret
.stat:  push bc
        call sync
        ldh a,[vSTAT]
        or $80
        ld b,a
        ldh a,[vLY]
        ld l,a
        ldh a,[vLYC]
        cp l
        jr nz,.nolyc
        set 2,b
.nolyc: ldh a,[vLCDC]
        add a
        jr nc,.sdone            ; display off: mode 0
        ld a,l
        cp 144
        jr c,.vis
        inc b                   ; mode 1
        jr .sdone
.vis:   ldh a,[linerem]
        cp LINE-20+1
        jr c,.m30
        inc b                   ; mode 2
        inc b
        jr .sdone
.m30:   cp LINE-63+1
        jr c,.sdone             ; mode 0
        inc b                   ; mode 3
        inc b
        inc b
.sdone: ld a,b
        pop bc
        ret

; io_write: L = register, B = value, C = access cycle.  Keeps DE.
io_write:
        ld a,l
        cp $46
        jp z,dma
        cp $40
        jr z,.lcdc
        cp $41
        jr z,.stat
        cp $45
        jr z,.lyc
        cp $0F
        jr z,.if
        cp $04
        jr z,.div
        cp $05
        jr z,.tima
        cp $06
        jr z,.tma
        cp $07
        jr z,.tac
        cp $02
        jr z,.sc
        cp $44
        ret z
        cp $50
        ret z
        ld h,$FF
        ld [hl],b
        ret
.stat:  ld a,b
        and $78
        ldh [vSTAT],a
        ret
.lyc:   ld a,b
        ldh [vLYC],a
        ret
.if:    ld a,b
        and $1F
        ldh [gIF],a
        jp force
.tma:   ld a,b
        ldh [tma],a
        ret
.div:   push bc
        call sync
        pop bc
        xor a
        ldh [vclk],a
        ldh [vclk+1],a
        jp schedule_c
.tima:  push bc
        call sync
        pop bc
        ld a,b
        ldh [tima],a
        jp schedule_c
.tac:   push bc
        call sync
        pop bc
        ld a,b
        and 7
        ldh [tac],a
        jp schedule_c
.sc:    ld a,b
        ldh [rSC],a
        or $7E
        ldh [gsc],a
        and $81
        cp $81
        ret nz
        ld a,5                  ; internal clock: the transfer completes shortly
        ldh [sercnt],a
        ret
.lcdc:  push bc
        call sync
        pop bc
        ldh a,[vLCDC]
        xor b
        add a
        jr nc,.same
        ld a,b
        add a
        jr c,.on
.w:     ldh a,[rLY]             ; switch the real display off only in vertical blank
        cp 144
        jr c,.w
        ld a,b
        ldh [rLCDC],a
        ldh [vLCDC],a
        xor a
        ldh [vLY],a
        jp schedule_c
.on:    ld a,b
        ldh [rLCDC],a
        ldh [vLCDC],a
        xor a
        ldh [vLY],a
        ld a,LINE
        ldh [linerem],a
        jp schedule_c
.same:  ld a,b
        ldh [rLCDC],a
        ldh [vLCDC],a
        ret

; The guest starts an OAM DMA from page B: run a real one from the same data,
; at the start of the real vertical blank.
dma:    push de
        ld a,b
        ldh [gdma],a
        cp $40
        jr nc,.n0
        add $40
        ld d,a
        ld a,GBASE
        ld [$2000],a
        jr .go
.n0:    cp $80
        jr nc,.n1
        ld d,a
        ldh a,[gbank]
        ld [$2000],a
        jr .go
.n1:    cp $A0
        jr nc,.n2
        ld d,a
        jr .go
.n2:    cp $C0
        jr nc,.n3
        ld d,a
        ldh a,[grambank]
        ld [$4000],a
        jr .go
.n3:    cp $E0
        jr nc,.n4
        sub $20
        ld d,a
        jr .go
.n4:    sub $40
        ld d,a
.go:    ldh a,[rLCDC]
        add a
        jr nc,.now
.w:     ldh a,[rLY]
        cp 144
        jr nz,.w
.now:   ld a,d
        call dmastub
        ldh a,[pcbank]
        ld [$2000],a
        xor a
        ld [$4000],a
        pop de
        ret
"""


def source() -> str:
    o = ["; GENERATED by gen.py -- GBinGB, a Game Boy emulator for the Game Boy", EQUATES]
    fetch = ["        ld a,[de]", "        inc e", "        jr z,next_x", "        ld l,a", "        ld h,HIGH(TLO)",
             "        ld a,[hl]", "        inc h", "        ld h,[hl]", "        ld l,a", "        jp hl"]
    nexts = []
    for n in (6, 5, 4):
        nexts += [f"next{n}:", "        ldh a,[cd]", f"        sub {n}", "        ldh [cd],a", "        jr c,event_j", *fetch]
    nexts += ["event_j:", "        jp event", "next_x: call xcross", "dispatch:", *fetch[3:]]
    nexts += ["next3:", "        ldh a,[cd]", "        sub 3", "        ldh [cd],a", "        jr c,event_j", *fetch]
    for n in (2, 1):  # the tail of a native ALU operation stores the guest's A and F, then falls through
        nexts += [f"tail_af{n}:", "        push af", "        ld sp,HOSTSP", f"next{n}:", "        ldh a,[cd]",
                  f"        sub {n}", "        ldh [cd],a", "        jr c,event_j", *fetch]
    nexts += ["next:", *fetch]
    o.append(CORE.replace("@NEXTS@", "\n".join(nexts)))
    hs = handlers()
    names = {}
    o.append("; ======================================================================")
    o.append("; One handler per opcode (generated)")
    o.append("; ======================================================================")
    bodies: dict[str, str] = {}
    for op in range(256):
        if op not in hs:
            names[op] = "crash"
            continue
        key = "\n".join(hs[op])
        if key in bodies and not any(line.startswith(".") for line in hs[op]):
            names[op] = bodies[key]
            continue
        names[op] = f"h_{op:02X}"
        bodies[key] = names[op]
        o.append(f"h_{op:02X}:")
        o += [x if x.endswith(":") else "        " + x for x in hs[op]]
    o.append("code_end:")
    o.append("        org $3D00")
    o.append("TLO:")
    for i in range(0, 256, 8):
        o.append("        db " + ", ".join(f"LOW({names[j]})" for j in range(i, i + 8)))
    o.append("THI:")
    for i in range(0, 256, 8):
        o.append("        db " + ", ".join(f"HIGH({names[j]})" for j in range(i, i + 8)))
    o.append("LENTAB:")
    for i in range(0, 256, 16):
        o.append("        db " + ", ".join(str(length(j)) for j in range(i, i + 16)))
    return "\n".join(o) + "\n"


def length(op: int) -> int:
    """Instruction length in bytes (an undefined opcode counts as one)."""
    if op in (0x01, 0x11, 0x21, 0x31, 0x08, 0xC3, 0xCD, 0xEA, 0xFA) or (op & 0xE7) in (0xC2, 0xC4):
        return 3
    if (op & 0xC7) == 0x06 or (op & 0xC7) == 0xC6 or op in (0x10, 0x18, 0x20, 0x28, 0x30, 0x38, 0xCB, 0xE0, 0xF0,
                                                             0xE8, 0xF8):  # fmt: skip
        return 2
    return 1


if __name__ == "__main__":
    print(source())
