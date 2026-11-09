"""Differential test: the demo guest run natively and under GBinGB must be in the same
state each time it executes HALT with the same frame count: work RAM, HRAM, video RAM
and sprite memory, byte for byte.  usage: difftest.py [FRAMES...]"""
import sys, warnings
warnings.filterwarnings("ignore")
sys.path.insert(0, "src")
from pathlib import Path
from pyboy import PyBoy
import demo, mkrom

def snapshots(rom_path, hook_addr, frames, emulated, maxticks):
    pb = PyBoy(rom_path, window="null", sound_emulated=False); pb.set_emulation_speed(0)
    want, got = set(frames), {}
    def at_halt(_):
        if emulated:
            wram = bytes(pb.memory[0xA000:0xC000]); hram = bytes(pb.memory[0xDF80:0xDFFF])
        else:
            wram = bytes(pb.memory[0xC000:0xE000]); hram = bytes(pb.memory[0xFF80:0xFFFF])
        f = wram[1] | wram[2] << 8
        if f in want and f not in got:
            got[f] = dict(wram=wram, hram=hram, vram=bytes(pb.memory[0x8000:0xA000]), oam=bytes(pb.memory[0xFE00:0xFEA0]),
                          io=bytes(pb.memory[a] for a in (0xFF40, 0xFF42, 0xFF43, 0xFF47, 0xFF48)), ticks=ticks[0])
    pb.hook_register(0, hook_addr, at_halt, None)
    ticks = [0]
    while len(got) < len(want) and ticks[0] < maxticks:
        pb.tick(1, False); ticks[0] += 1
    return got, pb

if __name__ == "__main__":
    frames = [int(x) for x in sys.argv[1:]] or [1, 2, 3, 10, 50, 200, 300]
    rom, dsym = demo.build_sym()
    Path("build").mkdir(exist_ok=True)
    Path("build/demo.gb").write_bytes(rom)
    Path("build/demo_in_gb.gb").write_bytes(mkrom.build(rom))
    _, hsym = mkrom.host_image()
    # native: the instruction before HALT at the end of a frame; emulated: the host's HALT handler
    nat, _ = snapshots("build/demo.gb", dsym["endframe"], frames, False, 3000)
    emu, pb = snapshots("build/demo_in_gb.gb", hsym["halt_go"], frames, True, 20000)
    pb.screen.image.save("build/demo_emulated.png")
    bad = 0
    for f in frames:
        if f not in nat or f not in emu:
            print(f"frame {f}: missing ({'native' if f not in nat else 'emulated'})"); bad += 1; continue
        diffs = []
        for k in ("wram", "hram", "vram", "oam", "io"):
            a, b = nat[f][k], emu[f][k]
            d = [i for i in range(len(a)) if a[i] != b[i]]
            if d: diffs.append(f"{k}: {len(d)} bytes differ, first at +{d[0]:#x} (native {a[d[0]]:02x}, emulated {b[d[0]]:02x})")
        ratio = emu[f]["ticks"] / max(nat[f]["ticks"], 1)
        print(f"frame {f:4d}: {'IDENTICAL' if not diffs else 'DIFFERENT'}  (native after {nat[f]['ticks']} host frames, emulated after {emu[f]['ticks']})")
        for d in diffs: print("     ", d)
        bad += bool(diffs)
    print("PASS" if not bad else "FAIL")
