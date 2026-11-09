"""The same guests under GBinGB inside mGBA (a second, independent emulator).
usage: python3 mgba_tests.py     (needs /usr/games/mgba; ROMs are built by tests.py)"""
import sys
sys.path.insert(0, "src"); sys.path.insert(0, ".")
from pathlib import Path
import demo, mkrom
from mgba_run import run, text

Path("build").mkdir(exist_ok=True)
cases = [("01", 1, 6), ("02", 1, 4), ("instr_timing", 1, 4), ("x_01-read_timing", 1, 4), ("x_02-write_timing", 1, 4),
         ("x_03-modify_timing", 1, 4), ("cpu_instrs", 1, 40), ("02", 2, 12)]
bad = 0
for name, depth, secs in cases:
    rom = Path(f"roms/{name}.gb").read_bytes()
    for _ in range(depth):
        rom = mkrom.build(rom)
    p = Path("build") / f"mgba_{name}_{depth}.gb"
    p.write_bytes(rom)
    rows = [r.strip() for r in text(run(str(p), secs)) if r.strip()]
    ok = any("Passed" in r for r in rows) and not any("Failed" in r for r in rows)
    print(f"{'PASS' if ok else 'FAIL'}  {name:18s} depth {depth}: {rows[-1] if rows else '(nothing printed)'}")
    bad += not ok
p = Path("build/mgba_demo.gb"); p.write_bytes(mkrom.build(demo.build()))
rows = [r.rstrip() for r in text(run(str(p), 5)) if r.strip()]
ok = any("GB IN GB DEMO" in r for r in rows) and any("FRAME:" in r for r in rows)
print(f"{'PASS' if ok else 'FAIL'}  demo guest draws its screen: {' | '.join(r.strip() for r in rows)}")
bad += not ok
print("ALL PASS" if not bad else f"{bad} FAILED")
sys.exit(bad != 0)
