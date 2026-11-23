"""Which relics to keep: favorite these in game, then sell everything else that isn't favorited.

Reads your save (read-only), including each relic's in-game favorite flag. A relic is worth keeping if it's in
(Deep of Night or Normal):
  * any nightfarer's best loadout on some vessel, or
  * any nightfarer's top --top (3) loadouts overall, or
  * a main's top --main-per-vessel (3) loadouts on some vessel, or
  * on its own it scores >= --keep-score (12) for a main.
Identical copies count: if a loadout needs 2 copies of a relic and you've favorited 1, one more is listed.
Only relics you haven't favorited yet are listed. Uses the weights in prefs/*.toml as they are on disk.
The web UI (relic_ui.py, "Keep & sell" tab) shows the same list; this writes it to favorites.md.

Usage: uv run favorites.py [save.sl2] [--slot NAME] [--main Raider [--main Wylder ...]] [--keep-score 12] [--top 3]
       [--main-per-vessel 3]
"""
import argparse, struct, tomllib
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import relic_opt as ro
import sl2_reader as s

HERE = Path(__file__).parent
MODE_NAME = {"don": "DoN", "normal": "Normal"}


def favorites(path, slot=None):
    """Rows (1 = oldest, as in sl2_reader.load) of favorited relics, and the relic count. The favorite flag is byte 12
    of a relic's 14-byte inventory entry."""
    found = []
    for data in s.user_data(path):
        p = s.parse_slot(data)
        if p:
            found.append((p, data))
    if slot is None:
        (_, _), data = max(found, key=lambda f: len(f[0][1]))
    else:
        data = next(d for k, ((name, _), d) in enumerate(found) if str(slot) in (name, str(k)))
    cur, relic_gas = 0x14, set()
    for _ in range(s.STATES):
        ga, _ = struct.unpack_from("<II", data, cur)
        if ga and ga & 0xF0000000 == s.RELIC:
            relic_gas.add(ga)
        cur += s.STATE_SIZE.get(ga & 0xF0000000, 8) if ga else 8
    cur += 0x94 + 0x5B8 + 4
    entries = []
    for _ in range(s.ENTRIES):
        ga, _, acq = struct.unpack_from("<III", data, cur)
        if ga in relic_gas:
            entries.append((acq, data[cur + 12]))
        cur += s.ENTRY_SIZE
    entries.sort()
    return {row for row, (_, fav) in enumerate(entries, 1) if fav}, len(entries)


def load_prefs(character, rel):
    return ro.Prefs(None, rel, tomllib.loads(ro.prefs_path(character).read_text(encoding="utf-8")))


def solve(job):
    """[(reason, Counter(head row of each relic type used))] for one nightfarer and mode: the top `per_vessel`
    loadouts on every vessel, and the top `top` overall. Each vessel's solutions are computed once and shared."""
    character, mode, top, per_vessel, path, slot = job
    rel = ro.load_relics(path, slot)
    p = load_prefs(character, rel)
    types, deep, out, m = ro.relic_types(rel, p), mode == "don", [], MODE_NAME[mode]
    used = lambda build: Counter(t["rows"][0] for _, t in build)
    streams = {v["name"]: ro.solutions(v, types, p, deep) for v in p.vessels}
    got = {n: [] for n in streams}

    def nth(n, i):   # i-th best (score, build) on vessel n, or None
        while len(got[n]) <= i:
            x = next(streams[n], None)
            if x is None:
                return None
            got[n].append(x)
        return got[n][i]

    for n in streams:
        for i in range(per_vessel):
            x = nth(n, i)
            if x is None:
                break
            out.append((f"{character} {m} {'best' if i == 0 else f'#{i + 1}'} on {n}", used(x[1])))
    # top N overall; the same relics on several vessels count once (as in relic_opt.best_builds)
    nxt, seen = {n: 0 for n in streams}, set()
    while len(seen) < top:
        cands = [(x[0], n) for n in streams if (x := nth(n, nxt[n]))]
        if not cands:
            break
        _, n = max(cands)
        u = used(got[n][nxt[n]][1])
        nxt[n] += 1
        key = frozenset(u.items())
        if key not in seen:
            seen.add(key)
            out.append((f"{character} {m} #{len(seen)} overall", u))
    return out


def standalone_scores(rel, chars):
    """row -> {character: score of that relic alone}."""
    sc = defaultdict(dict)
    for c in chars:
        p = load_prefs(c, rel)
        for t in ro.relic_types(rel, p):
            v = t["linear"] + sum(max(p.once[e], 0) for e in t["once_ids"]) + sum(max(p.groups[x][1], 0) for x in t["groups"])
            for row in t["rows"]:
                sc[row][c] = v
    return sc


def recommend(path=None, slot=None, mains=("Raider",), keep_score=12.0, top=3, main_per_vessel=3, workers=8):
    """The relics to favorite. `path`: a save (default: yours) or an .xlsx export (no favorite flags: none counted).
    Returns dict(keep=[relic dict + reasons], total, favorited, sell, mains, keep_score, top, source)."""
    src = Path(path) if path else s.default_save()
    rel = ro.load_relics(str(src), slot)
    if src.suffix.lower() in (".sl2", ".co2"):
        fav, n = favorites(src, slot)
        assert n == len(rel), (n, len(rel))
    else:
        fav = set()
    chars = [c for c in ro.CHARACTERS if ro.prefs_path(c).exists()]
    mains = [c for c in mains if c in chars]
    jobs = [(c, m, top, main_per_vessel if c in mains else 1, str(src), slot) for c in chars for m in ro.MODES]
    with ProcessPoolExecutor(min(workers, len(jobs))) as ex:
        results = [x for out in ex.map(solve, jobs) for x in out]

    need, reasons = defaultdict(int), defaultdict(list)   # head row -> copies needed / why
    for why, used in results:
        for r, x in used.items():
            need[r] = max(need[r], x)
            reasons[r].append(why)
    sc = standalone_scores(rel, mains)
    key = lambda r: (r["color"], r["deep"], tuple(sorted((x, c or 0) for x, _, c, _ in r["effects"])))
    groups = defaultdict(list)
    for r in rel:
        groups[key(r)].append(r["row"])
    keep = {}
    for rows in groups.values():
        head, why = rows[0], list(reasons[rows[0]])
        n_need = need[head]
        best = max(((v, c) for c, v in sc[head].items() if v >= keep_score), default=None)
        if best:
            why.insert(0, f"Scores {best[0]:.1f} for {best[1]}")
            n_need = max(n_need, 1)
        have = sum(x in fav for x in rows)
        for x in [x for x in rows if x not in fav][:max(0, n_need - have)]:
            keep[x] = why
    byrow = {r["row"]: r for r in rel}
    out = [dict(row=row, name=byrow[row]["name"], color=byrow[row]["color"], deep=byrow[row]["deep"],
                effects=[dict(name=nm, curse=cn) for _, nm, _, cn in byrow[row]["effects"]], reasons=keep[row])
           for row in sorted(keep)]
    return dict(keep=out, total=len(rel), favorited=len(fav), sell=len(rel) - len(fav) - len(keep), mains=mains,
                keep_score=keep_score, top=top, source=src.name, has_favorites=src.suffix.lower() in (".sl2", ".co2"))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("save", nargs="?", help="NR0000.sl2 (default: your save in %%APPDATA%%\\Nightreign)")
    ap.add_argument("--slot", help="save slot: player name or index (default: the one with the most relics)")
    ap.add_argument("--main", action="append", choices=ro.CHARACTERS, help="your main nightfarer(s) (default: Raider)")
    ap.add_argument("--keep-score", type=float, default=12.0, help="a main's standalone score that keeps a relic")
    ap.add_argument("--top", type=int, default=3, help="keep relics in every nightfarer's top N loadouts overall")
    ap.add_argument("--main-per-vessel", type=int, default=3, help="keep relics in a main's top N loadouts per vessel")
    ap.add_argument("--out", default=str(HERE / "favorites.md"))
    a = ap.parse_args()

    r = recommend(a.save, a.slot, a.main or ["Raider"], a.keep_score, a.top, a.main_per_vessel)
    lines = ["# Relics to favorite, in order found (oldest first)\n",
             f"{len(r['keep'])} to favorite, {r['sell']} to sell ({r['favorited']} already favorited, {r['total']} total). "
             f"Mains: {', '.join(r['mains'])}; keep score {a.keep_score:g}; every nightfarer's best per vessel and top "
             f"{a.top} overall; mains' top {a.main_per_vessel} per vessel.\n",
             "| Row | Relic | Color | Effects | Reason |", "|---|---|---|---|---|"]
    for k in r["keep"]:
        e = "; ".join(x["name"] + (f" (curse: {x['curse']})" if x["curse"] else "") for x in k["effects"])
        why = k["reasons"][0] + (f" (+{len(k['reasons']) - 1} more)" if len(k["reasons"]) > 1 else "")
        lines.append(f"| {k['row']} | {k['name']} | {k['color']}{' deep' if k['deep'] else ''} | {e} | {why} |")
    Path(a.out).write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"{r['total']} relics, {r['favorited']} favorited, {len(r['keep'])} to favorite, {r['sell']} to sell")
    print(f"wrote {a.out}")


if __name__ == "__main__":
    main()
