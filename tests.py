"""GBinGB regression tests, run inside the PyBoy emulator.

  cpu      Blargg's cpu_instrs (11 individual ROMs and the combined one),
           instr_timing and mem_timing run as guests and must print "Passed"
  diff     the demo guest run natively and as a guest must have identical memory
           at the same frames (difftest.py)
  nested   the emulator as its own guest, running 02-interrupts and instr_timing

usage: python3 tests.py [cpu] [diff] [nested] [quick]
The test ROMs are expected in roms/ (see README).
"""
import subprocess, sys, time, warnings
warnings.filterwarnings("ignore")
sys.path.insert(0, "src"); sys.path.insert(0, ".")
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

def guest_test(args):
    name, depth, maxframes = args
    import mkrom
    from harness import run, screen_text
    rom = Path(f"roms/{name}.gb").read_bytes()
    for _ in range(depth):
        rom = mkrom.build(rom)
    p = Path("build") / f"{name}_depth{depth}.gb"
    p.write_bytes(rom)
    t = time.time()
    pb, n = run(str(p), maxframes, lambda pb: any(w in screen_text(pb) for w in ("Passed", "Failed")))
    txt = screen_text(pb)
    ok = "Passed" in txt and "Failed" not in txt
    last = [l.strip() for l in txt.split("\n") if l.strip()][-1:] or ["(nothing printed)"]
    return name, depth, ok, n, last[0], time.time() - t

if __name__ == "__main__":
    what = set(sys.argv[1:]) or {"cpu", "diff", "nested"}
    Path("build").mkdir(exist_ok=True)
    jobs = []
    if "cpu" in what:
        names = ["01", "02", "06", "07", "08", "instr_timing"] if "quick" in what else \
            ["01", "02", "03", "04", "05", "06", "07", "08", "09", "10", "11", "instr_timing", "cpu_instrs",
             "x_01-read_timing", "x_02-write_timing", "x_03-modify_timing"]
        jobs += [(n, 1, 400000) for n in names]
    if "nested" in what:
        jobs += [("02", 2, 400000), ("instr_timing", 2, 400000)]
    bad = 0
    with ProcessPoolExecutor(4) as ex:
        for name, depth, ok, n, last, secs in ex.map(guest_test, jobs):
            print(f"{'PASS' if ok else 'FAIL'}  {name:18s} depth {depth}: {last:22s} {n:7d} frames ({n / 59.7 / 60:5.1f} min on a Game Boy), {secs:.0f} s here")
            bad += not ok
    if "diff" in what:
        r = subprocess.run([sys.executable, "difftest.py"], capture_output=True, text=True)
        out = [l for l in r.stdout.split("\n") if l.strip()]
        print("\n".join("      " + l for l in out[:-1]))
        print(f"{'PASS' if out and out[-1] == 'PASS' else 'FAIL'}  differential test of the demo guest")
        bad += not (out and out[-1] == "PASS")
    print("ALL PASS" if not bad else f"{bad} FAILED")
    sys.exit(bad != 0)
