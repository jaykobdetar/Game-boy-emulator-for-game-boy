"""Build a GBinGB cartridge image: the emulator followed by the guest ROM it will run.

usage: python3 mkrom.py GUEST.gb OUT.gb

The image is an MBC5 cartridge with battery-backed RAM: 32 KB when the guest
has at most 8 KB of cartridge RAM, 128 KB otherwise.  RAM bank 0 holds the
guest's work RAM, banks 1 and up its cartridge RAM (so the guest's saves
persist).  A GBinGB image is itself a valid guest.
"""
from __future__ import annotations

import sys
from pathlib import Path

import gen
import sm83asm

BANK = 0x4000


def host_image() -> tuple[bytes, dict]:
    a = sm83asm.assemble(gen.source(), 2 * BANK)
    if a.sym["code_end"] > a.sym["TLO"]:
        raise SystemExit(f"host code overruns its tables ({a.sym['code_end']:#x})")
    return bytes(a.rom), a.sym


def guest_config(guest: bytes) -> dict:
    kind, ramcode = guest[0x147], guest[0x149]
    mbc = 0 if kind in (0x00, 0x08, 0x09) else 1 if kind in (1, 2, 3) else 3 if 0x0F <= kind <= 0x13 else \
        5 if 0x19 <= kind <= 0x1E else None
    if mbc is None:
        raise SystemExit(f"guest cartridge type ${kind:02X} is not supported (ROM only, MBC1, MBC3, MBC5)")
    if len(guest) < 0x150 or len(guest) % BANK:
        raise SystemExit("the guest is not a Game Boy ROM image (its size is not a multiple of 16 KB)")
    if guest[0x143] == 0xC0:
        raise SystemExit("the guest is a Game Boy Color-only game; GBinGB emulates the original Game Boy")
    banks = max(2, -(-len(guest) // BANK))
    banks = 1 << (banks - 1).bit_length()
    if banks > 128:
        raise SystemExit("guest ROM larger than 2 MB is not supported")
    rambanks = {0: 0, 1: 1, 2: 1, 3: 4, 4: 8, 5: 8}.get(ramcode, 0)  # 128 KB guests get 64 KB
    has_ram = kind in (2, 3, 8, 9, 0x0F, 0x10, 0x12, 0x13, 0x1A, 0x1B, 0x1D, 0x1E) and rambanks > 0
    return {"mbc": mbc, "rommask": banks - 1, "hasram": int(has_ram), "rammask": max(rambanks, 1) - 1, "banks": banks}


def build(guest: bytes) -> bytes:
    host, sym = host_image()
    cfg = guest_config(guest)
    rom = bytearray(host) + bytearray(guest) + bytearray([0xFF] * (cfg["banks"] * BANK - len(guest)))
    total = 1 << (len(rom) - 1).bit_length()
    rom += bytearray([0xFF] * (total - len(rom)))
    rom[sym["cfg_mbc"]] = cfg["mbc"]
    rom[sym["cfg_rommask"]] = cfg["rommask"]
    rom[sym["cfg_hasram"]] = cfg["hasram"]
    rom[sym["cfg_rammask"]] = cfg["rammask"]
    rom[0x148] = (total // 0x8000).bit_length() - 1
    rom[0x149] = 0x04 if cfg["hasram"] and cfg["rammask"] > 0 else 0x03  # 128 KB or 32 KB of cartridge RAM
    x = 0
    for i in range(0x134, 0x14D):
        x = (x - rom[i] - 1) & 0xFF
    rom[0x14D] = x
    rom[0x14E] = rom[0x14F] = 0
    g = sum(rom) & 0xFFFF
    rom[0x14E], rom[0x14F] = g >> 8, g & 0xFF
    return bytes(rom)


if __name__ == "__main__":
    out = build(Path(sys.argv[1]).read_bytes())
    Path(sys.argv[2]).write_bytes(out)
    print(f"{sys.argv[2]}: {len(out) // 1024} KB")
