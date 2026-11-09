# GBinGB

A Game Boy emulator that runs on a Game Boy. The cartridge holds the emulator
(5 KB of code in ROM bank 0) followed by a guest ROM; when the console starts,
the emulator runs the guest. A GBinGB cartridge image is itself a valid guest,
so the emulator can run itself.

**Status: verified in two emulators, not yet run on hardware.** See
[What was checked](#what-was-checked) and [Limits](#limits).

## Try it

`prebuilt/gbingb-demo.gb` is the emulator with a small demo program as its
guest (text, a frame counter, a sprite you move with the D-pad, a beep on A).
`prebuilt/gbingb-nested-demo.gb` is the emulator running the emulator running
that demo. Both load in any Game Boy emulator, and should load from a flash
cartridge that supports MBC5 with battery RAM (32 KB for the first, 128 KB for
the second).

To wrap a ROM of your own (Python 3, standard library only):

```sh
cd src
python3 mkrom.py path/to/guest.gb ../out.gb
```

Supported guests: original Game Boy programs up to 2 MB that use no mapper,
MBC1, MBC3 (without the clock) or MBC5.

## How it works

The guest's code is ordinary Game Boy machine code, and so is the host, which
allows three shortcuts an emulator on other hardware does not have.

- **The guest's ROM is read in place.** The cartridge maps one guest bank at a
  time at `$4000`; guest bank 0 is mapped there too when the guest executes or
  reads it. The host itself fits in bank 0 so it is always visible.
- **Instructions that compute run natively.** For an arithmetic, logic, shift or
  bit instruction the host loads the guest's flags and accumulator with one
  `POP AF`, points `HL` at the memory cell holding the guest register, and
  executes the same instruction in its `(HL)` form. The real CPU produces the
  result and the flags; nothing is computed by hand.
- **Video, sound and the joypad are the real ones.** Guest writes to video RAM,
  sprite memory, the palettes, scroll and sound registers go to the hardware
  (the host first waits until the hardware will accept them), so the real
  screen shows the guest's picture.

What is virtual is **time and memory**:

- The guest's work RAM lives in cartridge RAM bank 0 and its HRAM in a copy in
  the host's RAM; the guest's cartridge RAM uses cartridge RAM banks 1 and up.
- Every instruction's length in machine cycles is counted. The scanline
  counter (`LY`), the `STAT` mode, `DIV`, the timer and all interrupts are
  derived from that count, not from the real hardware. The guest therefore
  behaves exactly as it would at full speed, only slower in real time, and the
  result does not depend on how fast the host happens to run.
- `HALT` skips guest time forward to the next interrupt in one step.
- Interrupts are delivered between guest instructions by the host; the real
  machine's interrupts stay off for good.

The guest starts with what the boot ROM left behind: the CPU registers (which
tell a program what model it is on), work RAM and HRAM.

`src/gen.py` generates the emulator's assembly source (one handler per opcode
is produced from a table); `src/sm83asm.py` is the assembler; `src/mkrom.py`
joins emulator and guest and fixes the cartridge header.

## Speed

Measured in the PyBoy emulator: roughly 10 to 45 times slower than native, depending
on the instruction mix (register-to-register loads are cheapest; stack and
memory instructions cost most). Blargg's `cpu_instrs`, which takes 39 seconds
natively, takes 26 minutes. The demo, which idles in `HALT` most of each frame,
runs at about a third of full speed, because idle time is skipped.

Two levels deep the slowdown multiplies: the nested demo draws a frame about
every 100 real frames.

## What was checked

All of this is inside emulators (`python3 tests.py`, `python3 mgba_tests.py`).

| Check | PyBoy | mGBA |
| --- | --- | --- |
| Blargg `cpu_instrs`: 11 individual tests and the combined ROM (MBC1), as guests | pass | 01, 02 and the combined ROM pass |
| Blargg `instr_timing` as a guest: every instruction's length in cycles | pass | pass |
| Blargg `mem_timing` (read, write, modify) as guests: the cycle within an instruction at which memory is accessed | pass | pass |
| The demo guest, native against emulated: work RAM (stack included), HRAM, video RAM, sprite memory and palette/scroll registers byte-identical at frames 1, 2, 3, 10, 50, 200, 300 | pass | not run |
| The demo guest draws its screen and reacts to the joypad | pass | draws its screen |
| Two levels deep (the emulator as its own guest): `02-interrupts` and `instr_timing` pass in the innermost guest | pass | `02-interrupts` passes |
| The assembler's encoding of every valid opcode against PyBoy's opcode table | pass | |

The test ROMs are not included; `python3 get_test_roms.py` downloads them.

## Limits

Not verified:

- **MBC3 guests.** The mapper code is there but no test used it. ROM-only,
  MBC1 and MBC5 guests were exercised (the demo, `cpu_instrs`, and the nested
  runs).
- **Real hardware.** Nothing here has run on a console. The host was written
  for the hardware's rules (video RAM is written only when the display allows
  it, sprite memory only in vertical blank, the display is switched off only
  in vertical blank, sprite DMA runs from HRAM), and mGBA enforces some of
  them, but only a real machine settles it. SameBoy or BGB would be a stricter
  test than the two emulators used.

By design:

- **Speed.** Games run in slow motion; see above.
- **Sound** registers are passed through as the guest writes them, so music
  plays at the guest's slowed tempo.
- **Effects timed to the scanline** (status bars, wavy screens) look wrong: the
  guest changes the registers at the right guest time, but the real screen is
  drawing at its own speed.
- **Game Boy Color-only games** are refused; the emulator is an original Game
  Boy. On a Game Boy Color the cartridge runs in its compatibility mode.

Not implemented:

- the `STAT` interrupt for horizontal blank (mode 0); the vertical blank,
  sprite-search and `LY = LYC` sources are;
- the `HALT` bug, and timing quirks finer than a machine cycle;
- the link cable (the guest's bytes are sent, and a transfer with the internal
  clock completes; nothing is received);
- MBC2, the MBC3 clock, MBC5 rumble, ROMs over 2 MB, and guest cartridge RAM
  beyond 64 KB;
- `STOP` (treated as a two-byte no-op).

## Files

| Path | What it is |
| --- | --- |
| `src/gen.py` | generator for the emulator's assembly source |
| `src/sm83asm.py` | two-pass assembler for the Game Boy CPU |
| `src/mkrom.py` | joins the emulator and a guest ROM into a cartridge image |
| `src/demo.py`, `src/font5x7.py` | the demo guest program and its font |
| `src/check_asm.py` | checks the assembler against PyBoy's opcode table |
| `tests.py`, `difftest.py`, `harness.py`, `runtest.py` | the tests in PyBoy (`pip install pyboy pillow`) |
| `mgba_tests.py`, `mgba_run.py` | the same guests in mGBA, driven through its debugger |
| `get_test_roms.py` | downloads the test ROMs into `roms/` |
| `prebuilt/` | the two demo cartridge images and screenshots |
