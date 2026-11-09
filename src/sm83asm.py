"""A small two-pass assembler for the Game Boy CPU (SM83).

Syntax (one statement per line, ';' starts a comment):

    label:          global label          .local:   local to the last global label
    NAME equ expr   constant (also NAME = expr)
    org expr        set the address (addresses equal ROM offsets: banks 0 and 1)
    db e, "text", ...     dw e, ...     ds count[, fill]
    ld a,[hl+]   ldh a,[$FF44]   ldh [c],a   jr nz,.loop   bit 7,[hl]   rst $38 ...

Numbers: 123, $7B, %1111011, 0x7B, 'c'.  Functions: HIGH(x), LOW(x).
"""

from __future__ import annotations

import re

R8 = ["b", "c", "d", "e", "h", "l", "[hl]", "a"]
R16 = ["bc", "de", "hl", "sp"]
R16P = ["bc", "de", "hl", "af"]
CC = ["nz", "z", "nc", "c"]
ALU = ["add", "adc", "sub", "sbc", "and", "xor", "or", "cp"]
ROT = ["rlc", "rrc", "rl", "rr", "sla", "sra", "swap", "srl"]
SIMPLE = {"nop": [0x00], "stop": [0x10, 0x00], "halt": [0x76], "di": [0xF3], "ei": [0xFB], "rlca": [0x07],
          "rrca": [0x0F], "rla": [0x17], "rra": [0x1F], "daa": [0x27], "cpl": [0x2F], "scf": [0x37], "ccf": [0x3F],
          "reti": [0xD9]}  # fmt: skip


class AsmError(Exception):
    pass


def split_ops(s: str) -> list[str]:
    out, cur, depth, q = [], "", 0, False
    for ch in s:
        if ch == '"':
            q = not q
        if not q and ch in "([":
            depth += 1
        if not q and ch in ")]":
            depth -= 1
        if ch == "," and depth == 0 and not q:
            out.append(cur.strip())
            cur = ""
        else:
            cur += ch
    if cur.strip():
        out.append(cur.strip())
    return out


def norm(op: str) -> str:
    """Canonical lower-case form of a register-like operand."""
    o = op.lower().replace(" ", "")
    return {"[hli]": "[hl+]", "[hld]": "[hl-]", "(hl)": "[hl]"}.get(o, o)


def is_mem(op: str) -> bool:
    return op.startswith("[") and op.endswith("]")


def encode(mn: str, ops: list[str]):
    """Return a list of bytes; an operand still to evaluate is ('b'|'w'|'rel'|'hi', expr)."""
    n = [norm(o) for o in ops]
    B = lambda e: ("b", e)  # noqa: E731
    W = lambda e: ("w", e)  # noqa: E731
    if mn in SIMPLE and not ops:
        return list(SIMPLE[mn])
    if mn == "ld":
        d, s = n
        if d in R8 and s in R8 and not (d == "[hl]" and s == "[hl]"):
            return [0x40 | R8.index(d) << 3 | R8.index(s)]
        if d == "a" and s in ("[bc]", "[de]", "[hl+]", "[hl-]"):
            return [{"[bc]": 0x0A, "[de]": 0x1A, "[hl+]": 0x2A, "[hl-]": 0x3A}[s]]
        if s == "a" and d in ("[bc]", "[de]", "[hl+]", "[hl-]"):
            return [{"[bc]": 0x02, "[de]": 0x12, "[hl+]": 0x22, "[hl-]": 0x32}[d]]
        if d == "sp" and s == "hl":
            return [0xF9]
        if d == "hl" and s.startswith("sp+"):
            return [0xF8, B(ops[1].strip()[2:].lstrip().lstrip("+"))]
        if d == "hl" and s.startswith("sp-"):
            return [0xF8, B("-(" + ops[1].strip()[2:].lstrip().lstrip("-") + ")")]
        if is_mem(d) and s == "sp":
            return [0x08, W(ops[0].strip()[1:-1])]
        if d == "a" and is_mem(s):
            return [0xFA, W(ops[1].strip()[1:-1])]
        if s == "a" and is_mem(d):
            return [0xEA, W(ops[0].strip()[1:-1])]
        if d in R16:
            return [0x01 | R16.index(d) << 4, W(ops[1])]
        if d in R8:
            return [0x06 | R8.index(d) << 3, B(ops[1])]
    if mn == "ldh":
        d, s = n
        if d == "a" and s == "[c]":
            return [0xF2]
        if d == "[c]" and s == "a":
            return [0xE2]
        if d == "a" and is_mem(s):
            return [0xF0, ("hi", ops[1].strip()[1:-1])]
        if s == "a" and is_mem(d):
            return [0xE0, ("hi", ops[0].strip()[1:-1])]
    if mn in ("push", "pop") and n[0] in R16P:
        return [(0xC5 if mn == "push" else 0xC1) | R16P.index(n[0]) << 4]
    if mn in ALU:
        if mn == "add" and n[0] == "hl" and n[1] in R16:
            return [0x09 | R16.index(n[1]) << 4]
        if mn == "add" and n[0] == "sp":
            return [0xE8, B(ops[1])]
        src, raw = (n[1], ops[1]) if len(n) == 2 and n[0] == "a" else (n[0], ops[0])
        if len(n) == 2 and n[0] != "a":
            raise AsmError(f"bad operands for {mn}")
        if src in R8:
            return [0x80 | ALU.index(mn) << 3 | R8.index(src)]
        return [0xC6 | ALU.index(mn) << 3, B(raw)]
    if mn in ("inc", "dec"):
        if n[0] in R8:
            return [(0x04 if mn == "inc" else 0x05) | R8.index(n[0]) << 3]
        if n[0] in R16:
            return [(0x03 if mn == "inc" else 0x0B) | R16.index(n[0]) << 4]
    if mn == "jp":
        if n == ["hl"]:
            return [0xE9]
        if len(n) == 2 and n[0] in CC:
            return [0xC2 | CC.index(n[0]) << 3, W(ops[1])]
        if len(n) == 1:
            return [0xC3, W(ops[0])]
    if mn == "jr":
        if len(n) == 2 and n[0] in CC:
            return [0x20 | CC.index(n[0]) << 3, ("rel", ops[1])]
        if len(n) == 1:
            return [0x18, ("rel", ops[0])]
    if mn == "call":
        if len(n) == 2 and n[0] in CC:
            return [0xC4 | CC.index(n[0]) << 3, W(ops[1])]
        if len(n) == 1:
            return [0xCD, W(ops[0])]
    if mn == "ret":
        if not n:
            return [0xC9]
        if n[0] in CC:
            return [0xC0 | CC.index(n[0]) << 3]
    if mn == "rst":
        return [("rst", ops[0])]
    if mn in ROT and n[0] in R8:
        return [0xCB, ROT.index(mn) << 3 | R8.index(n[0])]
    if mn in ("bit", "res", "set") and len(n) == 2 and n[1] in R8:
        return [0xCB, ("bit", ops[0], {"bit": 0x40, "res": 0x80, "set": 0xC0}[mn] | R8.index(n[1]))]
    raise AsmError(f"cannot encode: {mn} {', '.join(ops)}")


class Assembler:
    def __init__(self, size: int = 0x8000, fill: int = 0xFF):
        self.rom = bytearray([fill] * size)
        self.used = bytearray(size)
        self.sym: dict[str, int] = {}
        self.items = []  # (addr, encoded, scope, lineno, text)

    def expr(self, e: str, scope: str, addr: int, lineno: int) -> int:
        s = e.strip()
        s = re.sub(r"\$([0-9A-Fa-f]+)", r"0x\1", s)
        s = re.sub(r"%([01]+)", r"0b\1", s)
        s = re.sub(r"'(.)'", lambda m: str(ord(m.group(1))), s)
        s = re.sub(r"(?<![\w.])\.(\w+)", lambda m: f"{scope}__{m.group(1)}", s)
        s = s.replace("@", str(addr))
        env = dict(self.sym)
        env.update(HIGH=lambda v: (v >> 8) & 0xFF, LOW=lambda v: v & 0xFF)
        try:
            return int(eval(s, {"__builtins__": {}}, env))  # noqa: S307 - our own source text
        except Exception as ex:
            raise AsmError(f"line {lineno}: bad expression {e!r}: {ex}") from None

    def assemble(self, text: str):
        addr, scope = 0, ""
        pending = []  # equ definitions that may refer forward
        for lineno, raw in enumerate(text.split("\n"), 1):
            line = raw.split(";", 1)[0].rstrip() if '"' not in raw else raw.rstrip()
            if '"' in raw:  # strip a comment outside quotes
                out, q = "", False
                for ch in raw:
                    if ch == '"':
                        q = not q
                    if ch == ";" and not q:
                        break
                    out += ch
                line = out.rstrip()
            s = line.strip()
            if not s:
                continue
            m = re.match(r"^(\.?\w+):\s*(.*)$", s)
            if m:
                name = m.group(1)
                if name.startswith("."):
                    name = f"{scope}__{name[1:]}"
                else:
                    scope = name
                if name in self.sym:
                    raise AsmError(f"line {lineno}: duplicate label {name}")
                self.sym[name] = addr
                s = m.group(2).strip()
                if not s:
                    continue
            m = re.match(r"^(\w+)\s+(?:equ|EQU)\s+(.+)$", s) or re.match(r"^(\w+)\s*=\s*(.+)$", s)
            if m:
                pending.append((m.group(1), m.group(2), scope, addr, lineno))
                try:
                    self.sym[m.group(1)] = self.expr(m.group(2), scope, addr, lineno)
                except AsmError:
                    pass
                continue
            parts = s.split(None, 1)
            mn = parts[0].lower()
            ops = split_ops(parts[1]) if len(parts) > 1 else []
            if mn == "org":
                addr = self.expr(ops[0], scope, addr, lineno)
                continue
            if mn == "db":
                enc = []
                for o in ops:
                    if o.startswith('"'):
                        enc += [ord(c) for c in o[1:-1]]
                    else:
                        enc.append(("b", o))
            elif mn == "dw":
                enc = [("w", o) for o in ops]
            elif mn == "ds":
                n = self.expr(ops[0], scope, addr, lineno)
                enc = [("b", ops[1]) if len(ops) > 1 else 0] * n
            else:
                try:
                    enc = encode(mn, ops)
                except (AsmError, ValueError, IndexError) as ex:
                    raise AsmError(f"line {lineno}: {ex}: {s}") from None
            size = sum(2 if isinstance(x, tuple) and x[0] == "w" else 1 for x in enc)
            self.items.append((addr, enc, scope, lineno, s))
            addr += size
        for name, e, scope, a, lineno in pending:
            self.sym[name] = self.expr(e, scope, a, lineno)
        for addr, enc, scope, lineno, text in self.items:
            pos = addr
            total = sum(2 if isinstance(x, tuple) and x[0] == "w" else 1 for x in enc)
            for x in enc:
                if isinstance(x, int):
                    vals = [x]
                elif x[0] == "b":
                    v = self.expr(x[1], scope, addr, lineno)
                    if not -128 <= v <= 255:
                        raise AsmError(f"line {lineno}: byte out of range ({v}): {text}")
                    vals = [v & 0xFF]
                elif x[0] == "hi":
                    v = self.expr(x[1], scope, addr, lineno)
                    if not (0 <= v <= 0xFF or 0xFF00 <= v <= 0xFFFF):
                        raise AsmError(f"line {lineno}: ldh address out of range: {text}")
                    vals = [v & 0xFF]
                elif x[0] == "w":
                    v = self.expr(x[1], scope, addr, lineno)
                    if not -32768 <= v <= 0xFFFF:
                        raise AsmError(f"line {lineno}: word out of range: {text}")
                    vals = [v & 0xFF, (v >> 8) & 0xFF]
                elif x[0] == "rel":
                    v = self.expr(x[1], scope, addr, lineno) - (addr + total)
                    if not -128 <= v <= 127:
                        raise AsmError(f"line {lineno}: jr out of range ({v}): {text}")
                    vals = [v & 0xFF]
                elif x[0] == "rst":
                    v = self.expr(x[1], scope, addr, lineno)
                    if v & ~0x38:
                        raise AsmError(f"line {lineno}: bad rst vector: {text}")
                    vals = [0xC7 | v]
                elif x[0] == "bit":
                    v = self.expr(x[1], scope, addr, lineno)
                    if not 0 <= v <= 7:
                        raise AsmError(f"line {lineno}: bad bit number: {text}")
                    vals = [x[2] | v << 3]
                for b in vals:
                    if pos >= len(self.rom):
                        raise AsmError(f"line {lineno}: address {pos:#x} outside the image")
                    if self.used[pos]:
                        raise AsmError(f"line {lineno}: overlapping output at {pos:#06x}: {text}")
                    self.rom[pos] = b
                    self.used[pos] = 1
                    pos += 1
        return self


def assemble(text: str, size: int = 0x8000) -> Assembler:
    return Assembler(size).assemble(text)
