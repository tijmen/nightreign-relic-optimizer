"""Read the relic inventory straight from a Nightreign save (NR0000.sl2), read-only.

Layout, key and game tables come from alfizari's Nightreign Save Editor (MIT, see reference/save_editor/LICENSE;
commit 0d2ad14, 2026-06-05): https://github.com/alfizari/Elden-Ring-Nightreign-Save-Editor
  * NR0000.sl2 is a BND4 container; each USER_DATA entry is [16-byte IV][AES-128-CBC payload] with one fixed key.
  * In a character slot, 5120 item states start at 0x14 (weapon 88 bytes, armor 16, relic 80, empty 8). A relic state is
    ga_handle, item_id (0x80000000 | relic id), ..., effects at +16/+20/+24, curses at +56/+60/+64 (curse n belongs to
    effect n). The inventory entry table (14 bytes each) follows, 0x94 + 0x5B8 + 4 bytes after the last state; it lists
    which states are owned items, with an acquisition id.
  * Relic name/color/deep-ness: EquipParamAntique + AntiqueName.fmg; effect names: AttachEffectParam.attachTextId ->
    AttachEffectName.fmg (reference/save_editor/, en_US).
Usage: uv run sl2_reader.py [save.sl2]    # lists character slots and relic counts
"""
import csv, os, struct, sys
import xml.etree.ElementTree as ET
from pathlib import Path

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

KEY = bytes.fromhex("18f6326605bd178a5524523ac0a0c609")
DATA = Path(__file__).with_name("reference") / "save_editor"
NONE = 0xFFFFFFFF
RELIC, WEAPON, ARMOR = 0xC0000000, 0x80000000, 0x90000000
STATE_SIZE = {RELIC: 80, WEAPON: 88, ARMOR: 16}
STATES, ENTRIES, ENTRY_SIZE = 5120, 3065, 14
COLOR = {0: "Red", 1: "Blue", 2: "Yellow", 3: "Green", 4: "White"}


def default_save():
    """The most recently written NR0000.sl2 under %APPDATA%\\Nightreign\\<steam id>\\ (backups excluded)."""
    root = Path(os.environ.get("APPDATA", "")) / "Nightreign"
    saves = sorted(root.glob("*/NR0000.sl2"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not saves:
        sys.exit(f"no NR0000.sl2 under {root}; pass the save (or an .xlsx export) explicitly")
    return saves[0]


def user_data(path):
    """Decrypted payload of every BND4 entry, in order."""
    b = Path(path).read_bytes()
    if b[:4] != b"BND4":
        raise ValueError(f"{path}: not a BND4 save file")
    out = []
    for i in range(struct.unpack_from("<i", b, 12)[0]):
        size, _, off = struct.unpack_from("<iii", b, 64 + 32 * i + 8)
        iv, payload = b[off:off + 16], b[off + 16:off + size]
        d = Cipher(algorithms.AES(KEY), modes.CBC(iv)).decryptor()
        out.append(d.update(payload) + d.finalize())
    return out


def parse_slot(data):
    """(player name, [(acquisition id, real relic id, [effects], [curses])]) or None if this isn't a character slot."""
    if len(data) < 0x1000:
        return None
    states, cur = {}, 0x14
    for _ in range(STATES):
        ga, item = struct.unpack_from("<II", data, cur)
        size = STATE_SIZE.get(ga & 0xF0000000, 8) if ga else 8
        if ga and ga & 0xF0000000 == RELIC:
            v = struct.unpack_from("<20I", data, cur)
            states[ga] = (item & 0x00FFFFFF, list(v[4:7]), list(v[14:17]))
        cur += size
        if cur > len(data):
            return None
    cur += 0x94
    raw = data[cur:cur + 32]
    name = raw[:next((k for k in range(0, 32, 2) if raw[k:k + 2] == b"\0\0"), 32)].decode("utf-16-le", "ignore")
    cur += 0x5B8 + 4
    relics = []
    for _ in range(ENTRIES):
        ga, _, acq = struct.unpack_from("<III", data, cur)
        if ga in states:
            relics.append((acq, *states[ga]))
        cur += ENTRY_SIZE
    return (name, relics) if name.isprintable() and relics else None   # other entries decode to noise


def slots(path):
    return [s for s in map(parse_slot, user_data(path)) if s]


def _fmg(*names):
    out = {}
    for n in names:
        for t in ET.parse(DATA / "text" / n).getroot().iter("text"):
            if t.text and t.text != "%null%":
                out[int(t.get("id"))] = t.text
    return out


def _param(name, *cols):
    with open(DATA / "param" / name, encoding="utf-8", newline="") as f:
        return {int(r["ID"]): tuple(r[c] for c in cols) for r in csv.DictReader(f)}


class Tables:
    def __init__(self):
        self.relic_names = _fmg("AntiqueName.fmg.xml", "AntiqueName_dlc01.fmg.xml")
        self.effect_names = _fmg("AttachEffectName.fmg.xml", "AttachEffectName_dlc01.fmg.xml")
        self.relics = _param("EquipParamAntique.csv", "relicColor", "isDeepRelic")
        self.effects = _param("AttachEffectParam.csv", "attachTextId")

    def relic(self, rid):
        color, deep = self.relics.get(rid, ("0", "0"))
        return self.relic_names.get(rid, f"Unknown relic {rid}"), COLOR.get(int(color), "Red"), deep == "1"

    def effect(self, e):
        text = self.effects.get(e, (None,))[0]
        return self.effect_names.get(int(text), f"Unknown effect {e}") if text not in (None, "", "-1") else f"Unknown effect {e}"


def load(path=None, slot=None):
    """Relic rows like the xlsx export: dict(row, name, color, deep, effects=[(id, name, curse id|None, curse name|None)]).
    `row` is the relic's place in acquisition order (1 = oldest). `slot`: player name or index; default: the slot with
    the most relics."""
    path = path or default_save()
    found = slots(path)
    if not found:
        sys.exit(f"{path}: no character slot found")
    if slot is None:
        name, relics = max(found, key=lambda s: len(s[1]))
    else:
        pick = [s for k, s in enumerate(found) if str(slot) in (s[0], str(k))]
        if not pick:
            sys.exit(f"{path}: no slot {slot!r}; have: {', '.join(f'{k}: {s[0]}' for k, s in enumerate(found))}")
        name, relics = pick[0]
    t = Tables()
    out = []
    for row, (_, rid, effects, curses) in enumerate(sorted(relics), 1):
        rname, color, deep = t.relic(rid)
        eff = [(e, t.effect(e), None if c == NONE else c, None if c == NONE else t.effect(c))
               for e, c in zip(effects, curses) if e != NONE]
        out.append(dict(row=row, name=rname, color=color, deep=deep, effects=eff, relic_id=rid))
    return out, name, Path(path)


def main():
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else default_save()
    print(path)
    for k, (name, relics) in enumerate(slots(path)):
        print(f"  slot {k}: {name}: {len(relics)} relics")


if __name__ == "__main__":
    main()
