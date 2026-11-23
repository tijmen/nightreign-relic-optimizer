# CLAUDE.md

Nightreign Relic Optimizer: finds the best relic loadouts and which relics to keep before selling. Overview: README.md;
workflow, web UI and prefs format: docs/details.md.

## "Which relics should I favorite before I sell the rest?"
The web UI (`uv run relic_ui.py`) has a "Keep & sell" tab for this. From the CLI: `uv run favorites.py --main Raider`
(about 4 s). It reads the save read-only, including each relic's in-game favorite flag, and writes `favorites.md`
(gitignored) listing only the **unfavorited** relics worth keeping. The user favorites those in game and sells
everything else that isn't favorited. Give them the table from `favorites.md` and the summary line (N to favorite,
M to sell).

A relic is listed if, in DoN or Normal, it's in any nightfarer's best loadout on some vessel or top 3 overall, in a
main's top 3 loadouts on some vessel, or scores >= 12 on its own for a main (`--main` repeatable, `--keep-score`,
`--top`, `--main-per-vessel` to adjust). The user mains Raider.
Rows are acquisition order in the *current* save (1 = oldest), so they shift after selling; always re-run, never reuse
an old list.

## Notes
- Python: use `uv run` (no system `python` on PATH; `.venv/Scripts/python.exe` also works).
- Save format: `sl2_reader.py`. The favorite flag is byte 12 of a relic's 14-byte inventory entry (`favorites.py`).
- Never write to the save file.
