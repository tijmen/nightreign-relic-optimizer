# Nightreign relic optimizer

> **This is a vibe-coded project**, made with Claude Opus 5.5 by Tijmen de Haan, 2026-10-27. Feedback is welcome:
> open an issue.

For every nightfarer in *Elden Ring Nightreign*, this finds the best relics to equip across all vessels. It works for
both Deep of Night (3 normal + 3 deep slots) and normal expeditions (3 normal slots). It reads your relic inventory
straight from your save file, scores every relic with your own per-character effect weights (with game rules such as
stacking, curses and stat scaling built in), and solves for the exact best loadout. An editor in the browser re-solves
live as you change the weights.

![The editor showing Raider's best Deep of Night loadouts](docs/raider.png)

## Launching the UI

You need [uv](https://docs.astral.sh/uv/) and Windows (the save is read from `%APPDATA%\Nightreign`).

```
git clone https://github.com/tijmen/nightreign-relic-optimizer
cd nightreign-relic-optimizer
uv run relic_ui.py
```

This opens http://127.0.0.1:8765/ in your browser. Pick a nightfarer at the top and *Deep of Night* or *Normal* above
the results. Edit a weight and the loadouts update. **Save** writes your weights to `prefs/<character>.toml`. The save
file is only read, never written; after playing, reload the page to pick up new relics.

Other sources: `uv run relic_ui.py path/to/NR0000.sl2`, or an `.xlsx` export from the
[Nightreign Relic Editor](https://github.com/alfizari/Elden-Ring-Nightreign-Save-Editor). The command-line version is
`uv run relic_opt.py`. Details: [docs/details.md](docs/details.md).

## Credits

Save format, game text and relic tables: [alfizari/Elden-Ring-Nightreign-Save-Editor](https://github.com/alfizari/Elden-Ring-Nightreign-Save-Editor)
(MIT, see `reference/save_editor/LICENSE`). Effect stacking rules: the community "Nightreign Useful Info" sheet. Stat
scaling: the "Nightreign Attack Power Calculator" by Cryptid Tracker.
