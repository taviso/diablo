# Build Optimization Hints

The core solver finds the shortest path connecting the exact target nodes in
your JSON. Passing `--hints` runs a set of post-optimization checks on the
finished boards to suggest small tweaks that could save points or improve stats:

```bash
$ ./paragon_optimize.py --hints your_d4_build.json
```

All `(row, col)` coordinates in hint messages use the **rotated in-game view**
shown in the terminal visualizer, and each board's hints are printed directly
under its grid diagram.

---

## Hint Checks

### Glyph Thresholds & Radius

#### Glyph Threshold Unmet

If a socketed glyph does not have enough of its required stat in radius (`+40`
for primary-stat glyphs, `+25` for secondary-stat glyphs), this check finds the
minimum Paragon points needed to activate the bonus:

```text
[Glyph Threshold Unmet] Glyph 'Bane' only has 22/25 INT in radius (needs +3 INT to unlock its secondary & legendary bonus). Minimum repair: +2 Paragon points — add INT (+5 INT) at (13, 9), WILL (+5 WILL) at (13, 10) to reach 27/25 INT (+5 INT, +5 WILL) (or use --glyph-strategy=bonus).
```

#### Glyph Radius Breakpoint

For glyphs below level 46 (radius `3` at levels 1–14, radius `4` at levels
15–45), previews how much extra stat your **already-purchased** path will pick
up for free once the glyph reaches Lv. 15 or Lv. 46.

#### Secondary Glyph Over-Invested

Secondary-stat glyphs (e.g., an `INT` glyph on a Rogue) are often socketed just
to unlock their `+25` secondary bonus. If you stacked extra secondary stat
nodes inside the radius while leaving primary stat nodes unselected, this check
calculates how many points you can save by trimming down to the `+25` threshold:

```text
[-9 pts] Secondary-stat Glyph 'Nightstalker' has 74/25 INT in radius (+49 INT above its 25 threshold, while 12 in-radius DEX nodes are unselected) — if socketed only for its 25 INT secondary threshold, unmarking 14 in-radius INT targets leaves the cluster transit path at 25/25 INT and saves 9 Paragon points (-5 DEX, -49 INT, +5 WILL, trading -49 INT of 'Nightstalker' primary bonus scaling (74 -> 25 INT)).
```

#### In-Radius 1-Pt Upgrade

Finds unselected stat or `+7` Magic stat nodes inside a glyph radius that are
directly adjacent to your path, giving extra glyph scaling or `+7` secondary
stat for a single Paragon point.

---

### Stat & Path Efficiency

#### Inefficient Stat Target

Flags normal (`+5`) or Magic (`+7`) stat targets that require 1 or more bridge
nodes to reach (costing 2+ points for a single stat node), checks that the
glyph threshold stays met if removed, and suggests a 1-point adjacent swap:

```text
[-2 pts] Target INT (+5 INT) at (14, 12) costs 2 Paragon points (requires bridge DEX (+5 DEX) at (15, 12)) — disabling it saves 2 points (-5 DEX, -5 INT) ('Nightstalker' stays at 69/25 INT); or swap for 1-pt adjacent DEX (+5 DEX) at (13, 13) to save 1 net point (-5 INT).
```

If two stat targets share the same bridge node, it also reports the combined
savings of dropping both together.

#### Manual Path Targets Outside Radius

Flags plain `+5` stat nodes outside any glyph radius that were left marked as
mandatory targets in an exported build and cost extra detour points (which can
also be cleared automatically with `--relax-path-nodes`).

---

### Clusters, Spurs & Alternative Boards

#### High-Cost Target Spur

Flags isolated Rare/Magic targets—or a Legendary node that is the only target
on a leaf board—sitting at the end of a long dedicated spur (3+ points). If it
is the sole target on a leaf board, it also calculates the exact points saved by
dropping the entire board and re-solving the layout:

```text
[-17 pts] Enabling Legendary 'Danse Macabre' at (10, 7) costs 11 Paragon points (1 target + 10 path nodes, -25 DEX, -10 INT, -15 STR) (sole target on this leaf board: removing the whole board saves exactly 17 Paragon points with the layout re-solved [-40 DEX, -20 INT, -30 STR, -5 WILL]) — consider disabling if not build-critical.
```

#### Cluster Spur & Periphery Cost

Calculates the true point cost of each Rare/Magic cluster outside the glyph
radius, along with how many points you can save by keeping the Rare node and
dropping the outer Magic nodes:

```text
[-8 pts] Rare 'Spearhead' cluster around (3, 5) (6 target nodes) requires 2 dedicated path nodes — disabling this cluster saves 8 Paragon points (-5 DEX, -5 STR); or keep Rare 'Spearhead' and drop 3 leaf Magic nodes ((4, 5), (2, 6), (2, 5)) to save 3 points.
```

#### Alternative Board (Unused Legendary Node)

When a non-starter board has a socketed Glyph but its Legendary node is not
selected, checks all unused boards for your class (across all 4 rotations) to
see if a different board can connect the same exit gates and reach your Glyph's
bonus threshold in fewer Paragon points (or in the same minimum points with a
higher in-radius stat capacity):

```text
[-4 pts] Legendary 'Inner Beast' is unused on this board — switching to unused board 'Earthen Devastation' (Paragon_Druid_02, rotated 0°) connects the RIGHT/BOTTOM exit(s) + Glyph socket in 20 pts instead of 21 (-1 pt) and reaches 'Bane' threshold (25/25 INT) in 22 Paragon points instead of 26 (saving 4 points, max 42 INT in radius) (if you only need this board for its Glyph socket / transit path and not Rare 'Tenacity', Rare 'Impel' (+10 WILL)).
```

---

### Unselected Nodes & Glyph Swaps

#### Nearby & Reroute-Accessible Rare/Legendary Nodes

Finds unselected Rare or Legendary nodes near your path that can be picked up
for **0–2 additional points**—including nodes 3–4 steps away that can be
reached for 1 point by shifting your connecting corridor:

```text
[Reroute +1-Pt Rare/Legendary] Unselected Rare 'Skulker' at (13, 5) (with 3 adjacent Magic cluster nodes) is 3 steps from your current path, but rerouting 7 path nodes unlocks it for only +1 additional Paragon point (-10 DEX, +5 INT, -5 STR, +10 WILL).
```

#### Glyph Socket Swap

Checks all pairs of boards to see if swapping two socketed glyphs either:

1. Immediately gives higher scaling on your current path for **0 points**, or
2. Moves a primary-stat glyph onto a board with a higher primary-stat ceiling in its radius:

```text
[Glyph Socket Swap] Board #2 'Exploit Weakness' has a higher in-radius DEX ceiling (110 DEX at R=5) than #3 'Leyrana's Instinct' (105 DEX), while #3 'Leyrana's Instinct' has the same 74 INT radius ceiling — consider moving 'Nightstalker' (INT) to #3 'Leyrana's Instinct' and socketing 'Headhunter' (DEX) here (+5 DEX radius ceiling before pathing costs).
```

---

## Adding Custom Hint Passes

Hint passes live in `hints.py`. To add a new check, decorate a function with
`@register_hint_pass`:

```python
from .hints import Hint, HintContext, register_hint_pass

@register_hint_pass("My Custom Check")
def _hint_pass_custom(ctx: HintContext) -> list[Hint]:
    b = ctx.board
    hints = []
    # Use ctx.marginal_ablation(...), ctx.insertion_cost(...), etc.
    return hints
```
