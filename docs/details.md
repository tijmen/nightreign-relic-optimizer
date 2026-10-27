# Details

Finds the highest-scoring relic loadout over every vessel, for each nightfarer, in either mode:
- **Deep of Night** (`don`): 3 normal + 3 deep slots.
- **Normal expedition** (`normal`): only the 3 normal slots, and deep relics are never picked.

Each nightfarer has its own preferences in `prefs/<character>.toml` (weights, vessels, damage model). `raider.toml` is
the hand-tuned original. The other nine were seeded on 2026-10-27 from Raider's generic weights plus a per-character
profile (weapon classes, scaling stats, spells, their own tagged effects, stat-swap curses). Tune them as you play.

## Relic source
By default relics are read from your save, `%APPDATA%\Nightreign\<steam id>\NR0000.sl2` (the most recently written one;
read-only, `sl2_reader.py`). With several characters in one save, the slot with the most relics is used; pick another
with `--slot NAME`. `uv run sl2_reader.py` lists the slots. You can also pass a save path, or an
`all_relics_exported_YYYYMMDD.xlsx` export from the relic editor, as the first argument to any script. Relic names,
colors, deep-ness and effect names come from the game tables in `reference/save_editor/` (en_US).

## Workflow after new relics or a game patch
1. `uv run relic_opt.py --sync`  (re-maps IDs to the reference sheet, then adds any new effect/curse IDs to every
   `prefs/*.toml` at weight 0, tagged `# --- new in ...`, in [stacking] or [once] per the sheet). This needs
   `reference/useful_info.xlsx`, which is not in the repo (see "Stacking rules").
2. Set weights for those new lines.
3. `uv run relic_opt.py`

Options: `--character Duchess` (default: all), `--mode don|normal` (default: the file's `mode`, else `don`),
`--top N`, `--per-vessel N`, `--vessel "Urn"` (name filter), `--prefs other.toml`.

## Interactive editor
`uv run relic_ui.py` opens http://127.0.0.1:8765/. Pick the nightfarer in the header and
Deep of Night / Normal above the results (both remembered per browser). Edit weights / stacking / vessels in the table
and the top loadouts re-solve on every change (~1.5 s). Click an effect in a result to jump to its weight. "Save to
prefs/<character>.toml" writes edits back (comments and layout kept); unsaved edits are lost on reload.

## Stacking rules (source: reference sheet)
`reference/useful_info.xlsx` is the community "Nightreign Useful Info (1.03.2 + DLC)" sheet (not in the repo: download
it and save it there to rebuild references). Its result, `reference/effect_ref.json`, is included. `build_ref.py` maps every
effect/curse ID in an export to its row (`reference/effect_ref.json`); unmatched names are printed — add a pattern to
`OVERRIDES` in `build_ref.py`. The optimizer warns at startup when a prefs file stacks an effect differently from the sheet,
and the editor shows each effect's sheet text with a red "sheet says" flag on any disagreement.
Rules the sheet only gives in its notes live in code, not preferences: `REF_KIND_OVERRIDE` in `relic_opt.py`
(Improved Critical Hits +0 stacks, +1 doesn't) and `rules.py` (alias IDs of one effect, rally +1/+2 = +25%/+35% of the
base rally weight, effects that don't stack with each other). New sheet version: replace `reference/useful_info.xlsx`
and run `--sync`.

## Damage model (`[damage_model]` in each prefs file, `rules.py`)
Physical Attack Up tiers, Improved Melee Attack Power, Strength +n and the two Strength curses are computed, not
weighted by hand: 1 point = `points_per_pct_damage` per 1% damage; Strength uses the game's stat-scaling curves and
the character's stats per level (`reference/game_data.json`, extracted by `build_game_data.py` from the community Attack Power
Calculator, `reference/ap_calculator.xlsx`, not in the repo) for the chosen `weapon`, averaged over `level_weights`, with `two_handed`
share. Weight your other damage effects in the same unit (% damage) so everything is comparable.
`derive_dex = true` (set for Ironeye, Duchess, Executor) computes Dexterity +n and the two Dexterity curses the same way.
A derived stat curse's line in `[curses]`, if present, is added on top and prices the curse's *other* stat (e.g. the
Intelligence half of "Reduced Strength and Intelligence" for Recluse). Scholar and Undertaker have no per-level stats in
the calculator (it predates the DLC), so their model uses the sheet's level-15 stats for every level. Casters use a
low `points_per_pct_damage` because physical damage is a small part of what they deal. Scholar's and Undertaker's
weapons are placeholders: set the one you use.

`uv run test_solver.py` cross-checks the solver against brute force on random
small inventories and random weights.

## Changing preferences (all in `prefs/<character>.toml`)
- **Effect value**: edit the number on its line (search by name or ID). Negative = actively bad.
- **Stacking**: `[stacking]` counts every copy; `[once]` counts once. Move the line to switch.
- **Derived effects** (marked "derived" in the editor) change via the damage model or the base rally weight.
- **Curses**: `[curses]` per copy, plus `curse_count_penalty` per curse of any kind.
- **Vessels**: every character's 11 vessels are listed (colors from the sheet's Chalices tab); comment out a line to exclude it.
- **Mode**: optional top-level `mode = "normal"` makes normal expeditions the CLI default for that character.
- Effects for other characters are inert and score 0 automatically.
- **Normal vs DoN**: one set of weights serves both modes. If an effect is worth less outside Deep of Night (e.g. Night
  Invader kills), that's a judgment the weights don't capture yet.

Relic numbers in the output are acquisition order in the save (`#12`), or Excel row numbers for an export (`row 12`);
`dupes` lists identical relics.
