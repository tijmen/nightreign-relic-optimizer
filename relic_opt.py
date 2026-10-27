"""Find the best relic loadout across all vessels for each nightfarer, from a relic export + prefs/<character>.toml.

Usage:
    uv run relic_opt.py                 # optimize every character, relics read from your save in %APPDATA%\\Nightreign
    uv run relic_opt.py --sync          # add new effect/curse IDs to every prefs file (weight 0)
    uv run relic_opt.py SOURCE ...      # SOURCE: a NR0000.sl2 save, or an all_relics_exported_YYYYMMDD.xlsx export
Options: --character NAME (default: all), --mode don|normal (default: each file's `mode`), --prefs FILE,
         --top N, --vessel TEXT (only vessels whose name contains TEXT), --per-vessel N, --slot NAME|N (save slot)

Modes: "don" (Deep of Night) fills 3 normal + 3 deep slots; "normal" fills only the 3 normal slots, never deep relics.

Model: each relic scores the sum of its effect weights ([stacking]: per copy; [once]: once per build, no
matter how many copies) + its curse weights + curse_count_penalty per curse. Game rules (rules.py) add derived
weights (damage effects, Strength, rally tiers), merge alias IDs and enforce effects that don't stack with each
other. Solved exactly as an integer program (HiGHS), per vessel; next-best builds are found by excluding each
previous relic set.
"""
import argparse, heapq, itertools, json, re, sys, time, tomllib
from collections import Counter, defaultdict
from pathlib import Path

import pandas as pd
import pulp

import rules
import sl2_reader

NONE = 4294967295
COLOR = {"R": "Red", "G": "Green", "B": "Blue", "Y": "Yellow"}
CHAR_TAG = re.compile(r"^\[(\w+)\]")
CHARACTERS = ["Wylder", "Guardian", "Ironeye", "Duchess", "Raider", "Revenant", "Recluse", "Executor", "Scholar", "Undertaker"]
PREFS_DIR = Path(__file__).with_name("prefs")
MODES = ("don", "normal")


def prefs_path(character):
    return PREFS_DIR / f"{character.lower()}.toml"


def clean(s):
    return " ".join(str(s).split())


# ---------------------------------------------------------------- data

def load_relics(path=None, slot=None):
    """Relics from a save (.sl2, default: the one in %APPDATA%) or a relic-editor .xlsx export. Deep-ness comes from the
    game's relic table (EquipParamAntique.isDeepRelic); `row` is the Excel row, or acquisition order for a save."""
    if path is None or Path(path).suffix.lower() in (".sl2", ".co2"):
        relics, _, _ = sl2_reader.load(path, slot)
        for r in relics:
            r["name"], r["ref"] = clean(r["name"]), "#"
            r["effects"] = [(rules.canon(e), clean(n), c, cn and clean(cn)) for e, n, c, cn in r["effects"]]
        return relics
    tables = sl2_reader.Tables()
    df = pd.read_excel(path)
    relics = []
    for idx, r in df.iterrows():
        effects = []
        for i in (1, 2, 3):
            e = int(r[f"Effect {i} (ID)"])
            if e == NONE:
                continue
            e = rules.canon(e)   # alias IDs of the same effect -> one ID
            c = int(r[f"Sec Effect {i} (ID)"])
            effects.append((e, clean(r[f"Effect {i} (Name)"]),
                            None if c == NONE else c, None if c == NONE else clean(r[f"Sec Effect {i} (Name)"])))
        name = clean(r["Relic Name"])
        rid = int(r["Item ID"]) & 0x00FFFFFF
        deep = tables.relics[rid][1] == "1" if rid in tables.relics else name.startswith("Deep")
        relics.append(dict(row=idx + 2, ref="row ", name=name, color=r["Relic Color"], deep=deep, effects=effects))
    return relics


REF_PATH = Path(__file__).with_name("reference") / "effect_ref.json"   # built by build_ref.py
# Stacking the reference sheet gives only in its notes ("See Notes"):
REF_KIND_OVERRIDE = {
    7040200: "stack",   # Improved Critical Hits +0: stacks with itself and with +1
    7040201: "once",    # Improved Critical Hits +1: doesn't stack with itself
}


def load_ref():
    return json.loads(REF_PATH.read_text(encoding="utf-8")) if REF_PATH.exists() else {"effects": {}, "curses": {}}


def ref_kind(e, ref):
    """'stack' / 'once' per the reference sheet's "Stackable with self?" column, or None if it doesn't say."""
    if e in REF_KIND_OVERRIDE:
        return REF_KIND_OVERRIDE[e]
    st = ref["effects"].get(str(e), {}).get("stack", "").lower()
    return "stack" if st == "yes" else "once" if st in ("no", "n/a") else None


class Prefs:
    def __init__(self, path, relics, data=None):
        """`data` (an already-parsed prefs dict, e.g. from the web UI) overrides reading `path`."""
        p = data if data is not None else tomllib.loads(Path(path).read_text(encoding="utf-8"))
        self.raw = p
        self.character = p.get("character", "Raider")
        self.mode = p.get("mode", "don")
        if self.mode not in MODES:
            sys.exit(f"prefs: mode must be one of {MODES}, not {self.mode!r}")
        self.curse_count_penalty = p.get("curse_count_penalty", 0)
        self.unknown_curse_weight = p.get("unknown_curse_weight", -5)
        self.vessels = p["vessels"]
        stack = {int(k): v for k, v in p.get("stacking", {}).items()}
        once = {int(k): v for k, v in p.get("once", {}).items()}
        if dup := set(stack) & set(once):
            sys.exit(f"prefs: IDs in both [stacking] and [once]: {sorted(dup)}")
        self.stack, self.once = stack, once
        self.curses = {int(k): v for k, v in p.get("curses", {}).items()}
        self.names, self.curse_names = {}, {}
        for r in relics:
            for e, n, c, cn in r["effects"]:
                self.names[e] = n
                if c is not None:
                    self.curse_names[c] = cn
        # Internal rule structures, filled by rules.apply():
        #   groups: name -> (ids, weight, "any"): weight scores once if any member is equipped
        #   exclusive: list of id sets whose members don't stack with each other (only the best counts)
        self.groups, self.exclusive = {}, []
        rules.apply(self)
        if bad := {e for ids in self.exclusive for e in ids if e in self.stack}:
            sys.exit(f"prefs: effects that are exclusive with each other must be in [once], not [stacking]: {sorted(bad)}")
        ruled = {e for ids, _, _ in self.groups.values() for e in ids}
        self.unknown_effects = sorted(e for e, n in self.names.items() if e not in self.stack and e not in self.once
                                      and e not in ruled and not self.inert(e))
        self.unknown_curses = sorted(c for c in self.curse_names if c not in self.curses)

    def inert(self, e):
        """Effect tagged for another character and not explicitly weighted."""
        m = CHAR_TAG.match(self.names.get(e, ""))
        return bool(m) and m.group(1) != self.character and e not in self.stack and e not in self.once

    def curse_w(self, c):
        return self.curses.get(c, self.unknown_curse_weight) + self.curse_count_penalty


# ---------------------------------------------------------------- scoring

def relic_types(relics, prefs):
    """Collapse identical relics (same color, deep-ness, effects and curses) into one type with a row list,
    and drop types that can never add points (an empty slot is always allowed)."""
    types = {}
    for r in relics:
        key = (r["color"], r["deep"], tuple(sorted((e, c or 0) for e, _, c, _ in r["effects"])))
        types.setdefault(key, dict(r, rows=[]))["rows"].append(r["row"])
    out = []
    for t in types.values():
        ids = [e for e, *_ in t["effects"]]
        t["linear"] = (sum(prefs.stack.get(e, 0) for e in ids)
                       + sum(prefs.curse_w(c) for _, _, c, _ in t["effects"] if c is not None))
        t["once_ids"] = {e for e in ids if prefs.once.get(e, 0)}
        t["groups"] = {g for g, (gids, w, mode) in prefs.groups.items() if mode == "any" and w and gids & set(ids)}
        upside = t["linear"] + sum(max(prefs.once[e], 0) for e in t["once_ids"]) \
            + sum(max(prefs.groups[g][1], 0) for g in t["groups"])
        if upside > 1e-9:
            out.append(t)
    return out


def score(build, prefs):
    """Authoritative score of a list of relic types, with a per-effect breakdown."""
    parts = Counter()
    present = set()
    for t in build:
        for e, n, c, cn in t["effects"]:
            present.add(e)
            if e in prefs.stack:
                parts[n] += prefs.stack[e]
            if c is not None:
                parts[f"<<{cn}>>"] += prefs.curse_w(c)
    losers = exclusive_losers(present, prefs)
    for e in present - losers:
        if prefs.once.get(e, 0):
            parts[prefs.names[e]] += prefs.once[e]
    for g, (ids, w, mode) in prefs.groups.items():
        if mode == "any" and w and ids & present:
            parts[f"{g} (group)"] += w
    return sum(parts.values()), parts


def exclusive_losers(present, prefs):
    """Effects that score nothing because a better member of the same exclusive group is equipped."""
    losers = set()
    for ids in prefs.exclusive:
        pos = sorted((e for e in ids & present if prefs.once.get(e, 0) > 0), key=lambda e: -prefs.once[e])
        losers.update(pos[1:])
    return losers


# ---------------------------------------------------------------- MILP

def slots_of(vessel, deep):
    """(color, is_deep) per slot; outside Deep of Night only the 3 normal slots exist."""
    return [(c, False) for c in vessel["normal"]] + ([(c, True) for c in vessel["deep"]] if deep else [])


def solutions(vessel, types, prefs, deep=True):
    """Yield (score, build) for one vessel, best first."""
    slots = slots_of(vessel, deep)
    prob = pulp.LpProblem("relics", pulp.LpMaximize)
    x = {}
    for i, t in enumerate(types):
        for s, (c, deep) in enumerate(slots):
            if t["deep"] == deep and (c == "W" or COLOR[c] == t["color"]):
                x[i, s] = pulp.LpVariable(f"x_{i}_{s}", cat="Binary")
    by_t, by_s = defaultdict(list), defaultdict(list)
    for (i, s), v in x.items():
        by_t[i].append(v)
        by_s[s].append(v)
    for vs in by_s.values():
        prob += pulp.lpSum(vs) <= 1
    u = {i: pulp.lpSum(vs) for i, vs in by_t.items()}
    b = {i: pulp.LpVariable(f"b_{i}", cat="Binary") for i in by_t}
    for i in by_t:
        prob += u[i] <= min(len(types[i]["rows"]), len(by_t[i])) * b[i]
        prob += b[i] <= u[i]

    obj = [types[i]["linear"] * u[i] for i in by_t]
    # once-effects and groups: indicator y = "present in build"
    holders, yv = defaultdict(list), {}
    for i in by_t:
        for e in types[i]["once_ids"]:
            holders[("e", e)].append(i)
        for g in types[i]["groups"]:
            holders[("g", g)].append(i)
    for k, idxs in holders.items():
        w = prefs.once[k[1]] if k[0] == "e" else prefs.groups[k[1]][1]
        y = yv[k] = pulp.LpVariable(f"y_{k[0]}_{k[1]}", 0, 1)
        if w > 0:
            prob += y <= pulp.lpSum(b[i] for i in idxs)
        else:
            for i in idxs:
                prob += y >= b[i]
        obj.append(w * y)
    for ids in prefs.exclusive:   # at most one positive member of an exclusive group scores
        ys = [yv[("e", e)] for e in ids if ("e", e) in yv and prefs.once[e] > 0]
        if len(ys) > 1:
            prob += pulp.lpSum(ys) <= 1
    prob += pulp.lpSum(obj)

    solver = pulp.HiGHS(msg=False)
    while True:
        if prob.solve(solver) != pulp.LpStatusOptimal:
            return
        chosen = [i for i in b if b[i].value() > .5]
        build = []
        for s in range(len(slots)):
            for i in by_t:
                if (i, s) in x and x[i, s].value() > .5:
                    build.append((s, types[i]))
        yield pulp.value(prob.objective), build
        # exclude exactly this set of relic types next time
        prob += pulp.lpSum(b[i] for i in chosen) - pulp.lpSum(b[i] for i in b if i not in chosen) <= len(chosen) - 1


def best_builds(types, prefs, vessels, top, per_vessel, deep=True):
    """Lazily merge per-vessel solution streams into a global top list; identical relic sets across vessels merge."""
    streams = {v["name"]: solutions(v, types, prefs, deep) for v in vessels}
    heap, results, seen, per, tick = [], [], {}, defaultdict(list), itertools.count()

    def advance(name):
        for sc, build in streams[name]:
            heapq.heappush(heap, (-sc, next(tick), name, build))
            return

    for name in streams:
        advance(name)
    while heap and (len(results) < top or any(len(per[v["name"]]) < per_vessel for v in vessels)):
        neg, _, name, build = heapq.heappop(heap)
        key = frozenset(Counter(id(t) for _, t in build).items())
        if key in seen:
            seen[key]["also"].append(name)
        else:
            res = dict(score=-neg, vessel=name, build=build, also=[])
            seen[key] = res
            if len(results) < top:
                results.append(res)
            per[name].append(res)
        if len(per[name]) < max(per_vessel, top):
            advance(name)
    return results, per


# ---------------------------------------------------------------- output

def fmt_relic(slot, t, prefs, vessel):
    colors = vessel["normal"] + vessel["deep"]
    label = f"{'D' if t['deep'] else 'N'}{slot + 1}"
    eff = " | ".join(n + (f" <<{cn}>>" if cn else "") for _, n, _, cn in t["effects"])
    rows = f"{t['ref']}{t['rows'][0]}" + (f" (dupes: {', '.join(map(str, t['rows'][1:]))})" if len(t["rows"]) > 1 else "")
    return f"  {label} [{colors[slot]}] {rows:<10} {t['name']}: {eff}"


def print_build(rank, res, prefs, vessels, deep=True):
    vessel = next(v for v in vessels if v["name"] == res["vessel"])
    total, parts = score([t for _, t in res["build"]], prefs)
    assert abs(total - res["score"]) < 1e-6, (total, res["score"])
    also = f"  (same relics also fit: {', '.join(res['also'])})" if res["also"] else ""
    colors = f"{vessel['normal']} | {vessel['deep']}" if deep else vessel["normal"]
    print(f"#{rank}  {total:.1f}  {res['vessel']} [{colors}]{also}")
    for s, t in res["build"]:
        print(fmt_relic(s, t, prefs, vessel))
    pos = ", ".join(f"{k} {v:+.1f}" for k, v in sorted(parts.items(), key=lambda kv: -kv[1]) if v > 0)
    neg = ", ".join(f"{k} {v:+.1f}" for k, v in sorted(parts.items(), key=lambda kv: kv[1]) if v < 0)
    print(f"     + {pos}")
    if neg:
        print(f"     - {neg}")
    print()


def stacking_mismatches(prefs, ref):
    """(id, prefs table, reference kind) for effects whose [stacking]/[once] placement disagrees with the sheet."""
    out = []
    for table, ids in (("stack", prefs.stack), ("once", prefs.once)):
        for e in ids:
            k = ref_kind(e, ref)
            if k and k != table and e in prefs.names:
                out.append((e, table, k))
    return out


def sync(prefs_path, prefs, export):
    import build_ref
    if Path(build_ref.DEFAULT_REF).exists():
        build_ref.build(export)              # refresh reference/effect_ref.json for any new IDs
    ref = load_ref()
    text = Path(prefs_path).read_text(encoding="utf-8")
    tag = f"# --- new in {Path(export).name if export else 'save'} {time.strftime('%Y-%m-%d')} (weight 0, review)"
    for table, hdr in (("stack", "\n[stacking]\n"), ("once", "\n[once]\n")):
        new = [e for e in prefs.unknown_effects if (ref_kind(e, ref) or "once") == table]
        if new:
            lines = "\n".join(f"{e} = 0     # {prefs.names[e]}" for e in new)
            text = text.replace(hdr, f"{hdr}{tag}\n{lines}\n", 1)
    if prefs.unknown_curses:
        lines = "\n".join(f"{c} = {prefs.unknown_curse_weight:<5} # {prefs.curse_names[c]}" for c in prefs.unknown_curses)
        text = text.replace("\n[curses]\n", f"\n[curses]\n{tag}\n{lines}\n", 1)
    Path(prefs_path).write_text(text, encoding="utf-8")
    print(f"added {len(prefs.unknown_effects)} effects, {len(prefs.unknown_curses)} curses to {prefs_path}")


def run(export, relics, path, a):
    prefs = Prefs(path, relics)
    if a.sync:
        return sync(path, prefs, export)
    deep = (a.mode or prefs.mode) == "don"
    mode_name = "Deep of Night" if deep else "normal expedition"
    print("#" * 20, f"{prefs.character} - {mode_name} - {path}", "#" * 20, "\n")
    if prefs.unknown_effects or prefs.unknown_curses:
        print(f"WARNING: {len(prefs.unknown_effects)} effects scored 0 and {len(prefs.unknown_curses)} curses scored "
              f"{prefs.unknown_curse_weight} because they're not in prefs. Run with --sync to add them:")
        for e in prefs.unknown_effects:
            print(f"   effect {e}: {prefs.names[e]}")
        for c in prefs.unknown_curses:
            print(f"   curse  {c}: {prefs.curse_names[c]}")
        print()
    if bad := stacking_mismatches(prefs, load_ref()):
        print(f"WARNING: {len(bad)} effects stack differently from the reference sheet (prefs wins):")
        for e, table, k in bad:
            print(f"   {e}: {prefs.names[e]}  (prefs: {table}, reference: {k})")
        print()

    vessels = [v for v in prefs.vessels if not a.vessel or a.vessel.lower() in v["name"].lower()]
    top = a.top if a.top is not None else prefs.raw.get("top", 10)
    per_vessel = a.per_vessel if a.per_vessel is not None else prefs.raw.get("per_vessel", 1)
    types = relic_types(relics, prefs)
    results, per = best_builds(types, prefs, vessels, top, per_vessel, deep)

    print(f"{len(relics)} relics ({len(types)} distinct useful), {len(vessels)} vessels\n")
    print("=" * 30, f"TOP {top} OVERALL", "=" * 30)
    for k, res in enumerate(results, 1):
        print_build(k, res, prefs, vessels, deep)
    if per_vessel:
        print("=" * 30, f"BEST {per_vessel} PER VESSEL", "=" * 30)
        for v in sorted(vessels, key=lambda v: -max((r["score"] for r in per[v["name"]]), default=0)):
            for k, res in enumerate(per[v["name"]][:per_vessel], 1):
                print_build(k, res, prefs, vessels, deep)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("export", nargs="?", help="NR0000.sl2 or .xlsx export (default: your save in %%APPDATA%%\\Nightreign)")
    ap.add_argument("--slot", help="save slot: player name or index (default: the one with the most relics)")
    ap.add_argument("--character", default="all", help="one nightfarer, or 'all' (every prefs/*.toml)")
    ap.add_argument("--prefs", help="use this prefs file instead of prefs/<character>.toml")
    ap.add_argument("--mode", choices=MODES, help="don = 3 normal + 3 deep slots; normal = 3 normal slots only")
    ap.add_argument("--top", type=int)
    ap.add_argument("--per-vessel", type=int)
    ap.add_argument("--vessel", help="only vessels whose name contains this text")
    ap.add_argument("--sync", action="store_true", help="append unknown effect/curse IDs to prefs and exit")
    a = ap.parse_args()

    have = [c for c in CHARACTERS if prefs_path(c).exists()]
    if a.prefs:
        paths = [Path(a.prefs)]
    elif a.character.lower() == "all":
        paths = [prefs_path(c) for c in have]
    else:
        name = next((c for c in have if c.lower() == a.character.lower()), None)
        if not name:
            sys.exit(f"no prefs file for {a.character!r}; have: {', '.join(have)}")
        paths = [prefs_path(name)]
    relics = load_relics(a.export, a.slot)
    print(f"{len(relics)} relics from {a.export or sl2_reader.default_save()}\n")
    for path in paths:
        run(a.export, relics, path, a)


if __name__ == "__main__":
    main()
