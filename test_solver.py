"""Brute-force cross-check of the MILP on random small inventories, random weights, characters and both modes.
Usage: uv run test_solver.py [NR0000.sl2 | all_relics_exported_YYYYMMDD.xlsx | -] [trials]   (default / "-": your save)"""
import itertools, random, sys, tomllib
from pathlib import Path
import relic_opt as ro


def brute(vessel, types, prefs, deep):
    slots = ro.slots_of(vessel, deep)
    pools = [[None] + [t for t in types if t["deep"] == d and (c == "W" or ro.COLOR[c] == t["color"])] for c, d in slots]
    best = 0.0
    for combo in itertools.product(*pools):
        used = [t for t in combo if t is not None]
        ids = [id(t) for t in used]
        if any(ids.count(i) > len(t["rows"]) for i, t in zip(ids, used)):
            continue
        best = max(best, ro.score(used, prefs)[0])
    return best


def main():
    relics = ro.load_relics(sys.argv[1] if len(sys.argv) > 1 and sys.argv[1] != "-" else None)
    trials = int(sys.argv[2]) if len(sys.argv) > 2 else 30
    files = [ro.prefs_path(c) for c in ro.CHARACTERS if ro.prefs_path(c).exists()]
    rng = random.Random(1)
    for trial in range(trials):
        base = tomllib.loads(rng.choice(files).read_text(encoding="utf-8"))
        deep = rng.random() < 0.5
        raw = dict(base)
        # random weights, including negatives, on every effect/curse/group
        raw["stacking"] = {k: rng.choice([0, 0, rng.uniform(-3, 8)]) for k in base["stacking"]}
        raw["once"] = {k: rng.choice([0, 0, rng.uniform(-3, 8)]) for k in base["once"]}
        raw["curses"] = {k: rng.uniform(-6, 0) for k in base["curses"]}
        raw["once"][str(ro.rules.RALLY_BASE)] = rng.uniform(0, 10)
        raw["damage_model"] = dict(base["damage_model"], points_per_pct_damage=rng.uniform(0.2, 2),
                                   two_handed=rng.choice([0, 0.5, 1]))
        prefs = ro.Prefs(None, relics, raw)
        for ids in prefs.exclusive:            # exclusive effects: positive weights so they compete
            for e in ids:
                prefs.once[e] = rng.uniform(1, 8)
        vessel = rng.choice(base["vessels"])
        # small sample, biased toward relics with group members so groups/exclusives are exercised
        grouped = {e for ids, _, _ in prefs.groups.values() for e in ids}
        hot = [r for r in relics if any(e in grouped for e, *_ in r["effects"])]
        sample = rng.sample(relics, 40) + rng.sample(hot, 20)
        sample += [dict(r) for r in rng.sample(sample, 5)]      # duplicates (same effects, different rows)
        for k, r in enumerate(sample[-5:]):
            r["row"] = 100000 + k
        types = ro.relic_types(sample, prefs)
        got = next(ro.solutions(vessel, types, prefs, deep), (0.0, []))[0]
        exp = brute(vessel, ro.relic_types(sample, prefs), prefs, deep)
        status = "ok" if abs(got - exp) < 1e-6 else "MISMATCH"
        print(f"trial {trial:2} {'DoN' if deep else 'normal':6} {vessel['name']:32} milp {got:7.2f}  brute {exp:7.2f}  {status}")
        if status != "ok":
            sys.exit(1)
    print("all trials match")


if __name__ == "__main__":
    main()
