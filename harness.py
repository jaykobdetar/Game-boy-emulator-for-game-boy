"""Run a ROM in PyBoy headless and read what it shows.  usage: harness.py ROM FRAMES"""
import sys, warnings
warnings.filterwarnings("ignore")
from pyboy import PyBoy

def screen_text(pb):
    """Decode the background tile map at $9800 as ASCII (Blargg's tests use tile number = character)."""
    rows = []
    for y in range(18):
        row = "".join(chr(c) if 32 <= c < 127 else " " for c in (pb.memory[0x9800 + 32 * y + x] for x in range(20)))
        rows.append(row.rstrip())
    return "\n".join(r for r in rows if r)

def run(rom, frames, until=None, sound=False):
    pb = PyBoy(rom, window="null", sound_emulated=sound)
    pb.set_emulation_speed(0)
    n = 0
    while n < frames:
        pb.tick(60, False)
        n += 60
        if until and until(pb):
            break
    return pb, n

if __name__ == "__main__":
    pb, n = run(sys.argv[1], int(sys.argv[2]), lambda pb: any(w in screen_text(pb) for w in ("Passed", "Failed")))
    print(f"[{n} frames]"); print(screen_text(pb))
    if len(sys.argv) > 3:
        pb.screen.image.save(sys.argv[3])
