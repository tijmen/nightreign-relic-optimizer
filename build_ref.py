"""Map every effect/curse ID in a relic export to its row in the community "Nightreign Useful Info" sheet.

Usage: uv run build_ref.py [NR0000.sl2 | all_relics_exported_YYYYMMDD.xlsx | -] [reference/useful_info.xlsx]
       (default / "-": your save in %APPDATA%\\Nightreign)
Writes reference/effect_ref.json: {"effects": {id: ref}, "curses": {id: ref}}, ref = sheet/row/desc/effect/stack/notes.
Deep-only IDs (6xxxxxx) are looked up in "Deep Relic Effects" first, everything else in "Relic Effects" first.
Unmatched names are printed; add them to OVERRIDES (name regex -> (sheet, description prefix)) if needed.
"""
import json, re, sys
from pathlib import Path

import openpyxl
import relic_opt as ro

NORMAL, DEEP = "Relic Effects", "Deep Relic Effects"
# Export names whose sheet description doesn't pattern-match. Value: (sheet, start of "Relic Description").
OVERRIDES = {
    r"^Physical Attack Up( \+[0-2])?$": (NORMAL, "Physical Attack Up +0/1/2"),
    r"^Improved Critical Hits( \+1)?$": (NORMAL, "Improved Critical Hits"),
    r"^Starting armament deals \w+ damage$": (NORMAL, "Starting Weapon deals"),
    r"^Starting armament inflicts [\w ]+$": (NORMAL, "Stating armament inflicts"),
    r"^Poise \+[123]$": (NORMAL, "Poise +1/2/3"),
    r"^Changes compatible armament's (skill|sorcery|incantation) to": (NORMAL, "Change compatible armament skill"),
    r"^Small Pouch in possession": (NORMAL, "Small pouch in possession"),
    r"^Stonesword Key in possession": (NORMAL, "Stonesword in possession"),
    r"in possession at start of expedition$": (NORMAL, "[Consumable] in possession"),
    r"^Items confer effect to all nearby allies$": (NORMAL, "Item confer effect"),
    r"^Increased rune acquisition for self and allies$": (NORMAL, "Increase rune acquisition"),
    r"^Attack power increased for each evergaol prisoner defeated$": (NORMAL, "Attack power permanently increased for each evergaol"),
    r"^Attack power increased for each Night Invader defeated$": (NORMAL, "Attack power up after defeating a Night Invader"),
    r"(?i)rune discount for shop purchases": (NORMAL, "(Huge) Rune discount"),
    r"^(Magic|Fire|Lightning|Holy) Attack Power Up( \+[0-2])?$": (NORMAL, "Magic/Fire/Lightning/Holy Attack Up +0/1/2"),
    r"^(Magic|Fire|Lightning|Holy) Attack Power Up \+[34]$": (DEEP, "Magic/Fire/Lightning/Holy Attack Up +3/4"),
    r"^(Magic|Fire|Lightning|Holy) Damage Negation Up$": (NORMAL, "Magic/Fire/Lightning/Holy Damage Negation Up"),
    r"^Improved (Magic|Fire|Lightning|Holy) Damage Negation \+[12]$": (DEEP, "Improved [Element] Damage Negation"),
    r"^Improved \w+( \w+)? Resistance$": (NORMAL, "Improved [Status] Resistance"),
    r"^Improved \w+( \w+)? Resistance \+[12]$": (DEEP, "Improved [Status] Resistance +1/2"),
    r"^Improved Stance-Breaking when Two-Handing$": (NORMAL, "Improved stance breaking when two handing"),
    r"^Improved Stance-Breaking when Wielding Two Armaments$": (NORMAL, "Improved stance breaking while wielding"),
    r"^Physical attack power increases after using grease items$": (NORMAL, "Attack power increases after using grease"),
    r"^Improved (Throwing Pot|Throwing Knife|Glintstone and Gravity Stone) Damage$|^Improved Perfuming Arts$": (NORMAL, "Improved Throwing Pot/Knife"),
    r"^Improved (Throwing Pot Damage|Perfuming Arts) \+[12]$": (DEEP, "Improved [Consumable] Damage"),
    r"^Improved .+ (Sorcery|sorcery|Incantations)$": (NORMAL, "Improved [Spell School]"),
    r"^Improved Roar & Breath Attacks$": (NORMAL, "Improves Roar and Breath"),
    r"^Improved (?!Affinity|Melee|Skill).+ Attack Power$": (NORMAL, "Improve [Weapon Type] attack power"),
    r"^Improved Attack Power with 3\+ .+ Equipped$": (NORMAL, "Improved Attack Power with 3 [Weapon Type]"),
    r"^Max FP Up with 3\+": (NORMAL, "Max FP Up with 3+"),
    r"^Max HP Up with 3\+": (NORMAL, "Max HP Up with 3+"),
    r"^Attack power up when facing [\w ]+-afflicted enemy$": (NORMAL, "Attack power up when facing"),
    r"^Attack power up when facing [\w ]+-afflicted enemy \+[12]$": (DEEP, "Attack power up when facing"),
    r"^Character Skill Cooldown Reduction": (NORMAL, "Character Skill Cooldown"),
    r"^Ultimate Art Auto Charge": (NORMAL, "Ultimate Art Gauge"),
    r"^Successful guarding fills more of the Art gauge$": (NORMAL, "Art gauge charged from successful guarding"),
    r"^Successful guarding fills more of the Art gauge \+1$": (DEEP, "Art gauge charged from successful guarding +1"),
    r"^Critical hits fill more of the Art gauge$": (NORMAL, "Art gauge fills moderately upon critical"),
    r"^Critical hits fill more of the Art gauge \+1$": (DEEP, "Art gauge fills moderately upon critical hit +1"),
    r"^HP restored when using medicinal boluses, etc\.$": (NORMAL, "HP restored when using cured meats"),
    r"^HP Restoration upon .+ Attacks$": (NORMAL, "HP restoration after [Weapon type]"),
    r"^FP Restoration upon (?!Successive).+ Attacks$": (NORMAL, "FP restoration after [Weapon type]"),
    r"^Stamina Recovery upon Landing Attacks": (NORMAL, "Stamina Recovery upon Landing Attacks"),
    r"^Critical Hit Boosts Stamina Recovery Speed( \+1)?$": (NORMAL, "Critical Hit Boosts Stamina Recovery Speed"),
    r"^Continuous HP Recovery$": (NORMAL, "Continuous HP recovery"),
    r"^Improved Damage Negation at Low HP$": (NORMAL, "Improved Damage Negation at Low HP"),
    r"^Increased Maximum (HP|FP|Stamina)$": (NORMAL, "Increased maximum HP/FP/Stamina"),
    r"^(Vigor|Mind|Endurance) \+[123]$": (NORMAL, None),   # resolved below by name
    r"^(Strength|Dexterity|Intelligence|Faith|Arcane) \+[123]$": (NORMAL, "Strength/Dexterity"),
    r"^Physical Attack Up \+[34] ?$": (DEEP, "Physical Attack Up +3/4"),
    r"^Improved Guard Counters \+[12]$": (DEEP, "Improved Guard Counters +1/2"),
    r"^Improved Physical Damage Negation \+[12]$": (DEEP, "Improved Physical Damage Negation +1/2"),
    r"^Improved Affinity Damage Negation": (DEEP, "Improved Affinity Damage Negation"),
    r"^Improved Affinity Attack Power": (DEEP, "Improved Affinity Attack Power"),
    r"^Improved (Sorceries|Incantations)": (DEEP, "Improved Sorceries/Incantations"),
    r"^Reduced FP Consumption$": (DEEP, "Reduced FP Consumption"),
    r"^Dormant Power Helps Discover": (DEEP, "Dormant Power Helps Discover"),
    r"^Partial HP Restoration upon Post-Damage Attacks \+[12]$": (DEEP, "Partial HP Restoration upon Post-Damage Attacks +1/2"),
    r"^(Sleep|Madness) in Vicinity Improves Attack Power": (DEEP, "Sleep/Madness in Vicinity"),
    r"^Physical attack power increases after using grease items \+[12]$": (DEEP, "Physical attack power increases after using grease"),
    r"^Defeating enemies fills more of the Art gauge \+1$": (DEEP, "Defeating enemies fills more of the Art gauge +1"),
    r"^HP Restoration upon Thrusting Counterattack \+1$": (DEEP, "HP Restoration upon Thrusting Counterattack +1"),
    r"^HP restored when using medicinal boluses, etc\. \+1$": (DEEP, "HP restored when using medicinal boluses"),
    r"^Improved Flask HP Restoration$": (DEEP, "Improved Flask HP Restoration"),
    r"^Improved Initial Standard Attack$": (NORMAL, "Improved Initial Standard Attack"),
    r"^Taking attacks improves attack power$": (NORMAL, "Taking attacks improves attack power"),
    r"^\[Executor\] Character Skill Boosts Attack": (NORMAL, "[Executor] Character Skill Boosts Attack"),
    r"^\[Raider\] Damage taken while using": (NORMAL, "[Raider] Damage taken while using"),
    r"^\[Raider\] Duration of Ultimate Art": (NORMAL, "[Raider] Duration of Ultimate Art"),
    r"^\[Revenant\] Increased Max FP upon": (DEEP, "[Revenant] Increases Max FP"),
    r"^\[Guardian\] Increased duration for Character Skill$": (NORMAL, "[Guardian] Increased skill duration"),
    r"^\[Scholar\] Prevent slowing of Character Skill": (NORMAL, "[Scholar] Prevent slowing"),
    r"^\[Undertaker\] Attack power increased by landing": (NORMAL, "[Undertaker] Attack power increases by landing"),
    r"^\[Wylder\] Art gauge greatly filled": (NORMAL, "[Wylder] Art gauge is greatly filled"),
    r"^\[Wylder\] Art activation spreads fire": (NORMAL, "[Wylder] Art activation spreads fire"),
    r"^\[Wylder\] \+1 additional Character Skill use$": (NORMAL, "[Wylder] +1 additional"),
    r"^\[Ironeye\] \+1 additional Character Skill use$": (NORMAL, "[Ironeye] +1 additional"),
    r"^\[Guardian\] Slowly restores nearby allies' HP": (NORMAL, "[Guardian] Slowly restores nearby allies HP"),
    r"^\[Executor\] Roaring restores HP": (NORMAL, "[Executor] Roaring restores HP"),
    r"^\[Recluse\] Collecting affinity residue": (NORMAL, "[Recluse] Collecting affinity residue"),
    r"^\[Revenant\] Strengthens family": (NORMAL, "[Revenant] Strengthens family"),
    r"^\[Ironeye\] Art Charge Activation": (NORMAL, "[Ironeye] Art Charge Activation"),
    r"^\[Duchess\] Successive dagger attack": (NORMAL, "[Duchess] Successive dagger attack"),
    r"^Defeating enemies near Totem Stela restores HP$": (NORMAL, "Defeating enemies near Totem Stela"),
    r"^Improved Poise Near Totem Stela$": (NORMAL, "Improved Poise Near Totem Stela"),
    r"^Max HP increased for each great enemy defeated at a Great Church\.?$": (DEEP, "Max HP increased for each great enemy"),
    r"^Attacks Inflict Rot when Damage is Taken$": (NORMAL, "Attacks Inflict Rot When Damage"),
    r"^FP Restoration upon Successive Attacks$": (NORMAL, "FP Restoration upon Successive Attacks"),
    # curses
    r"^Taking Damage Causes \w+( \w+)? Buildup$": (DEEP, "Taking Damage Causes [Status] Buildup"),
    r"^Reduced Intelligence and Dexterity$": (DEEP, "Reduced Dexterity and Intelligence"),
    r"^Reduced Faith and Strength$": (DEEP, "Reduced Strength and Faith"),
    r"^Reduced Damage Negation After Evading$": (DEEP, "More Damage Taken After Evasion"),
}
NAME_ROWS = {"Vigor": "Vigor +1/2/3", "Mind": "Mind +1/2/3", "Endurance": "Endurance +1/2/3"}


def sheet_rows(path):
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    rows = {}
    for sheet in (NORMAL, DEEP):
        it = wb[sheet].iter_rows(values_only=True)
        hdr = next(it)
        for i, r in enumerate(it, start=2):
            if r[1]:
                rows.setdefault(sheet, []).append(dict(sheet=sheet, row=i, cat=r[0], desc=ro.clean(r[1]),
                                                       effect=ro.clean(r[2] or ""), stack=ro.clean(r[3] or ""),
                                                       notes=ro.clean(r[4] or "") if len(r) > 4 else ""))
    return rows


def norm(s):
    return re.sub(r"[^a-z0-9+\[\]]+", " ", s.lower()).strip()


def find(rows, name, deep_first):
    order = (DEEP, NORMAL) if deep_first else (NORMAL, DEEP)
    for r in rows[order[0]]:   # exact (normalized) match in the preferred sheet wins, e.g. deep "Increased Maximum HP"
        if norm(r["desc"]) == norm(name):
            return r
    for pat, (sheet, prefix) in OVERRIDES.items():
        if re.search(pat, name):
            if prefix is None:
                prefix = NAME_ROWS[name.split()[0]]
            hits = [r for r in rows[sheet] if r["desc"].lower().startswith(prefix.lower())]
            if hits:
                return hits[0]
    for sheet in order:   # exact (normalized) description match
        for r in rows[sheet]:
            if norm(r["desc"]) == norm(name):
                return r
    return None


DEFAULT_REF = Path(__file__).with_name("reference") / "useful_info.xlsx"


def build(export, ref_path=DEFAULT_REF):
    rows = sheet_rows(ref_path)
    relics = ro.load_relics(export)
    prev = ro.load_ref()   # keeps an ID's match when only its game text changed (patches reword effects)
    effects, curses = {}, {}
    for r in relics:
        for e, n, c, cn in r["effects"]:
            effects[e] = n
            if c is not None:
                curses[c] = cn
    out, missing = {"effects": {}, "curses": {}}, []
    for kind, table in (("effects", effects), ("curses", curses)):
        for i, n in sorted(table.items()):
            ref = find(rows, n, deep_first=kind == "curses" or str(i).startswith("6")) or prev[kind].get(str(i))
            if ref:
                out[kind][str(i)] = ref
            else:
                missing.append(f"{kind[:-1]} {i}: {n}")
    ro.REF_PATH.write_text(json.dumps(out, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"reference: matched {len(out['effects'])}/{len(effects)} effects, {len(out['curses'])}/{len(curses)} curses")
    for m in missing:
        print("  UNMATCHED (add to OVERRIDES in build_ref.py):", m)


def main():
    src = sys.argv[1] if len(sys.argv) > 1 and sys.argv[1] != "-" else None
    build(src, sys.argv[2] if len(sys.argv) > 2 else DEFAULT_REF)


if __name__ == "__main__":
    main()
