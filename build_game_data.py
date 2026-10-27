"""Extract the game parameters the damage model needs into reference/game_data.json.

Source: "Nightreign Attack Power Calculator" by Cryptid Tracker (linked from the Useful Info sheet), which carries the
game's regulation params: CalcCorrectGraphEz (stat -> scaling %), HeroData (stats per character level),
EquipParamWeapon + WeaponData (base attack and stat scaling per weapon).
Usage: uv run build_game_data.py [reference/ap_calculator.xlsx]
"""
import json, sys
from pathlib import Path

import pandas as pd

SRC = Path(__file__).with_name("reference") / "ap_calculator.xlsx"
OUT = Path(__file__).with_name("reference") / "game_data.json"
STATS = ["statVigor", "statMind", "statEndurance", "statStrength", "statDexterity", "statIntelligence", "statFaith", "statArcane"]


def main():
    x = pd.ExcelFile(sys.argv[1] if len(sys.argv) > 1 else SRC)
    g = pd.read_excel(x, "CalcCorrectGraphEz").set_index("ID")
    curves = {str(int(i)): [0.0] + [round(float(v), 4) for v in row.values if pd.notna(v)] for i, row in g.iterrows()}
    hero = pd.read_excel(x, "HeroData")
    heroes = {}
    for _, r in hero.iterrows():
        name, lv = str(r["NameLv"]).split("Lv")
        heroes.setdefault(name, {})[lv] = {s[4:].lower(): int(r[s]) for s in STATS}
    w = pd.read_excel(x, "EquipParamWeapon").set_index("ID")
    wd = pd.read_excel(x, "WeaponData")
    weapons = {}
    for _, r in wd.iterrows():
        if pd.isna(r["ID"]) or int(r["ID"]) not in w.index:
            continue
        p = w.loc[int(r["ID"])]
        if any(p[k] for k in ("attackBaseMagic", "attackBaseFire", "attackBaseThunder", "attackBaseDark")) or p.attackBasePhysics <= 0:
            continue   # physical-only weapons: the Strength model below covers physical AP
        weapons[str(r["Weapon"])] = dict(cls=str(r["Weapon Class"]), base=int(p.attackBasePhysics), str=float(p.correctStrength),
                                         dex=float(p.correctAgility), curve=str(int(p.correctType_Physics)),
                                         two_hand_bonus=bool(r["bothHandsAtkBonus"]))
    OUT.write_text(json.dumps(dict(source="Nightreign Attack Power Calculator (Cryptid Tracker) v1.1.1, regulation 1.02.2",
                                   curves=curves, heroes=heroes, weapons=weapons), indent=0), encoding="utf-8")
    print(f"wrote {OUT}: {len(curves)} curves, {len(heroes)} heroes, {len(weapons)} weapons")


if __name__ == "__main__":
    main()
