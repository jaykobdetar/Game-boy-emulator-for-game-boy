"""Run a guest ROM under GBinGB inside PyBoy and report what the guest printed.
usage: runtest.py GUEST.gb [MAXFRAMES] [PNG]"""
import sys, time, warnings
warnings.filterwarnings("ignore")
sys.path.insert(0, "src")
from pathlib import Path
import mkrom
from harness import run, screen_text

def run_guest(path, maxframes=60000, png=None, quiet=False):
    out = Path("build"); out.mkdir(exist_ok=True)
    rom = out / (Path(path).stem + "_in_gb.gb")
    rom.write_bytes(mkrom.build(Path(path).read_bytes()))
    t = time.time()
    pb, n = run(str(rom), maxframes, lambda pb: any(w in screen_text(pb) for w in ("Passed", "Failed")))
    txt = screen_text(pb)
    if png: pb.screen.image.save(png)
    if not quiet:
        print(f"== {Path(path).name}: {n} frames ({n/59.7:.0f} s of real Game Boy time), {time.time()-t:.1f} s host")
        print(txt)
    return txt, n, pb

if __name__ == "__main__":
    run_guest(sys.argv[1], int(sys.argv[2]) if len(sys.argv) > 2 else 60000, sys.argv[3] if len(sys.argv) > 3 else None)
