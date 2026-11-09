"""Check the assembler's encodings against PyBoy's independent opcode table (all 512 entries)."""
import re, warnings
warnings.filterwarnings("ignore")
from pyboy.core import opcodes
import sm83asm

def to_mine(m):
    if not m.strip() or m in ("???", "PREFIX CB") or m.startswith("PREFIX") or "Illegal" in m:
        return None  # opcodes the CPU does not have, and the CB prefix itself
    if m == "JP (HL)":
        return "jp hl"
    s = m.lower().replace("(", "[").replace(")", "]")
    mn, _, rest = s.partition(" ")
    ops = rest.split(",") if rest else []
    ops = [o.replace("d16", "$1234").replace("a16", "$1234").replace("d8", "$12") for o in ops]
    if mn == "stop": return "stop"
    if mn in ("ld", "ldh"):
        if any("a8" in o for o in ops): mn = "ldh"; ops = [o.replace("a8", "$FF12") for o in ops]
        if "[c]" in ops: mn = "ldh"
        ops = [o.replace("sp+r8", "sp+$12") for o in ops]
    if mn in ("jr",): ops = [o.replace("r8", "@+2+$12") for o in ops]
    if mn == "add" and ops and ops[0] == "sp": ops[1] = "$12"
    if mn == "rst": ops = ["$" + ops[0].rstrip("h")]
    if mn in ("sub", "and", "xor", "or", "cp") and len(ops) == 1: ops = ["a"] + ops
    return mn + " " + ",".join(ops)

bad = n = 0
for i, m in enumerate(opcodes.CPU_COMMANDS):
    src = to_mine(m)
    if src is None: continue
    try:
        a = sm83asm.assemble("org 0\n" + src + "\n", 16)
        got = bytes(a.rom[:3])
    except Exception as e:
        print("FAIL", hex(i), m, "->", src, e); bad += 1; continue
    want = bytes([i]) if i < 256 else bytes([0xCB, i - 256])
    n += 1
    if not got.startswith(want):
        print("MISMATCH", hex(i), m, "->", src, got.hex()); bad += 1
    else:
        rest = got[len(want):]
        if "$1234" in src and rest[:2] != b"\x34\x12": print("OPERAND", m, got.hex()); bad += 1
        if ("$12" in src and "$1234" not in src and "rst" not in src) and rest[:1] != b"\x12": print("OPERAND8", m, src, got.hex()); bad += 1
print(f"{n} encodings checked, {bad} bad")
