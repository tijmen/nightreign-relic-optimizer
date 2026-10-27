"""Game-mechanics knowledge applied on top of the weights in prefs/<character>.toml.

Sources: the reference sheet (reference/useful_info.xlsx, "Stackable with self?" + notes) and game params
(reference/game_data.json, from the Attack Power Calculator). Everything here is fact about the game, not preference:
  * ALIASES       - IDs that are the same effect (unique-relic variants); scored as one effect.
  * Rally tiers   - "+1/+2: same as original, but +25%/+35% HP per hit; +1 stacks with +2, neither stacks with +0".
                    You weight the base effect; the tiers are derived from it.
  * EXCLUSIVE     - effects that don't stack with each other; only the best one counts.
  * Damage model  - Physical Attack Up tiers, Improved Melee Attack Power, Strength +n and Strength curses are valued as
                    % damage (points_per_pct_damage per 1%), Strength via the character's real stat scaling across levels.
                    With damage_model.derive_dex, Dexterity +n and the Dexterity curses are derived the same way.
                    A stat curse's [curses] weight, if listed, is added on top: it prices the other stat the curse lowers.
"""
import csv, json, math, re
from pathlib import Path

GAME_DATA = Path(__file__).with_name("reference") / "game_data.json"
LV15_STATS = Path(__file__).with_name("reference") / "sheets" / "Character_Stats_Level_15.tsv"   # covers the DLC heroes

ALIASES = {
    10001: 7032200, 100000: 7032200,   # Taking attacks improves attack power (three IDs, one effect, doesn't stack)
    7001409: 7001402,                  # Physical Attack Up +2
    7006001: 7006000,                  # Improved Stance-Breaking when Two-Handing
    7006101: 7006100,                  # Improved Stance-Breaking when Wielding Two Armaments
    10000: 7036000,                    # FP Restoration upon Successive Attacks
    # unique character relics carry their own copy of a generic effect ID
    10002: 7035900,                    # Fell Omen Fetish: Switching Weapons Boosts Attack Power
    11000: 7033200,                    # The Wylder's Earring: [Wylder] +1 additional Character Skill use
    11002: 7032400,                    # Silver Tear: [Wylder] Art gauge greatly filled when ability is activated
    12002: 7012000, 12003: 7012000,    # Witch's Brooch: [Guardian] Slowly restores nearby allies' HP while Art is active
    16002: 7010900,                    # Old Portrait: [Revenant] Expend own HP to fully heal nearby allies
    17002: 7032900,                    # Bone-Like Stone: [Recluse] Suffer blood loss and increase attack power
    19001: 7036300,                    # Note "My Dear Successor": [Scholar] Allies targeted by Character Skill gain boosted attack
}

RALLY_BASE = 7005600
RALLY_TIERS = {6005600: .25, 6005601: .35}

EXCLUSIVE = {   # name pattern -> why
    r"^Starting armament ": "starting armament element/status effects don't stack with each other",
    r"^Changes compatible armament's ": "one starting armament: only one skill/spell change can apply",
    r"^Dormant Power Helps Discover": "only one preferred weapon class applies",
}

# Unconditional damage multipliers (reference sheet), valued as 100*ln(multiplier) % so stacking copies multiply.
DAMAGE_MULT = {7001400: 1.04, 7001401: 1.05, 7001402: 1.06, 6001400: 1.105, 6001401: 1.12,
               7006600: 1.05}   # Improved Melee Attack Power: 1.05x all attack power
STR_EFFECTS = {7000300: 1, 7000301: 2, 7000302: 3}   # Strength +1/2/3 (stat points)
STR_CURSES = {6830000: -3, 6830300: -3}              # Reduced Strength and Intelligence / Faith and Strength
DEX_EFFECTS = {7000400: 1, 7000401: 2, 7000402: 3}   # Dexterity +1/2/3 (only with derive_dex)
DEX_CURSES = {6830100: -3, 6830200: -3}              # Reduced Dexterity and Faith / Dexterity and Intelligence

DEFAULT_MODEL = dict(points_per_pct_damage=1.0, weapon="Raider's Greataxe", two_handed=0.0,
                     level_weights={"5": 1, "9": 2, "13": 2, "15": 3}, derive_dex=False)


def canon(e):
    return ALIASES.get(e, e)


class DamageModel:
    """% physical attack-power change from a Strength/Dexterity delta, averaged over character levels (and 1H/2H share)."""

    def __init__(self, cfg, character):
        game = json.loads(GAME_DATA.read_text(encoding="utf-8"))
        self.cfg = {**DEFAULT_MODEL, **cfg}
        self.ppp = float(self.cfg["points_per_pct_damage"])
        if self.cfg["weapon"] not in game["weapons"]:
            raise ValueError(f"damage_model.weapon {self.cfg['weapon']!r} not in reference/game_data.json")
        self.wep = game["weapons"][self.cfg["weapon"]]
        self.curve = game["curves"][self.wep["curve"]]
        self.levels = {int(k): float(v) for k, v in self.cfg["level_weights"].items() if float(v) > 0}
        self.note = ""
        if character in game["heroes"]:
            self.stats = {L: game["heroes"][character][str(L)] for L in self.levels}
        else:   # no per-level data (calculator predates the DLC): the sheet's level-15 stats stand in for every level
            with LV15_STATS.open(encoding="utf-8") as f:
                row = next(r for r in csv.DictReader(f, delimiter="	") if r["Name"] == character)
            self.levels = {15: 1.0}
            self.stats = {15: dict(strength=int(float(row["Strength"])), dexterity=int(float(row["Dexterity"])))}
            self.note = f"; level 15 only: no per-level stats for {character}"
        self.two = min(max(float(self.cfg["two_handed"]), 0.0), 1.0)

    def ap(self, strength, dex, two_hand):
        if two_hand and self.wep["two_hand_bonus"]:
            strength = int(strength * 1.5)
        g = lambda s: self.curve[max(1, min(int(s), len(self.curve) - 1))]
        return self.wep["base"] * (1 + self.wep["str"] / 100 * g(strength) / 100 + self.wep["dex"] / 100 * g(dex) / 100)

    def stat_pct(self, stat, delta):
        """Level-weighted mean of 100*ln(AP with `stat` + delta / AP without), in % damage."""
        ds, dd = (delta, 0) if stat == "strength" else (0, delta)
        tot = sum(self.levels.values())
        out = 0.0
        for L, wt in self.levels.items():
            s, d = self.stats[L]["strength"], self.stats[L]["dexterity"]
            for two, share in ((False, 1 - self.two), (True, self.two)):
                if share:
                    out += wt * share * 100 * math.log(self.ap(s + ds, d + dd, two) / self.ap(s, d, two))
        return out / tot

    def mult_pct(self, m):
        return 100 * math.log(m)


def apply(prefs):
    """Fill prefs' effective tables from its user weights + the rules above. Sets prefs.derived / prefs.rule_notes."""
    model = DamageModel(prefs.raw.get("damage_model", {}), prefs.character)
    derived, notes = {}, {}

    def put(table, e, w, why):
        prefs.once.pop(e, None); prefs.stack.pop(e, None)
        table[e] = w
        derived[("e", e)] = (w, why)

    for e, m in DAMAGE_MULT.items():
        pct = model.mult_pct(m)
        put(prefs.stack, e, model.ppp * pct, f"{m}x damage = {pct:.2f}% (stacks multiplicatively)")
    stats = [("strength", "Str", STR_EFFECTS, STR_CURSES)]
    if model.cfg["derive_dex"]:
        stats.append(("dexterity", "Dex", DEX_EFFECTS, DEX_CURSES))
    src = f"with {model.cfg['weapon']} (level-weighted{model.note})"
    for stat, short, effects, curses in stats:
        for e, d in effects.items():
            pct = model.stat_pct(stat, d)
            put(prefs.stack, e, model.ppp * pct, f"+{d} {short} = {pct:+.2f}% damage {src}")
        for c, d in curses.items():
            pct = model.stat_pct(stat, d)
            other = prefs.curses.get(c, 0)   # listed weight = the curse's other stat
            prefs.curses[c] = model.ppp * pct + other
            derived[("c", c)] = (prefs.curses[c], f"{d} {short} = {pct:+.2f}% damage {src}"
                                 + (f"; {other:+g} from [curses] for the other stat" if other else ""))

    # Rally: the base weight is the mechanic; +1/+2 add 25%/35% of it, and the base adds nothing once a tier is present.
    w = prefs.once.pop(RALLY_BASE, 0)
    prefs.groups["rally"] = ({RALLY_BASE, *RALLY_TIERS}, w, "any")
    notes[RALLY_BASE] = "Rally: this weight is the mechanic; +1 / +2 add 25% / 35% of it (they stack with each other, not with this)"
    for e, f in RALLY_TIERS.items():
        put(prefs.once, e, f * w, f"+{f:.0%} of rally weight {w:g}; stacks with the other tier, not with base")

    for pat, why in EXCLUSIVE.items():
        ids = {e for e, n in prefs.names.items() if re.search(pat, n)}
        if ids:
            prefs.exclusive.append(ids)
            for e in ids:
                notes[e] = f"Exclusive: {why}; only the best one counts"
    for a, c in ALIASES.items():
        notes[c] = (notes.get(c, "") + f" Also covers ID {a}.").strip()
    prefs.derived, prefs.rule_notes, prefs.model = derived, notes, model
