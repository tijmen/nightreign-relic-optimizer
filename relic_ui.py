"""Interactive weight editor: edit preference weights in a table, see the top loadouts update live.

Usage:  uv run relic_ui.py [NR0000.sl2 | all_relics_exported_YYYYMMDD.xlsx] [--slot NAME] [--port 8765]
        (default: your save in %APPDATA%\\Nightreign; reload the page after playing to re-read it)

Pick a nightfarer (prefs/<character>.toml) and Deep of Night or normal expedition in the page. Edits are applied in
memory on every change; "Save" writes them back to that character's file (comments and layout kept), so the
command-line relic_opt.py sees the same weights.
"""
import argparse, json, re, threading, time, tomllib, webbrowser
from collections import Counter
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import relic_opt as ro
import rules

LINE = re.compile(r"^(\d+)\s*=\s*(-?[\d.]+)\s*(?:#\s*(.*))?$")
TABLE = re.compile(r"^\[([\w.]+)\]\s*(#.*)?$")
VESSEL = re.compile(r'^(\s*)(#\s*)?(\{\s*name\s*=\s*"(.+?)",\s*normal\s*=\s*"(\w+)",\s*deep\s*=\s*"(\w+)"\s*\},?.*)$')
KIND = {"stacking": "stack", "once": "once", "curses": "curse"}
TABLE_OF = {v: k for k, v in KIND.items()}


def num(w):
    return f"{w:g}"


def parse_lines(text):
    """Entries in file order: (table, category, id, weight, name), plus all vessel lines incl. commented-out ones."""
    entries, vessels, table, cat = [], [], None, ""
    for line in text.splitlines():
        if m := TABLE.match(line):
            table, cat = m.group(1), ""
        elif line.startswith("# --- "):
            cat = line[6:].strip()
        elif (m := LINE.match(line)) and table in KIND:
            entries.append(dict(kind=KIND[table], cat=cat, id=int(m.group(1)), w=float(m.group(2)), name=m.group(3) or ""))
        elif (m := VESSEL.match(line)) and table is None:
            vessels.append(dict(name=m.group(4), normal=m.group(5), deep=m.group(6), on=not m.group(2)))
    return entries, vessels


def toml_value(v):
    if isinstance(v, dict):
        return "{ " + ", ".join(f"{k} = {toml_value(x)}" for k, x in v.items()) + " }"
    if isinstance(v, bool):
        return "true" if v else "false"
    return json.dumps(v) if isinstance(v, str) else num(float(v))


def save(path, st):
    """Write weights/kinds/damage model/globals/vessel toggles back into a prefs file, keeping everything else."""
    lines = Path(path).read_text(encoding="utf-8").splitlines()
    want = {(e["kind"] == "curse", e["id"]): e for e in st["entries"]}
    vessels_on = {v["name"]: v["on"] for v in st["vessels"]}
    model = st.get("damage_model", {})
    out, moved, table = [], [], None
    for line in lines:
        if m := TABLE.match(line):
            table = m.group(1)
        elif table == "damage_model" and (m := re.match(r"^(\w+)(\s*=\s*)(.*?)(\s+#.*)?$", line)) and m.group(1) in model:
            line = m.group(1) + m.group(2) + toml_value(model[m.group(1)]) + (m.group(4) or "")
        elif (m := LINE.match(line)) and table in KIND:
            e = want.get((table == "curses", int(m.group(1))))
            if e:
                new = f"{e['id']} = {num(e['w']):<5} # {e['name']}"
                if TABLE_OF[e["kind"]] != table:          # stacking <-> once switch: move the line
                    moved.append((e, new))
                    continue
                line = new
        elif table is None and (m := re.match(r"^(curse_count_penalty\s*=\s*)(-?[\d.]+)(.*)$", line)):
            line = m.group(1) + num(st["curse_count_penalty"]) + m.group(3)
        elif table is None and (m := VESSEL.match(line)) and m.group(4) in vessels_on:
            line = m.group(1) + ("" if vessels_on[m.group(4)] else "# ") + m.group(3)
        out.append(line)
    for e, new in moved:   # insert under the same category header in the destination table (create it if absent)
        dest = TABLE_OF[e["kind"]]
        start = next(i for i, l in enumerate(out) if l.strip() == f"[{dest}]")
        end = next((i for i in range(start + 1, len(out)) if TABLE.match(out[i]) or out[i].startswith("# ====")), len(out))
        hdr = next((i for i in range(start, end) if out[i] == f"# --- {e['cat']}"), None)
        if hdr is None:
            out[start + 1:start + 1] = [f"# --- {e['cat']}", new]
        else:
            out.insert(hdr + 1, new)
    Path(path).write_text("\n".join(out) + "\n", encoding="utf-8")


class App:
    def __init__(self, export, slot=None):
        self.export, self.slot = export, slot
        self.characters = [c for c in ro.CHARACTERS if ro.prefs_path(c).exists()]
        if not self.characters:
            raise SystemExit(f"no prefs files in {ro.PREFS_DIR}")
        self.lock = threading.Lock()
        self.reload()

    def reload(self):
        """(Re-)read the relics; for a save this picks up whatever the game wrote since."""
        self.relics = ro.load_relics(self.export, self.slot)
        self.source = Path(self.export) if self.export else ro.sl2_reader.default_save()
        inv = Counter()
        for r in self.relics:
            for e, _, c, _ in r["effects"]:
                inv[(False, e, r["deep"])] += 1
                if c is not None:
                    inv[(True, c, r["deep"])] += 1
        self.inv = inv
        self.loaded_mtime = self.source.stat().st_mtime

    def path(self, character):
        if character not in self.characters:
            raise ValueError(f"no prefs file for {character!r}")
        return ro.prefs_path(character)

    def state(self, character):
        if self.source.stat().st_mtime != self.loaded_mtime:   # the game saved: re-read relics
            with self.lock:
                try:
                    self.reload()
                except Exception as ex:   # e.g. caught mid-write; keep the previous relics, retry next load
                    print(f"re-reading {self.source} failed ({ex}); keeping the previous relics")
        path = self.path(character)
        text = path.read_text(encoding="utf-8")
        raw = tomllib.loads(text)
        entries, vessels = parse_lines(text)
        for e in entries:
            c = e["kind"] == "curse"
            e["n"], e["d"] = self.inv[(c, e["id"], False)], self.inv[(c, e["id"], True)]
        prefs = ro.Prefs(None, self.relics, raw)
        ref = ro.load_ref()
        for (k, i), (w, why) in sorted(prefs.derived.items()):   # read-only rows computed by rules.py
            c = k == "c"
            entries.append(dict(kind="curse" if c else "stack" if i in prefs.stack else "once", cat="Derived from game rules",
                                id=i, w=w, name=(prefs.curse_names if c else prefs.names).get(i, str(i)), derived=True,
                                why=why, n=self.inv[(c, i, False)], d=self.inv[(c, i, True)]))
        for e in entries:   # reference-sheet row for the table, plus its stacking verdict and any game rule
            e["rule"] = None if e["kind"] == "curse" else prefs.rule_notes.get(e["id"])
            r = ref["curses" if e["kind"] == "curse" else "effects"].get(str(e["id"]))
            e["ref"] = r and dict(sheet=r["sheet"], row=r["row"], desc=r["desc"], effect=r["effect"],
                                  stack=r["stack"], notes=r["notes"])
            e["ref_kind"] = None if e["kind"] == "curse" else ro.ref_kind(e["id"], ref)
        game = json.loads(rules.GAME_DATA.read_text(encoding="utf-8"))
        return dict(export=self.source.name, relic_count=len(self.relics), prefs=f"prefs/{path.name}", character=character,
                    characters=self.characters, mode=prefs.mode, entries=entries, vessels=vessels,
                    damage_model=prefs.model.cfg, weapons=sorted(game["weapons"]),
                    curse_count_penalty=raw.get("curse_count_penalty", 0),
                    unknown=len(prefs.unknown_effects) + len(prefs.unknown_curses))

    def solve(self, st):
        raw = tomllib.loads(self.path(st["character"]).read_text(encoding="utf-8"))
        deep = st.get("expedition", "don") == "don"
        user = [e for e in st["entries"] if not e.get("derived")]
        raw["stacking"] = {str(e["id"]): e["w"] for e in user if e["kind"] == "stack"}
        raw["once"] = {str(e["id"]): e["w"] for e in user if e["kind"] == "once"}
        raw["curses"] = {str(e["id"]): e["w"] for e in user if e["kind"] == "curse"}
        raw["damage_model"] = st["damage_model"]
        raw["curse_count_penalty"] = st["curse_count_penalty"]
        raw["vessels"] = [v for v in st["vessels"] if v["on"]]
        t0 = time.perf_counter()
        with self.lock:
            prefs = ro.Prefs(None, self.relics, raw)
            types = ro.relic_types(self.relics, prefs)
            n = st.get("n", 3)
            if st.get("mode") == "per_vessel":
                _, per = ro.best_builds(types, prefs, raw["vessels"], 0, 1, deep)
                res = sorted((p[0] for p in per.values() if p), key=lambda r: -r["score"])[:n]
            else:
                res, _ = ro.best_builds(types, prefs, raw["vessels"], n, 0, deep)
        derived = {f"{k}{i}": dict(w=w, why=why) for (k, i), (w, why) in prefs.derived.items()}
        return dict(builds=[self.describe(r, prefs, raw["vessels"], deep) for r in res], derived=derived,
                    ms=round((time.perf_counter() - t0) * 1000))

    def describe(self, res, prefs, vessels, deep=True):
        v = next(x for x in vessels if x["name"] == res["vessel"])
        colors = v["normal"] + v["deep"]
        seen, slots = set(), []
        losers = ro.exclusive_losers({e for _, t in res["build"] for e, *_ in t["effects"]}, prefs)
        for s, t in res["build"]:
            effs = []
            for e, name, c, cn in t["effects"]:
                if e in prefs.stack:
                    w, note = prefs.stack[e], "per copy"
                elif prefs.inert(e):
                    w, note = 0, f"inert: not {prefs.character}"
                else:
                    w, note = prefs.once.get(e, 0), "once"
                    if w and e in seen:
                        w, note = 0, "duplicate: already counted"
                    elif w and e in losers:
                        w, note = 0, "exclusive: a better effect of the same group counts instead"
                    seen.add(e)
                for g, (ids, gw, mode) in prefs.groups.items():
                    if mode == "any" and e in ids:
                        if g not in seen:
                            w, note = w + gw, f"{note} + {g} mechanic {num(gw)}"
                        seen.add(g)
                effs.append(dict(id=e, name=name, w=w, note=note,
                                 curse=None if c is None else dict(id=c, name=cn, w=prefs.curse_w(c))))
            slots.append(dict(label=f"{'D' if t['deep'] else 'N'}{s + 1}", color=colors[s], rows=t["rows"], ref=t["ref"],
                              name=t["name"], effects=effs))
        return dict(score=round(res["score"], 2), vessel=res["vessel"], normal=v["normal"], deep=v["deep"] if deep else "",
                    also=res["also"], slots=slots)


def make_handler(app):
    page = Path(__file__).with_name("relic_ui.html")   # re-read per request so page edits need no restart

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def send(self, body, ctype="application/json", code=200):
            body = body if isinstance(body, bytes) else json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            url = urlparse(self.path)
            if url.path == "/":
                self.send(page.read_bytes(), "text/html; charset=utf-8")
            elif url.path == "/api/state":
                c = parse_qs(url.query).get("character", [""])[0]
                self.send(app.state(c if c in app.characters else
                                    "Raider" if "Raider" in app.characters else app.characters[0]))
            elif self.path == "/favicon.ico":
                self.send(b"", "image/x-icon", 204)
            else:
                self.send(b"not found", "text/plain", 404)

        def do_POST(self):
            st = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            try:
                if self.path == "/api/solve":
                    self.send(app.solve(st))
                elif self.path == "/api/save":
                    with app.lock:
                        save(app.path(st["character"]), st)
                    self.send(dict(ok=True))
                else:
                    self.send(b"not found", "text/plain", 404)
            except Exception as ex:   # surface errors in the page instead of killing the request silently
                self.send(dict(error=f"{type(ex).__name__}: {ex}"), code=500)

    return H


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("export", nargs="?", help="NR0000.sl2 or .xlsx export (default: your save in %%APPDATA%%\\Nightreign)")
    ap.add_argument("--slot", help="save slot: player name or index (default: the one with the most relics)")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--no-browser", action="store_true")
    a = ap.parse_args()
    app = App(a.export, a.slot)
    srv = ThreadingHTTPServer(("127.0.0.1", a.port), make_handler(app))
    url = f"http://127.0.0.1:{a.port}/"
    print(f"Relic weight editor at {url}  (Ctrl+C to stop)")
    if not a.no_browser:
        webbrowser.open(url)
    srv.serve_forever()


if __name__ == "__main__":
    main()
