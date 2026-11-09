"""Run a ROM in mGBA headless for a number of seconds, then read the background tile map
through its command-line debugger.  usage: mgba_run.py ROM SECONDS"""
import os, re, signal, subprocess, sys, time

def run(rom, seconds):
    env = dict(os.environ, SDL_VIDEODRIVER="dummy", SDL_AUDIODRIVER="dummy", HOME="/tmp/mgba-home")
    os.makedirs("/tmp/mgba-home", exist_ok=True)
    p = subprocess.Popen(["/usr/games/mgba", "-d", "-C", "videoSync=0", "-C", "audioSync=0", "-C", "fpsTarget=6000",
                          "-l", "0", rom], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                         text=True, env=env)
    p.stdin.write("c\n"); p.stdin.flush()
    time.sleep(seconds)
    p.send_signal(signal.SIGINT)
    time.sleep(0.5)
    for row in range(18):
        p.stdin.write(f"x/1 0x{0x9800 + 32 * row:04x} 20\n")
    p.stdin.write("status\nq\n"); p.stdin.flush()
    try:
        out = p.communicate(timeout=20)[0]
    except subprocess.TimeoutExpired:
        p.kill(); out = p.communicate()[0]
    return out

def text(out):
    rows = []
    for m in re.finditer(r"0x0*98[0-9A-Fa-f]{2}:\s*((?:[0-9A-Fa-f]{2}\s*)+)", out):
        bs = [int(x, 16) for x in m.group(1).split()]
        rows.append("".join(chr(b) if 32 <= b < 127 else " " for b in bs))
    return rows

if __name__ == "__main__":
    out = run(sys.argv[1], float(sys.argv[2]))
    rows = text(out)
    if not rows:
        print(out[-1500:])
    # rows come in 16-byte dump lines; print the non-empty ones
    print("\n".join(r.rstrip() for r in rows if r.strip()))
