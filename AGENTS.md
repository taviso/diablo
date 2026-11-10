# AGENTS.md — Diablo 4 Paragon Board Optimization Project

## 1. Overview of Work Completed

In this project, we:
1. **Evaluated Optimality (Theoretical & Empirical**:
   - Proved that the `d4-paragon.com` algorithm is **suboptimal**, both mathematically (as a Steiner tree solver) and with respect to Diablo 4 game mechanics.
   - Demonstrated on the sample 5-board Rogue build ([`tests/eternal_rogue_sample.json`](file:///workspace/diablo/tests/eternal_rogue_sample.json), 146 target nodes) that `d4-paragon.com` selects **248 or 249 nodes**, whereas the true optimal solution requires **246 nodes** 
2. **Built an Optimal Command-Line Replacement (`paragon_optimize.py` + `libsteiner.cpp`)**:
   - Implemented an exact **Node-Weighted Steiner Tree** solver using optimality-preserving graph reduction rules and **Dreyfus–Wagner dynamic programming** ($O(3^k V + 2^k V \log V)$), accelerated in C++ (`libsteiner.cpp` / `libsteiner.so`) with a pure-Python fallback.
   - Added joint optimization of internal board paths, exit gate selection, board rotation, and non-overlapping 2D board layout, along with Diablo 4 Glyph radius mechanics, configurable stat prioritization (`--prioritize-stat`), and an extensible marginal cost & stat-swap hints engine (`--hints`).

---

## 2. The Diablo 4 Paragon System & Optimization Problem

In *Diablo 4*, characters unlock Paragon points and spend them on up to **5 Paragon Boards** (1 Starter Board plus up to 4 additional class boards):

- **Board Grid ($21 \times 21$)**:
  - Each board is a $21 \times 21$ orthogonal grid of cells (indexed `0..440` where `index = row * 21 + col`).
  - Cells are either empty (`DEAD`) or contain selectable nodes:
    - **Start Node**: Located at `(14, 10)` on the Starter Board (`id` ending in `_00`).
    - **Exit / Attachment Gates (`EXIT`)**: Up to 4 perimeter gates per board at `TOP (0, 10)`, `RIGHT (10, 20)`, `BOTTOM (20, 10)`, and `LEFT (10, 0)` (the Starter Board only exits via `TOP (0, 10)`).
    - **Legendary Node (`LEGENDARY`)**: 1 per non-starter board, granting a major build-defining multiplier.
    - **Glyph Socket (`GLYPH`)**: 1 per board, into which a player sockets a Glyph (`glyph`, `glyphLevel`).
    - **Rare (`RARE`) & Magic (`MAGIC`, `MAGIC_DEX`, `MAGIC_INT`, `MAGIC_STR`, `MAGIC_WILL`) Nodes**: Clustered high-value stat/affix nodes surrounding Rare nodes.
    - **Normal Stat Nodes (`DEX`, `INT`, `STR`, `WILL`)**: +5 attribute nodes that form the connecting corridors between clusters and populate the Glyph radius.
- **Glyph Mechanics & Radius**:
  - A socketed Glyph projects a diamond-shaped **Manhattan ($L_1$) radius** around the board's `GLYPH` socket (`|r - r_glyph| + |c - c_glyph| <= R`):
    - **Level 1–14**: Radius $R = 3$
    - **Level 15–45**: Radius $R = 4$
    - **Level 46+**: Radius $R = 5$
  - Purchasing nodes of the Glyph's governed attribute (`DEX`, `INT`, `STR`, or `WILL`) inside this radius unlocks the Glyph's secondary bonus requirement and scales its primary multiplicative/additive bonus.
- **Board Rotation & 2D Arrangement**:
  - Non-starter boards can be rotated clockwise in $90^\circ$ increments (`rotation` $\in \{0, 1, 2, 3\}$) and attached to adjacent boards on a 2D grid `(x, y)` whenever two boards' selected `EXIT` gates meet at their shared border.
- **The Optimization Objective**:
  - Given a set of desired target nodes on each board, find:
    1. The valid non-overlapping 2D arrangement and rotation of all boards,
    2. Which `EXIT` gates are activated on each board to link the boards into a connected tree rooted at the Starter Board, and
    3. The minimum-point connected subgraph on each board spanning all target nodes and active `EXIT` gates—breaking ties by maximizing the attribute value of the intermediate path nodes (including Glyph radius synergy).

---

## 3. Why `d4-paragon.com` Is Suboptimal

### A. Greedy Pairwise Heuristic Instead of Exact Steiner Trees
Connecting a set of terminal nodes on a grid graph with the minimum number of intermediate vertices is the **Node-Weighted Steiner Tree Problem**.
- `d4-paragon.com` uses a greedy Prim-like heuristic (Takahashi–Matsuyama shortest-path heuristic): it iteratively attaches the shortest path from the current tree to the nearest unconnected target node.
- This heuristic has a worst-case approximation ratio of $2 - \frac{2}{|T|}$ because it connects terminals **pairwise** and fails to recognize **3-way Steiner junctions** (single intermediate nodes that bridge 3+ disconnected target clusters simultaneously).

### B. Implementation Bugs we noticed
- **Wrong Class Secondary Stat Priorities**: `d4-paragon.com` appears to set `STR=0`, `MAGIC_STR=0` and `WILL=0.025`, `MAGIC_WILL=0.035` for Rogue class (despite Rogue boards having 20 `MAGIC_STR` nodes and 0 `MAGIC_WILL` nodes.
- Appears to copy-paste Barbarian/Druid onto Paladin/Warlock (setting Paladin's `INT` and Warlock's `STR` secondary stats to `0`).

---

## 4. Architecture of Our Optimal Solution

### Files Created
- [`paragon_optimize.py`](file:///workspace/diablo/paragon_optimize.py): CLI entrypoint handling argument parsing and JSON I/O.
- [`paragon/`](file:///workspace/diablo/paragon): Modular Python package:
  - [`paragon/game_data.py`](file:///workspace/diablo/paragon/game_data.py): Enums, grid constants, class stat priorities, and official `DiabloTools/d4data` board names, Glyph metadata (`GLYPH_DATA`), Rare/Legendary node metadata (`BOARD_SPECIAL_NODES`), and Core Stat grants.
  - [`paragon/board.py`](file:///workspace/diablo/paragon/board.py): `ParagonBoard` model, Maxroll JSON unwrapping (`unwrap_paragon_boards` supporting `.data.activeProfile` and `.paragon.position`, plus `unwrap_all_paragon_configs`), free start/entry gate accounting (`free_coords`, `purchased_nodes`), screen rotation transforms, Core Stat calculations, and Glyph/stat priority weighting.
  - [`paragon/solver.py`](file:///workspace/diablo/paragon/solver.py): Exact graph reduction rules, C++/Python Dreyfus–Wagner DP, Z3 MaxSMT, and SciPy/HiGHS MILP solvers, and global 2D board tree/rotation search.
  - [`paragon/visualize.py`](file:///workspace/diablo/paragon/visualize.py): Rotated in-game Unicode block and ASCII board/layout renderers and summary table.
  - [`paragon/hints.py`](file:///workspace/diablo/paragon/hints.py): Generic, extensible post-optimization marginal cost & stat-swap hints engine (`--hints`) and point budget enforcement (`--budget`, `enforce_budget`, `print_budget_report`).
  - [`paragon/paragon_meta_data.json`](file:///workspace/diablo/paragon/paragon_meta_data.json): Structural $21 \times 21$ board cell type metadata across all 80 Paragon boards.
- [`libsteiner.cpp`](file:///workspace/diablo/libsteiner.cpp) (`libsteiner.so`): Native C++ accelerator implementing single-pass multi-exit Dreyfus–Wagner dynamic programming (with a pure-Python fallback in `paragon/solver.py`).
- [`tests/`](file:///workspace/diablo/tests): Automated verification suite and sample user builds:
  - [`tests/test_optimizer.py`](file:///workspace/diablo/tests/test_optimizer.py): Automated `unittest` verification and regression suite (21 tests covering exact Steiner optimality, Z3 and MILP vs. Dreyfus–Wagner equivalence, priority normalization, all `tests/*.json` user builds, all 233 unique Paragon configurations across 75 Maxroll builds in `tests/builds/*.json`, 10 randomized multi-board builds with stat-prioritization monotonicity, `--relax-path-nodes`, `--glyph-strategy`, `--budget` enforcement, joint 2D layout + threshold repair, exact whole-board removal vs. full re-optimization, exact Glyph threshold repair, and `--hints` invariants) run by `make check` / `make test`.
  - [`tests/eternal_rogue_sample.json`](file:///workspace/diablo/tests/eternal_rogue_sample.json), [`tests/1_board_1_glyph.json`](file:///workspace/diablo/tests/1_board_1_glyph.json), [`tests/Shred_Endgame_Druid.json`](file:///workspace/diablo/tests/Shred_Endgame_Druid.json), and [`tests/builds/`](file:///workspace/diablo/tests/builds): Real-world user and Maxroll community Paragon build JSONs across all 8 classes.
- [`Makefile`](file:///workspace/diablo/Makefile): Build and verification rules (`all`, `check`, `clean`) for compiling `libsteiner.so` and running `tests/test_optimizer.py`.
- [`README.md`](file:///workspace/diablo/README.md): User documentation and CLI reference.
- [`HINTS.md`](file:///workspace/diablo/HINTS.md): Detailed reference for the `--hints` optimization passes, `d4data` mechanics, and hybrid marginal cost analysis.

### Algorithmic Pipeline
> **Note on Combinatorial State Explosion & Timeouts**: Because Steiner tree solving ($O(3^k V)$), threshold subset search, and 2D board tree/rotation enumeration are combinatorial problems, unguarded parameter combinations (such as high terminal counts $k$ or large subset branch-and-bound trees) can cause exponential state explosion and unexpectedly huge runtimes. Always enforce strict work/iteration bounds in search algorithms and always wrap shell commands/tasks in a reasonable timeout (e.g., `timeout 10s ...`).

1. **Exact Optimality-Preserving Graph Reductions (`solve_board_exact`)**:
   - Before running Dreyfus–Wagner on a board, non-terminal vertices (excluding protected exit coordinates) are iteratively reduced without changing the optimal Steiner tree:
     - **Dead-End Pruning**: Removes non-terminals touching $\le 1$ neighbor/component.
     - **Redundant Bridge Pruning**: Removes degree-2 non-terminals whose two endpoints already belong to or touch the same terminal component.
     - **Strict Neighborhood Dominance**: Removes non-terminal $v$ if another non-terminal $z$ has $w(z) \le w(v)$ and $z$'s component+vertex neighborhood is a superset of $v$'s.
     - **Single-Neighbor Component Contraction**: Contracts the unique non-terminal neighbor into any terminal component that has only 1 exit point.
2. **Single-Pass Multi-Exit Dreyfus–Wagner DP (`solve_board_all_exits`), Z3 MaxSMT (`_solve_board_z3`), & HiGHS MILP Fallback (`_solve_board_milp`)**:
   - **Primary Solver (`--solver dw` / `--solver auto` for $\le 15$ bits)**: Fixes one target component $C_{k-1}$ as the root and encodes the remaining $k-1$ target components in bits `0..k-2` and the 4 board exits (`TOP`, `RIGHT`, `BOTTOM`, `LEFT`) in bits `k-1..k+2`. Computes the exact minimum Node-Weighted Steiner Tree for **all** valid exit subsets in a single DP pass per board (~0.8s total across all 5 boards of `tests/eternal_rogue_sample.json`).
   - **Z3 MaxSMT Solver (`--solver z3`)**: Formulates the Node-Weighted Steiner Tree as a lexicographic MaxSMT problem (`:opt.priority lex`) solved via `/usr/bin/z3 -T:30 -in`, minimizing non-terminal cardinality (`:id card`) as the primary objective and maximizing scaled node priority (`:id prio`) as the secondary tie-breaker. Cross-checked against Dreyfus–Wagner across all 5 sample boards in `make test` (`tests/test_optimizer.py`).
   - **High-$k$ Fallback (`--solver milp` / `--solver auto` for $> 15$ bits)**: Formulates the Node-Weighted Steiner Tree as a polynomial-size ($O(k E)$) single-commodity directed network flow Mixed-Integer Linear Program solved via `scipy.optimize.milp` (HiGHS), falling back to Z3 (`_solve_board_z3`) if SciPy is not installed.
3. **Global 2D Board Tree, Rotation & Exit Search (`optimize_layout_and_paths`)**:
   - For each screen-space exit mask, selects the optimal board rotation $r \in \{0, 1, 2, 3\}$ mapping unrotated exits $e$ to screen exits $(e + r) \bmod 4$.
   - Exhaustively searches all valid non-overlapping 2D grid trees rooted at `(0, 0)` to find the globally minimum node count and best priority-weighted score.
4. **Tiered Stat Prioritization (`DEFAULT_CLASS_NODE_PRIORITY` & `--prioritize-stat`)**:
   - Node weights are defined as $w(v) = 1.0 - \frac{\text{priority}(v)}{100000.0}$ (`PRIORITY_SCALE = 100000.0`), guaranteeing that the sum of priority discounts across all $\le 441$ nodes on a board is strictly $< 1.0$, so **minimum total node count is always the primary objective**.
   - Corrected default class stat priorities across all 8 classes (`Barb`, `Druid`, `Necro`, `Rogue`, `Sorc`, `Spirit`, `Paladin`, `Warlock`) using the ground-truth `MAGIC_<STAT>` cluster distributions in `paragon_meta_data.json`.
   - Supports ordered stat priority lists (e.g., `--prioritize-stat dex,str,int`) using exponentially tiered weights (`1.2 * 3.5^rank`) so earlier stats in the list are strongly preferred over later stats when breaking ties among equal-length paths.
5. **Terminal Visualization (`--no-visualize`, `--no-summary`, `--ascii`, `--no-color`)**:
   - By default (when `-o / --output` is not specified), displays 24-bit ANSI color Unicode block visualizations (including a 2D board layout overview map and Glyph radius overlays) and a summary table; supports `--ascii` and `--no-color` for plain output, or `-o <file.json>` to write optimized JSON and suppress terminal visualization/summary.
6. **Actionable Build Optimization Hints (`--hints`) & Point Budget Enforcement (`--budget`)**:
   - Runs 8 modular post-optimization analysis passes registered via `@register_hint_pass` in [`paragon/hints.py`](file:///workspace/diablo/paragon/hints.py) (documented in [`HINTS.md`](file:///workspace/diablo/HINTS.md)): Exact Glyph Threshold Repairs, Breakpoints & Secondary Over-Investment; Validated Stat Target Efficiency, Shared Bridges & Swaps; High-Cost Single Target Spurs & Exact Whole-Board Removal; Unused Legendary Board Replacement; Rare/Magic Cluster Marginal & Periphery Cost; Manual Path Targets Outside Radius; Nearby & Reroute-Accessible Rare/Legendary Nodes; and Cross-Board Glyph Socket Swaps.
   - Enforces a configurable Paragon point budget (`--budget POINTS`, defaulting to `MAX_PARAGON_POINTS = 342`; `--budget 0` for unlimited) via `enforce_budget` and `print_budget_report`: if the initial optimal solution exceeds the budget, it relaxes non-Glyph plain `+5` stat corridors and iteratively trims excess in-radius `+5` Glyph stat targets down toward each Glyph's `25`/`40` bonus threshold while keeping all Legendary/Rare/Magic nodes and Glyph bonuses active (or reports `No Solution Possible Within Budget` with suggested cluster cuts if the minimum-threshold floor exceeds the budget).
   - Uses hybrid leaf-peeling + exact Dreyfus–Wagner re-solving (`HintContext.marginal_ablation` and `HintContext.insertion_cost`) to evaluate target removals and additions in ~0.45s across a 5-board build, rendering each board's hints directly beneath its $21 \times 21$ visualization by default.

---

## 5. Glyph & Board Metadata Scope and Design Rationale

### A. What `paragon_meta_data.json`, `game_data.py`, and Build JSONs Contain
- **`paragon_meta_data.json` Is Structural Only**: It maps each board ID (`Paragon_<Class>_<NN>`) and cell index (`"0".."440"`) to one of 14 generic cell types (`DEAD`, `DEX`, `INT`, `STR`, `WILL`, `MAGIC`, `MAGIC_DEX`, `MAGIC_INT`, `MAGIC_STR`, `MAGIC_WILL`, `RARE`, `LEGENDARY`, `GLYPH`, `EXIT`). It strips out individual node names and non-stat affix text.
- **Official `DiabloTools/d4data` Enrichment (`paragon/game_data.py`)**: To model exact Core Stat totals and Glyph requirements, [`paragon/game_data.py`](file:///workspace/diablo/paragon/game_data.py) supplements the structural grid with official game data extracted from `DiabloTools/d4data`:
  - Exact Core Stat grants (`+5` for normal `<STAT>`, `+7` for `MAGIC_<STAT>`, and `+10` for Rare nodes that grant a Core Stat),
  - Official board display names (`BOARD_NAMES`), Rare & Legendary node names (`BOARD_SPECIAL_NODES`), and
  - Exact Glyph names, governed stats, secondary bonus thresholds (`25` vs. `40` Core Stat or `5` Magic), and scaling modes (`GLYPH_DATA`).
- **Glyph Template Identifiers**: In `d4-paragon.com` build JSONs (such as [`tests/eternal_rogue_sample.json`](file:///workspace/diablo/tests/eternal_rogue_sample.json)), the `glyph` field stores internal template identifiers (e.g., `Rare_056_Dexterity_Main`, `Rare_083_Intelligence_Side`, `Rare_Dex_Generic`), which `GLYPH_DATA` maps to their in-game names (*Exploit*, *Combat*, *Canny*, *Versatility*, etc.). Notably, `d4-paragon.com`'s Python backend ignores `glyph` and `glyphLevel` completely during optimization and relies on the user's pre-selected `nodes` dictionary.

### B. Why Target Node Selection Cannot Be Fully Inferred from Board + Glyph Names Alone
1. **Archetype-Specific Rare/Magic Clusters**: Each Paragon board contains 6 distinct Rare/Magic clusters targeting mutually exclusive archetypes (e.g., Marksman vs. Cutthroat vs. Trap vs. Imbuement vs. Maximum Life vs. Single-Element Resistance). Because `paragon_meta_data.json` labels all non-attribute Magic/Rare nodes simply as `"MAGIC"` and `"RARE"`, even inside a Glyph radius (or for Glyphs that boost Magic/Rare nodes), forcing all `"MAGIC"`/`"RARE"` nodes to be mandatory would waste Paragon points on dead affixes that the player intentionally skipped.
2. **Threshold-Only vs. Max-Stacking Glyphs**: Players sometimes socket a secondary-stat Glyph solely to reach its 25-stat bonus threshold (5 nodes) to unlock its multiplicative secondary bonus, whereas on primary-stat Glyphs they may stack 15+ stat nodes while still skipping isolated perimeter nodes at radius $R=5$ that require 2–3 off-stat detour points.

### C. Hybrid Glyph Handling in `paragon_optimize.py`
To respect build-specific cluster choices while still leveraging Glyph mechanics:
1. **Mandatory Input Targets**: All Rare, Magic, `MAGIC_<STAT>`, and normal stat nodes marked in the input JSON's `nodes` dictionary are preserved as mandatory terminals.
2. **Automatic In-Radius Tie-Breaking (`node_priority`)**: `get_glyph_stat_types` maps each socketed Glyph's governed attribute to both its normal (`STAT`) and Magic (`MAGIC_<STAT>`) node types. Inside the Glyph's Manhattan radius ($R \in \{3, 4, 5\}$ from `glyphLevel`), `node_priority` boosts matching `STAT` / `MAGIC_<STAT>` nodes (`+0.35`) as well as in-radius `MAGIC` / `RARE` nodes (`+0.15`), capturing extra Glyph scaling whenever an equal-length connecting path passes through the radius.
3. **Flexible Per-Glyph Optimization Strategies (`--glyph-strategy`)**: Supports a global default strategy (`bonus`, `max`, `none`, `preserve`) and/or per-Glyph overrides by name (e.g. `--glyph-strategy=bonus,Headhunter=max` or `--glyph-strategy=Ranger=bonus,Headhunter=none,Nightstalker=max`):
   - `preserve` (default): Keeps all input targets mandatory and tie-breaks equal-length paths through in-radius Glyph nodes.
   - `bonus`: Jointly optimizes the 2D board layout, active exit gates, and provably minimum-point set of in-radius nodes (`+5` normal, `+7` Magic, or `+10` Rare) needed to reach the Glyph's threshold (`40` primary stat, `25` secondary stat, or `50` All Stats for *Tears of Blood*).
   - `max`: Promotes all matching `STAT` and `MAGIC_<STAT>` nodes inside the Glyph's Manhattan radius to mandatory targets (and ensures the bonus threshold is met on Lv.1 $R=3$ boards).
   - `none`: Disables in-radius priority tie-breaking and threshold repairs (and with `--relax-path-nodes`, relaxes in-radius plain `+5` stat nodes down to the minimum connecting path).
4. **Opt-In Path Relaxation (`--relax-path-nodes`)**: `d4-paragon.com` and Maxroll exports mark every purchased path node, so exported builds leave nothing to reroute (e.g. `tests/builds/Paladin_Arbiter_Endgame__Eternal_Non_Seasonal.json` has 319 targets out of 323 purchased points). Alone (or for `preserve`/`max` Glyphs), `--relax-path-nodes` demotes plain `+5` stat targets that don't feed the socketed Glyph to optional path nodes (`ParagonBoard.relaxed_nodes`), so no Glyph's in-radius stat can decrease (Paladin: 323 -> 312 points). For `bonus` and `none` Glyphs (`--relax-path-nodes --glyph-strategy=bonus,...`), in-radius plain `+5` stat nodes are also relaxed so the optimizer pares `bonus` Glyphs down to their minimum-point threshold selection and `none` Glyphs down to their minimum connecting path (Paladin with `bonus`: 323 -> 285 points; Druid with `bonus`: 225 -> 213 points while repairing all 6 Glyph thresholds). Rare node attribute requirements are not modeled, so the CLI reports the Core Stat change vs. without `--relax-path-nodes`.

| Flags Passed | Input Targets | In-Radius Glyph Nodes | Sample Build Results |
| :--- | :--- | :--- | :--- |
| *(none)* (`preserve`) | Keeps all input targets mandatory. | Tie-breaks equal-length paths through in-radius Glyph stat nodes. | `1_board_1_glyph`: **12 pts** (`20/40 WILL`) |
| `--glyph-strategy=bonus` | **Additive**: Keeps all input targets mandatory. | If a `bonus` Glyph is below `glyph_threshold`, adds the **minimum-point** in-radius nodes (`+5` normal, `+7` Magic, `+10` Rare) to reach the threshold. | `1_board_1_glyph`: **16 pts** (`40/40 WILL`)<br>`Shred_Endgame_Druid`: **230 pts** (repairs `Keeper` & `Bane`) |
| `--glyph-strategy=max` | Keeps all input targets mandatory. | Promotes **all** matching `STAT` and `MAGIC_<STAT>` nodes in radius to mandatory targets (and meets `glyph_threshold` on Lv.1 $R=3$ boards). | `1_board_1_glyph`: **28 pts** (`85/40 WILL`)<br>`Paragon_Warlock_00` Lv.1 ($R=3$): **`40/40 WILL`** |
| `--relax-path-nodes` | Relaxes plain `+5` stat nodes *outside* the Glyph radius (and non-governed plain `+5` nodes inside). | Preserves all in-radius `feeds_glyph` nodes as mandatory so no Glyph's in-radius stat can decrease. | `Paladin_Arbiter_Endgame`: `323` $\rightarrow$ **312 pts** |
| `--relax-path-nodes` `--glyph-strategy=bonus` | Relaxes **all** plain `+5` stat nodes (both outside and inside the Glyph radius). | Jointly selects the 2D layout, exits, and **minimum-point** in-radius nodes needed to meet each socketed Glyph's bonus threshold. | `Paladin_Arbiter_Endgame`: `323` $\rightarrow$ **285 pts**<br>`Shred_Endgame_Druid`: `225` $\rightarrow$ **213 pts**<br>`eternal_rogue_sample`: `241` $\rightarrow$ **194 pts** |
| `--relax-path-nodes` `--glyph-strategy=bonus,Headhunter=max` | Relaxes plain `+5` stat nodes outside radii and inside `bonus` radii. | Keeps all in-radius stat nodes for `Headhunter` (`105/40 DEX`) while paring all other Glyphs down to their minimum-point bonus thresholds. | `eternal_rogue_sample`: `241` $\rightarrow$ **206 pts** |
| `--relax-path-nodes` `--glyph-strategy=Ranger=bonus,Headhunter=none,Nightstalker=max` | Relaxes plain `+5` stat nodes outside radii and inside `bonus`/`none` radii. | Applies `bonus` to `Ranger`, `none` to `Headhunter`, `max` to `Nightstalker`, and `preserve` to remaining Glyphs (`Grenadier`). | `eternal_rogue_sample`: `241` $\rightarrow$ **214 pts** |
