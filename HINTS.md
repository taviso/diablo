# `HINTS.md` — Paragon Build Optimization Hints & Marginal Cost Analysis (`--hints`)

When running [`paragon_optimize.py`](file:///workspace/diablo/paragon_optimize.py) with `--hints`, the optimizer runs a suite of post-optimization analysis passes defined in [`paragon/hints.py`](file:///workspace/diablo/paragon/hints.py).

Whereas the core Steiner Tree solver finds the mathematically minimum-point path connecting the **exact target nodes** marked in the input JSON, `--hints` answers the next question a player faces when tuning a build:
> *"Which of my selected target nodes, clusters, or boards are costing too many Paragon points to reach, which stat targets or Glyph sockets can be swapped for higher stats at lower cost, and where can I spend 1 point along my path for high return?"*

All coordinates reported by `--hints` are in **rotated in-game screen coordinates `(row, col)`**, matching the terminal visualization output and how the board appears in *Diablo 4*. By default (unless `--no-visualize` is passed), each board's hints are printed directly beneath that board's $21 \times 21$ grid diagram ([`format_board_hints`](file:///workspace/diablo/paragon/hints.py#L648-L664)), making it easy to inspect the referenced `(row, col)` coordinates in place.

---

## 1. What Game Data & Mechanics Are Currently Considered

To evaluate the true value of every node and Glyph on a board, [`paragon/game_data.py`](file:///workspace/diablo/paragon/game_data.py) and [`paragon/board.py`](file:///workspace/diablo/paragon/board.py) incorporate official game data extracted from `DiabloTools/d4data`:

1. **Exact Core Stat Grants (`NORMAL_STAT_GRANT`, `MAGIC_STAT_GRANT`, `RARE_STAT_GRANT`)**:
   - **Normal Stat Nodes (`DEX`, `INT`, `STR`, `WILL`)**: Grant **`+5`** Core Stat (`AttributeFormulas.gam.json` formula `5`).
   - **Magic Stat Nodes (`MAGIC_DEX`, `MAGIC_INT`, `MAGIC_STR`, `MAGIC_WILL`)**: Grant **`+7`** Core Stat (`1.4×` a normal node, formula `5 * 1.4`).
   - **Core-Stat Rare Nodes (`RARE`)**: Out of 344 Rare nodes across all 80 Paragon boards in [`BOARD_SPECIAL_NODES`](file:///workspace/diablo/paragon/game_data.py#L413-L495), 143 include a `ParagonNodeCoreStat_RareMinor` or `RareMajor` affix granting **`+10`** of the class's Primary Stat (`2.0×` a normal node).
   - **Board Attachment Gates (`EXIT`)**: Grant **`+5` to all 4 Core Stats** (`+5 DEX, +5 INT, +5 STR, +5 WILL`).

2. **Socketed Glyph Metadata ([`GLYPH_DATA`](file:///workspace/diablo/paragon/game_data.py#L247-L418))**:
   - Maps all 162 internal Glyph template IDs (e.g., `Rare_056_Dexterity_Main`, `Rare_083_Intelligence_Side`, `Rare_Dex_Generic`, `S02_Rare_Vampiric_01`) to:
     - **In-Game Name**: e.g., *Grenadier*, *Nightstalker*, *Headhunter*, *Ranger*, *Tears of Blood*.
     - **Governed Core Stat**: `DEX`, `INT`, `STR`, `WILL`, or `ALL`.
     - **Bonus Threshold Requirement**: **`40`** Core Stat in radius for Primary-stat (`_Main` / `_Generic`) Glyphs, **`25`** Core Stat in radius for Secondary-stat (`_Side`) Glyphs, or **`50`** All Stats for *Tears of Blood*.
     - **Primary Scaling Mode (`glyph_mode`)**:
       - `'stat'`: Scales per point of governed Core Stat purchased in radius (`+5` normal, `+7` magic, `+10` rare).
       - `'normal'`: Boosts all **Normal stat nodes** in radius by `+200%+` (e.g., *Headhunter*, *Eliminator*, *Challenger*).
       - `'magic'`: Boosts all **Magic nodes** in radius by `+250%–300%+` (e.g., *Unleash*, *Versatility*, *Fluidity*, *Devious*).
       - `'rare'`: Boosts all **Rare nodes** in radius by `+200%–250%+` (e.g., *Tactician*, *Protector*, *Dominate*, *Winter*).

3. **Glyph Manhattan ($L_1$) Radius (`get_glyph_radius`)**:
   - Level `1–14` $\rightarrow R = 3$, Level `15–45` $\rightarrow R = 4$, Level `46+` $\rightarrow R = 5$.

4. **Class Stat Priorities (`DEFAULT_CLASS_NODE_PRIORITY` & `--prioritize-stat`)**:
   - Every class has 1 Primary Stat, 2 Secondary Stats (which appear as `MAGIC_<STAT>` nodes on that class's boards and unlock Rare node secondary bonuses), and 1 Off-Stat (`0.0` priority).

---

## 2. How Marginal Cost & Ablation Analysis Works

Given an optimized board with selected nodes $S^*$ and required target nodes $T$, the **marginal cost** of a subset of target nodes $U \subseteq T$ is the net reduction in total Paragon points when $U$ is unmarked as required:

$$\Delta \text{points}(U) = |S^*(T)| - |S^*(T \setminus U)|$$

Along with the scalar point savings $\Delta \text{points}(U)$, marginal ablation computes the exact set difference between the old and new trees:
- **`removed_coords` ($S^*(T) \setminus S^*(T \setminus U)$)**: Target and path nodes that are no longer purchased.
- **`added_coords` ($S^*(T \setminus U) \setminus S^*(T)$)**: New path nodes purchased if unmarking $U$ causes the optimal Steiner tree to **reroute** along a shorter or higher-priority corridor.
- **Net Core Stat Delta (`HintContext.net_stat_changes`)**: The exact change in `DEX`, `INT`, `STR`, and `WILL` between `added_coords` and `removed_coords` (accounting for `+5` normal, `+7` magic, and `+10` rare stat grants).

### Hybrid Fast Ablation (`HintContext`)
Naively re-running a full 4-exit-bit Steiner tree solver for every individual target node and sub-cluster on a 5-board build (146 targets in [`tests/eternal_rogue_sample.json`](file:///workspace/diablo/tests/eternal_rogue_sample.json)) would be slow because removing an *interior* node of a connected cluster splits 1 cluster into 2–4 disconnected components, inflating the Dreyfus–Wagner terminal count $k$.

Instead, [`HintContext`](file:///workspace/diablo/paragon/hints.py#L64-L280) combines five techniques:

1. **Cluster-Aware Candidate Filtering**:
   - Multi-node Rare/Magic clusters (`CLUSTER_NODE_TYPES`) are analyzed as connected components in the cluster pass rather than splitting their interior nodes during single-node passes.
   - Inside a Glyph radius, single-node stat ablation only tests **leaf targets** (degree 1 in $S^*$) or **isolated targets** (0 adjacent target neighbors), avoiding artificial cluster fragmentation.
2. **Exact Leaf-Spur Walking (`HintContext.trace_leaf_spur`)**:
   - When a single target node $u$ (or active exit gate $e$) is a **leaf** in the selected tree $S^*$ (only 1 selected neighbor), we walk backward along degree-2 non-required path nodes until reaching an anchor vertex $a$:
     - If $a$ is itself a required node ($a \in T \cup \text{ActiveExits}$), then removing the spur `spur = [u, ...]` leaves $S^* \setminus \text{spur}$ connected and spanning all remaining terminals $T \setminus \{u\}$. Because $S^*$ was globally optimal for $T$, $S^* \setminus \text{spur}$ is mathematically guaranteed to be optimal for $T \setminus \{u\}$ in **$O(\text{spur length})$ time** with zero solver calls!
3. **Fast $O(|S^*|)$ Topological Subgraph Peeling (`HintContext.peel_ablation`)**:
   - For sub-cluster periphery pruning (e.g., keeping a cluster's Rare node while dropping 2–3 leaf Magic nodes) and 2-node shared-bridge pairs, `peel_ablation` removes the candidate subset from $S^*$, iteratively peels any non-required path nodes whose degree drops to $\le 1$, and verifies in microseconds whether the remaining subgraph stays connected and spans all remaining required nodes (providing a fast feasible lower bound on point savings without an exact re-solve).
4. **Cached 0-Exit-Bit Exact Steiner Re-Solving (`HintContext.solve_for_terminals`, `solve_without`, `insertion_cost`)**:
   - Whenever a target node or cluster is *not* a simple leaf anchored at a required terminal (for example: a leaf spur anchored at a degree-3 Steiner junction, a peripheral cluster node whose removal allows the path to straighten out, an exit gate spur, an out-of-radius Rare/Magic cluster, or an unselected Rare/Legendary insertion test), `solve_for_terminals` calls `solve_board_exact(..., fixed_terminals=terms)`.
   - Because the board's active exit gates are fixed on the optimized board, `fixed_terminals` solves with **0 exit bits** ($16\times$ fewer bitmask states than the initial 4-exit-bit solve) and caches results in `HintContext._solve_cache`.
   - Note that hints evaluate **alternative local edits** on the current layout; point savings across different hints on the same board can overlap and should not be summed without re-running `--hints`.
5. **Exact Whole-Board Removal (`HintContext.board_removal`, `solver.solve_layout_without`)**:
   - `optimize_layout_and_paths` caches every board's all-exit-mask Steiner solutions (`ParagonBoard.exit_solutions`), so removing a board only re-runs the 2D layout/rotation search (~3 ms) on the remaining boards — the **globally exact** savings with every other board's exits, rotation, and path re-chosen (on the sample build, removing `#4 Cheap Shot` saves 50 Paragon points vs. the 47-point fixed-layout estimate).
   - If a complex board ever takes $> 2.0$ seconds during `--hints`, [`run_optimization_hints`](file:///workspace/diablo/paragon/hints.py#L1268-L1301) automatically displays a live progress indicator on `stderr`.

---

## 3. The 8 Optimization Hint Passes

Hint passes are modular functions registered via `@register_hint_pass(name)` in [`paragon/hints.py`](file:///workspace/diablo/paragon/hints.py).

### Pass 1: Glyph Threshold, Synergy & Over-Investment (`_hint_pass_glyph_synergy`)
Checks how effectively the current path utilizes each board's socketed Glyph:
- **`[Glyph Threshold Unmet]` & Exact Minimum Repair (`HintContext.exact_threshold_repair`)**: Sums the exact governed Core Stat purchased inside the Glyph's Manhattan radius (`+5` normal, `+7` magic, `+10` rare). If `cur_stat < glyph_threshold` (`25` or `40`), finds the minimum-point selection (with the board's active exits fixed) that reaches the threshold, including reroutes that drop existing path nodes:
  - OPT $= \min_S |\text{Steiner}(T \cup S)|$ over sets $S$ of in-radius governed-stat nodes (the optimal tree $T^*$ is reached at $S^* = T^* \cap \text{candidates}$). A uniform-cost search expands candidate sets in order of tree size (monotone in $S$), branching only on candidates outside the current tree, so the first tree meeting the threshold is a proven minimum.
  - The search is capped at 300 cached exact re-solves; if the cap is hit, the hint reports the best repair found together with a proven lower bound (`Best repair found: +4 Paragon points (search limit reached; the minimum is at least +1)`), and reports `Minimum repair` otherwise.
  - *Example on `tests/Shred_Endgame_Druid.json`*:
    > `[Glyph Threshold Unmet] Glyph 'Bane' only has 22/25 INT in radius (needs +3 INT to unlock its secondary & legendary bonus). Minimum repair: +2 Paragon points — add INT (+5 INT) at (13, 9), WILL (+5 WILL) at (13, 10) to reach 27/25 INT (+5 INT, +5 WILL) (or use --glyph-strategy=bonus).`
- **`[Glyph Radius Breakpoint]`**: For Glyphs below level 46 ($R=3$ at levels 1–14, $R=4$ at levels 15–45), previews how much additional governed stat the **already-purchased** path provides at `Lv.15 (R=4)` and `Lv.46 (R=5)` for **0 additional Paragon points**.
- **`[-N pts] Secondary Glyph Over-Invested`**: When a board sockets a **Secondary-Stat Glyph** (governed stat $\ne$ class Primary Stat from `DEFAULT_CLASS_NODE_PRIORITY`, e.g., `Nightstalker` `INT` on Rogue with threshold `25`) and purchases $\ge 15$ stat points above the threshold while leaving in-radius Primary Stat nodes unselected, tests whether unmarking the in-radius secondary-stat targets leaves the natural cluster transit path at or above the threshold, explicitly reporting both the point savings and the primary bonus scaling tradeoff.
  - *Example on `tests/eternal_rogue_sample.json` (`#2 Exploit Weakness`)*:
    > `[-9 pts] Secondary-stat Glyph 'Nightstalker' has 74/25 INT in radius (+49 INT above its 25 threshold, while 12 in-radius DEX nodes are unselected) — if socketed only for its 25 INT secondary threshold, unmarking 14 in-radius INT targets leaves the cluster transit path at 25/25 INT and saves 9 Paragon points (-5 DEX, -49 INT, +5 WILL, trading -49 INT of 'Nightstalker' primary bonus scaling (74 -> 25 INT)).`
- **`[In-Radius 1-Pt Upgrade]` & `[In-Radius 1-Pt Magic Stat]`**: Identifies unselected nodes inside the Glyph radius that are **orthogonally adjacent** to the current path (costing only **1 Paragon point** with zero path overhead) and either scale the socketed Glyph's primary bonus or grant **`+7` secondary Core Stat** (`MAGIC_<STAT>`, `1.4×` a normal `+5` stat node).

### Pass 2: Stat Target Efficiency, Shared Bridges & Swaps (`_hint_pass_stat_target_swaps`)
Evaluates normal (`+5`) and Magic (`+7`) stat target nodes to find inefficient stat investments:
- **`[-N pts] Inefficient Stat Target`**: Triggers when a single stat target node $u$ has a marginal cost of **$\ge 2$ Paragon points** (requiring 1+ bridge/detour nodes). Reports:
  1. The exact bridge node(s) required and net Core Stat delta when disabled,
  2. Whether the board's **Glyph bonus threshold remains satisfied** on the ablated tree `new_selected`,
  3. The best **validated 1-point adjacent swap** directly adjacent to `new_selected` (ranked by in-radius Glyph synergy, class/stat priority, stat grant, and proximity to $u$), and
  4. **Shared-Bridge Pair Savings**: If a second stat target shares $u$'s bridge node such that disabling both together collapses the bridge (`peel_ablation` saves $\ge 3$ points), reports the combined 2-target savings and resulting Glyph stat!
  - *Example on `tests/eternal_rogue_sample.json` (`#2 Exploit Weakness`)*:
    > `[-2 pts] Target INT (+5 INT) at (14, 12) costs 2 Paragon points (requires bridge DEX (+5 DEX) at (15, 12)) — disabling it saves 2 points (-5 DEX, -5 INT) ('Nightstalker' stays at 69/25 INT); or swap for 1-pt adjacent DEX (+5 DEX) at (13, 13) to save 1 net point (-5 INT).`
- **`[-1 pt] Path Reroute Savings`**: Triggers when unmarking a stat target $u$ saves 1 Paragon point while rerouting the path through higher-value stat nodes.

### Pass 3: High-Cost Single Target Nodes & Whole-Board Removal (`_hint_pass_high_cost_single_targets`)
Evaluates isolated Rare or Magic leaf target nodes (outside multi-node clusters) — or a Legendary node that is the sole target on a leaf board — that sit at the end of long dedicated path spurs:
- **`[-N pts] High-Cost Target Spur`**: Triggers when an isolated Rare/Magic target node (or a sole-target leaf board's Legendary node) has a marginal cost of **$\ge 3$ Paragon points** (`1` target + `2+` dedicated path nodes). Non-sole Legendary nodes are skipped since players pick a board specifically for its Legendary node.
- **Exact Whole-Board Removal for Sole-Target Leaf Boards (`HintContext.board_removal`)**: When the target is the **sole target on a leaf board** (1 active exit; the Glyph socket is not counted as a target), removing it means removing the whole board, so the hint reports the exact savings and Core Stat delta with the remaining layout fully re-solved (including the parent board's now-unneeded gate spur and any re-chosen exits/rotations).
  - *Example on `tests/eternal_rogue_sample.json` (`#5 Danse Macabre`)*:
    > `[-17 pts] Enabling Legendary 'Danse Macabre' at (10, 7) costs 11 Paragon points (1 target + 10 path nodes, -25 DEX, -10 INT, -15 STR) (sole target on this leaf board: removing the whole board saves exactly 17 Paragon points with the layout re-solved [-40 DEX, -20 INT, -30 STR, -5 WILL]) — consider disabling if not build-critical.`

### Pass 4: Exact Board Removal Cost (`_hint_pass_board_removal_cost`)
Informational `[Board Cost]` hint triggered when a non-starter board's true build cost (`solve_layout_without`) differs from its on-board node count (`held != pts_saved`):
- **Attachment Overhead (`pts_saved > held`)**: Attaching this board requires neighboring boards to spend extra points on exit-gate spurs, so dropping or deferring the board while leveling frees more points than the board's own node count.
  - *Example on `tests/eternal_rogue_sample.json` (`#4 Cheap Shot`)*:
    > `[Board Cost] Attaching this 44-point board requires +6 extra gate-path points on neighboring boards (50 points total build cost) — if you drop or defer this board while leveling, removing it frees 50 Paragon points across the build (-140 DEX, -35 INT, -30 STR, -5 WILL).`
- **Transit Hub Discount (`pts_saved < held`)**: The board serves as a multi-exit transit hub between other boards; if dropped or replaced, the remaining boards must spend extra points to route around it, freeing fewer net points than the board holds.
  - *Example on `tests/eternal_rogue_sample.json` (`#2 Exploit Weakness`)*:
    > `[Board Cost] This 71-point board acts as a transit hub between neighboring boards — if you drop or replace it, the remaining boards must spend +6 extra points to route around it, so removing it only frees 65 net Paragon points (-135 DEX, -94 INT, -25 STR, -5 WILL).`

### Pass 5: Rare & Magic Cluster Marginal & Periphery Cost (`_hint_pass_cluster_marginal_cost`)
Analyzes connected Rare/Magic target clusters (`CLUSTER_NODE_TYPES`) for three distinct optimization opportunities:
1. **`[-N pts] Cluster Spur Cost`**: Full spur clusters outside the Glyph radius that require dedicated path nodes (`path_overhead >= 1` and `pts_saved >= 5`), plus the option to keep the Rare node and drop leaf Magic nodes via `peel_ablation`.
   - *Example (`#4 Cheap Shot`)*:
     > `[-8 pts] Rare 'Spearhead' cluster around (3, 5) (6 target nodes) requires 2 dedicated path nodes — disabling this cluster saves 8 Paragon points (-5 DEX, -5 STR); or keep Rare 'Spearhead' and drop 3 leaf Magic nodes ((4, 5), (2, 6), (2, 5)) to save 3 points.`
2. **`[-N pts] Transit Cluster Periphery`**: Semi-transit clusters outside the Glyph radius where the main highway only uses 1–3 nodes of the cluster while the remaining 3–5 nodes hang off the side (`pts_saved >= 3` and `pts_saved >= len(comp) / 2`), reporting both full-cluster savings and **keep-Rare periphery pruning**.
   - *Examples*:
     - `#1 Start`: `[-4 pts] Rare 'Resilience' cluster around (11, 8) (5 target nodes) only uses 1 node on the main path — disabling the cluster saves 4 Paragon points (no core stat change); or keep Rare 'Resilience' and drop 3 peripheral Magic nodes ((11, 7), (11, 9), (12, 8)) to save 3 points.`
     - `#2 Exploit Weakness`: `[-5 pts] Rare 'Unassailable' cluster around (6, 8) (6 target nodes) only uses 1 node on the main path — disabling the cluster saves 5 Paragon points (+5 DEX, -5 INT, +5 STR); or keep Rare 'Unassailable' and drop 2 peripheral Magic nodes ((5, 8), (4, 8)) to save 2 points.`
3. **`[-1 pt] Cluster Edge Detour`**: Single peripheral `MAGIC` nodes on the border of a cluster that force the connecting path to detour around higher-value stat nodes.
   - *Example (`#3 Leyrana's Instinct`)*:
     > `[-1 pt] Peripheral MAGIC node at (14, 18) forces a detour — unmarking it reroutes via MAGIC_STR (+7 STR) at (16, 18), saving 1 Paragon point (-5 INT, +7 STR).`

### Pass 6: Manual Normal Stat Targets Outside Radius (`_hint_pass_manual_path_targets`)
Checks normal `+5` stat nodes (`DEX`, `INT`, `STR`, `WILL`) outside the Glyph radius that were left marked as required targets from manual pathing in `d4-paragon.com` exports.

### Pass 7: Nearby & Reroute-Accessible Rare/Legendary Nodes (`_hint_pass_nearby_unselected_rares`)
Combines a multi-source BFS ($d \le 4$) from `board.selected_nodes` with exact fixed-exit Steiner insertion (`HintContext.insertion_cost`) to find unselected `RARE` or `LEGENDARY` nodes outside the Glyph radius that can be unlocked for **0–2 additional Paragon points**:
- **`[Nearby 1-Pt Rare/Legendary]` / `[Nearby 2-Pt Rare/Legendary]`**: Unselected Rare/Legendary nodes that lie 1 step (or 2 steps for `+10` Core-Stat Rares and Legendaries) directly off the current path.
  - *Example (`#5 Danse Macabre`)*:
    > `[Nearby 1-Pt Rare/Legendary] Unselected Rare 'Acrobat' at (7, 5) (with 3 adjacent Magic cluster nodes) is directly adjacent to your active path (can be unlocked for just 1 Paragon point).`
- **`[Reroute +N-Pt Rare/Legendary]`**: Unselected Rare/Legendary nodes that are 2–4 steps away from the current path via BFS, **but** can be acquired for only **0–2 additional Paragon points** by shifting the path to an alternate corridor!
  - *Example (`#5 Danse Macabre`)*:
    > `[Reroute +1-Pt Rare/Legendary] Unselected Rare 'Skulker' at (13, 5) (with 3 adjacent Magic cluster nodes) is 3 steps from your current path, but rerouting 7 path nodes unlocks it for only +1 additional Paragon point (-10 DEX, +5 INT, -5 STR, +10 WILL).`

### Pass 8: Cross-Board Glyph Socket Swaps (`_hint_pass_glyph_socket_swaps`)
Evaluates two cross-board Glyph socket optimizations across all boards in the build:
1. **`[0-Pt Glyph Socket Swap]` (Immediate 0-Point Scaling Gain)**: Evaluates every pair of socketed Glyphs on the current `selected_nodes` of both boards (using each Glyph's own radius). If swapping two Glyphs keeps both secondary thresholds met and strictly increases the primary scaling score (`DEX`, `INT`, `STR`, `WILL`, `normal`, `magic`, or `rare` node count) of at least one Glyph without decreasing the other, reports the immediate 0-point gain (noting when node-amplifying glyphs should be checked for specific boosted affixes).
2. **`[Glyph Socket Swap]` (Radius Stat Ceiling Swap)**: Compares the maximum available in-radius Core Stat ceilings (`HintContext.max_radius_stat`, evaluated at the incoming Glyph's radius) across all boards. Flags when a Secondary-Stat Glyph is socketed on a board that has a **higher Primary-Stat radius ceiling** than another board socketing a Primary-Stat Glyph (while that other board has $\ge$ the secondary-stat radius ceiling).
   - *Example on `tests/eternal_rogue_sample.json` (`#2 Exploit Weakness` $\leftrightarrow$ `#3 Leyrana's Instinct`)*:
     > `[Glyph Socket Swap] Board #2 'Exploit Weakness' has a higher in-radius DEX ceiling (110 DEX at R=5) than #3 'Leyrana's Instinct' (105 DEX), while #3 'Leyrana's Instinct' has the same 74 INT radius ceiling — consider moving 'Nightstalker' (INT) to #3 'Leyrana's Instinct' and socketing 'Headhunter' (DEX) here (+5 DEX radius ceiling before pathing costs).`

---

## 4. Extending `--hints` with New Passes

Adding a new optimization test requires only decorating a function with `@register_hint_pass(...)` in [`paragon/hints.py`](file:///workspace/diablo/paragon/hints.py):

```python
from .hints import Hint, HintContext, register_hint_pass

@register_hint_pass("My Custom Optimization Check")
def _hint_pass_custom(ctx: HintContext) -> list[Hint]:
    b = ctx.board
    hints = []
    # Query `ctx.marginal_ablation(...)`, `ctx.peel_ablation(...)`,
    # `ctx.adj_unselected_in_radius`, `b.node_stat_grants(coord)`,
    # `b.node_label(coord)`, `ctx.to_screen(coord)`, etc.
    return hints
```

Every registered pass automatically receives a per-board [`HintContext`](file:///workspace/diablo/paragon/hints.py#L64-L280) (with access to all boards' contexts via `ctx.shared_contexts`), participates in the `>2s` progress indicator, and has its returned [`Hint`](file:///workspace/diablo/paragon/hints.py#L43-L61) objects sorted by `points_delta` (highest point savings first) both under each board's `--visualize` grid and in the standalone `--hints` report.
