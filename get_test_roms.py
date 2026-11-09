"""Download the public test ROMs the regression tests use (Blargg's gb-test-roms) into roms/."""
import urllib.parse, urllib.request
from pathlib import Path

BASE = "https://raw.githubusercontent.com/retrio/gb-test-roms/master/"
FILES = {
    "01": "cpu_instrs/individual/01-special", "02": "cpu_instrs/individual/02-interrupts",
    "03": "cpu_instrs/individual/03-op sp,hl", "04": "cpu_instrs/individual/04-op r,imm",
    "05": "cpu_instrs/individual/05-op rp", "06": "cpu_instrs/individual/06-ld r,r",
    "07": "cpu_instrs/individual/07-jr,jp,call,ret,rst", "08": "cpu_instrs/individual/08-misc instrs",
    "09": "cpu_instrs/individual/09-op r,r", "10": "cpu_instrs/individual/10-bit ops",
    "11": "cpu_instrs/individual/11-op a,(hl)", "cpu_instrs": "cpu_instrs/cpu_instrs",
    "instr_timing": "instr_timing/instr_timing", "x_01-read_timing": "mem_timing/individual/01-read_timing",
    "x_02-write_timing": "mem_timing/individual/02-write_timing",
    "x_03-modify_timing": "mem_timing/individual/03-modify_timing",
}
Path("roms").mkdir(exist_ok=True)
for name, path in FILES.items():
    data = urllib.request.urlopen(BASE + urllib.parse.quote(path + ".gb"), timeout=30).read()
    Path(f"roms/{name}.gb").write_bytes(data)
    print(f"roms/{name}.gb  {len(data)} bytes")
