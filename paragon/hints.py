"""
Generic, extensible Paragon build optimization hints engine (--hints).

Provides `Hint`, `HintContext`, and `@register_hint_pass` to analyze optimized
Paragon boards using hybrid marginal cost analysis (fast leaf-spur walks,
O(|S*|) subgraph peeling, and cached 0-exit-bit exact Steiner re-solving) and
official d4data node/glyph values.
"""

import sys
import time
from collections import Counter, deque
from dataclasses import dataclass, field
from functools import cache
from heapq import heappop, heappush

from .board import PLAIN_STAT_TYPES, ParagonBoard, grid_components, is_grid_connected, neighbors4
from .game_data import (
    EXIT_COORDS,
    MAGIC_STAT_GRANT,
    MAX_PARAGON_POINTS,
    NODE_TYPE,
    NORMAL_STAT_GRANT,
    PARAGON_EXIT,
    STARTER_START_COORD,
    load_meta_data,
)
from .solver import solve_board_exact, solve_glyph_threshold_repair, solve_layout_without

STAT_NODE_TYPES = {
    NODE_TYPE.DEX,
    NODE_TYPE.INT,
    NODE_TYPE.STR,
    NODE_TYPE.WILL,
    NODE_TYPE.MAGIC_DEX,
    NODE_TYPE.MAGIC_INT,
    NODE_TYPE.MAGIC_STR,
    NODE_TYPE.MAGIC_WILL,
}

CLUSTER_NODE_TYPES = {
    NODE_TYPE.RARE,
    NODE_TYPE.LEGENDARY,
    NODE_TYPE.MAGIC,
    NODE_TYPE.MAGIC_DEX,
    NODE_TYPE.MAGIC_INT,
    NODE_TYPE.MAGIC_STR,
    NODE_TYPE.MAGIC_WILL,
}


@dataclass
class Hint:
    """Structured optimization suggestion or warning produced by a hint pass."""

    board_index: int
    board: ParagonBoard
    category: str
    message: str
    points_delta: int = 0
    screen_coords: list[tuple[int, int]] = field(default_factory=list)
    board_name: str = field(init=False)
    board_id: str = field(init=False)

    def __post_init__(self):
        self.board_name = self.board.board_name
        self.board_id = self.board.board_type_id


class HintContext:
    """
    Shared per-board analysis context passed to every registered hint pass.
    Caches exact fixed-exit Steiner re-solves, screen-coordinate mappings,
    subgraph peeling, and 1-point adjacent unselected candidate nodes.
    """

    def __init__(
        self,
        board_index: int,
        board: ParagonBoard,
        all_boards: list[ParagonBoard],
        solver: str = "auto",
        shared_contexts: dict[int, "HintContext"] | None = None,
    ):
        self.board_index = board_index
        self.board = board
        self.all_boards = all_boards
        self.solver = solver
        self.shared_contexts = shared_contexts if shared_contexts is not None else {}
        self._solve_cache: dict[frozenset[tuple[int, int]], tuple[int, float, set[tuple[int, int]]] | None] = {}
        self._board_removal: tuple | None = None

        self.active_exit_coords = board.active_exit_coords()
        self.primary_stat = board.class_primary_stat()

        # Precompute 1-point unselected nodes adjacent to the current selected tree
        self.adj_unselected: list[tuple[int, int]] = []
        self.adj_unselected_in_radius: list[tuple[int, int]] = []
        for coord, nt in sorted(board.grid_type.items()):
            if nt == NODE_TYPE.DEAD or coord in board.selected_nodes or coord in EXIT_COORDS.values():
                continue
            if any(nb in board.selected_nodes for nb in neighbors4(coord)):
                self.adj_unselected.append(coord)
                if board.in_glyph_radius(coord):
                    self.adj_unselected_in_radius.append(coord)

        self.adj_unselected.sort(key=lambda c: -board.node_priority(c))
        self.adj_unselected_in_radius.sort(key=lambda c: -board.node_priority(c))

    @property
    def base_required(self) -> set[tuple[int, int]]:
        return self.board.required_terminals()

    def to_screen(self, coord: tuple[int, int]) -> tuple[int, int]:
        """Map an unrotated `(row, col)` board coordinate to rotated screen-space `(x, y)` = `(col, row)`."""
        sr, sc = self.board.unrotated_to_screen(coord[0], coord[1])
        return (sc, sr)

    def selected_neighbors(
        self, coord: tuple[int, int], nodes_set: set[tuple[int, int]] | None = None
    ) -> list[tuple[int, int]]:
        s = self.board.selected_nodes if nodes_set is None else nodes_set
        return [nb for nb in neighbors4(coord) if nb in s]

    def trace_leaf_spur(self, leaf: tuple[int, int]) -> tuple[list[tuple[int, int]], tuple[int, int] | None]:
        """
        If `leaf` has degree 1 in `board.selected_nodes`, walk along degree-2 non-required
        path nodes until reaching either a required node or a branching junction (degree >= 3).
        Returns `(spur_nodes_list, anchor_coord)`.
        """
        nbs = self.selected_neighbors(leaf)
        if len(nbs) != 1:
            return ([leaf], None)
        req = self.base_required
        spur = [leaf]
        prev = leaf
        curr = nbs[0]
        while curr not in req and len(self.selected_neighbors(curr)) == 2:
            spur.append(curr)
            nxt = [x for x in self.selected_neighbors(curr) if x != prev][0]
            prev, curr = curr, nxt
        return (spur, curr)

    def peel_ablation(
        self, removed_targets: set[tuple[int, int]]
    ) -> tuple[int, set[tuple[int, int]]] | None:
        """
        Fast O(|S*|) topological peeling on `board.selected_nodes`:
        Removes `removed_targets` and iteratively prunes any non-required path nodes
        whose degree drops to <= 1. Returns `(points_saved, removed_coords)` if the
        remaining selected subgraph stays connected and spans all remaining required nodes,
        or `None` if removing `removed_targets` disconnects `board.selected_nodes`.
        """
        req = self.base_required - removed_targets
        rem = set(self.board.selected_nodes) - removed_targets
        if not req.issubset(rem):
            return None
        if not rem:
            return (len(self.board.selected_nodes), set(self.board.selected_nodes))

        changed = True
        while changed:
            changed = False
            for u in list(rem):
                if u not in req and len(self.selected_neighbors(u, rem)) <= 1:
                    rem.remove(u)
                    changed = True

        if not is_grid_connected(rem):
            return None

        removed_all = self.board.selected_nodes - rem
        return (len(removed_all), removed_all)

    def solve_for_terminals(
        self, terminals: set[tuple[int, int]]
    ) -> tuple[int, float, set[tuple[int, int]]] | None:
        """Compute and cache the exact optimal Steiner tree on `board` for `terminals`."""
        key = frozenset(terminals)
        if key in self._solve_cache:
            return self._solve_cache[key]
        if not terminals:
            self._solve_cache[key] = (0, 0.0, set())
            return self._solve_cache[key]
        res = solve_board_exact(
            self.board,
            is_starter=True,
            max_exits=0,
            solver=self.solver,
            fixed_terminals=terminals,
        )
        sol = res.get(0)
        self._solve_cache[key] = sol
        return sol

    def solve_without(
        self, removed_targets: set[tuple[int, int]], extra_removed_required: set[tuple[int, int]] | None = None
    ) -> tuple[int, float, set[tuple[int, int]]] | None:
        """
        Compute the exact optimal Steiner tree on `board` when `removed_targets` (and optional
        `extra_removed_required` such as an exit gate) are unmarked.
        """
        all_removed = removed_targets if not extra_removed_required else (removed_targets | extra_removed_required)
        return self.solve_for_terminals(self.base_required - all_removed)

    def insertion_cost(
        self, added_targets: set[tuple[int, int]]
    ) -> tuple[int, set[tuple[int, int]], set[tuple[int, int]]] | None:
        """
        Compute the exact fixed-exit marginal point cost of adding `added_targets` to `base_required`.
        Returns `(additional_points, removed_coords, added_coords)` or `None` if unsolvable.
        """
        sol = self.solve_for_terminals(self.base_required | added_targets)
        if sol is None:
            return None
        new_sel = sol[2]
        pts_added = len(new_sel) - len(self.board.selected_nodes)
        return (pts_added, self.board.selected_nodes - new_sel, new_sel - self.board.selected_nodes)

    def marginal_ablation(
        self, removed_targets: set[tuple[int, int]], force_exact: bool = False
    ) -> tuple[int, set[tuple[int, int]], set[tuple[int, int]]]:
        """
        Return `(points_saved, removed_coords, added_coords)` when `removed_targets` are unmarked.
        Uses fast O(1) spur walk when a single leaf target's spur attaches directly to another
        required node, and falls back to exact 0-exit-bit Steiner re-solve otherwise.
        """
        if len(removed_targets) == 1 and not force_exact:
            u = next(iter(removed_targets))
            nbs = self.selected_neighbors(u)
            if len(nbs) == 1:
                spur, anchor = self.trace_leaf_spur(u)
                if anchor is not None and anchor in self.base_required:
                    return (len(spur), set(spur), set())

        sol = self.solve_without(removed_targets)
        if sol is None:
            return (0, set(), set())
        new_sel = sol[2]
        pts_saved = len(self.board.selected_nodes) - len(new_sel)
        return (pts_saved, self.board.selected_nodes - new_sel, new_sel - self.board.selected_nodes)

    def exact_threshold_repair(
        self, max_solves: int = 500
    ) -> tuple[set[tuple[int, int]], int] | None:
        """
        Find the minimum-point selection (with this board's active exits fixed) whose governed Core Stat
        inside the Glyph radius meets the Glyph's threshold.
        Returns `(selected_nodes, lower_bound_count)`, or `None` if the threshold is unreachable on this board
        (or no repair was found within `max_solves` exact re-solves). `selected_nodes` is a proven minimum when
        `len(selected_nodes) == lower_bound_count`; otherwise the search limit was hit and it is the best found.
        """
        return solve_glyph_threshold_repair(self.board, solver=self.solver, max_solves=max_solves)

    def pare_targets_to_threshold(
        self, drop_targets: set[tuple[int, int]], thresh: int | None = None
    ) -> set[tuple[int, int]] | None:
        """
        Unmark `drop_targets` on `self.board` and return the minimum-point selection that still meets
        `thresh` (default `self.board.glyph_threshold`), without mutating `self.board`.
        """
        b = self.board
        eff_thresh = b.glyph_threshold if thresh is None else thresh
        sol = self.solve_without(drop_targets)
        if sol is None:
            return None
        base_sel = sol[2]
        if b.glyph_stat_in_radius(base_sel) >= eff_thresh:
            return base_sel
        prev_targets, prev_sel, prev_thresh = b.target_nodes, b.selected_nodes, b.glyph_threshold
        try:
            b.target_nodes = prev_targets - drop_targets
            b.selected_nodes = base_sel
            b.glyph_threshold = eff_thresh
            rep = solve_glyph_threshold_repair(b, solver=self.solver)
            return rep[0] if rep is not None else None
        finally:
            b.target_nodes, b.selected_nodes, b.glyph_threshold = prev_targets, prev_sel, prev_thresh

    def board_removal(self) -> tuple[int, Counter] | None:
        """
        Exact savings from removing `self.board` from the build entirely: the remaining boards'
        layout, rotations, exits, and paths are fully re-solved (`solve_layout_without`).
        Returns `(points_saved, core_stat_deltas)` in purchased Paragon points across all boards,
        or `None` if infeasible.
        """
        if self._board_removal is None:
            res = solve_layout_without(self.all_boards, {id(self.board)}, solver=self.solver)
            if res is None:
                self._board_removal = (None,)
            else:
                _, selections = res
                old_total = 0
                new_total = 0
                deltas: Counter = Counter()
                for ob in self.all_boards:
                    old_p = ob.purchased_nodes(ob.selected_nodes)
                    new_p = ob.purchased_nodes(selections.get(id(ob), set()))
                    old_total += len(old_p)
                    new_total += len(new_p)
                    for k, v in self.net_stat_changes_on(ob, old_p - new_p, new_p - old_p).items():
                        deltas[k] += v
                self._board_removal = ((old_total - new_total, deltas),)
        return self._board_removal[0]

    def is_sole_target_leaf_board(self) -> bool:
        """True if this non-starter board is a leaf in the layout (1 active exit) with a single non-socket target."""
        b = self.board
        return (
            not b.is_starter_board()
            and len(self.active_exit_coords) == 1
            and len(b.target_nodes - {b.glyph_coord} - b.auto_glyph_bonus_nodes) == 1
        )

    def max_radius_stat(self, stat_name: str, radius: int | None = None) -> int:
        """Compute the maximum possible points of `stat_name` available inside this board's Glyph radius."""
        if self.board.glyph_coord is None:
            return 0
        rad = self.board.glyph_radius if radius is None else radius
        if rad <= 0:
            rad = 5
        gr, gc = self.board.glyph_coord
        total = 0
        for (r, c), nt in self.board.grid_type.items():
            if nt == NODE_TYPE.DEAD or (r, c) in EXIT_COORDS.values():
                continue
            if abs(r - gr) + abs(c - gc) <= rad:
                total += self.board.node_stat_grant_for((r, c), stat_name)
        return total

    def net_stat_changes(
        self, removed_coords: set[tuple[int, int]], added_coords: set[tuple[int, int]]
    ) -> dict[str, int]:
        """Compute net Core Stat change (`added - removed`) across `DEX`, `INT`, `STR`, `WILL`."""
        return self.net_stat_changes_on(self.board, removed_coords, added_coords)

    @staticmethod
    def net_stat_changes_on(
        board: ParagonBoard, removed_coords: set[tuple[int, int]], added_coords: set[tuple[int, int]]
    ) -> dict[str, int]:
        """Compute net Core Stat change (`added - removed`) on `board` across `DEX`, `INT`, `STR`, `WILL`."""
        deltas = Counter()
        for c in added_coords:
            for k, v in board.node_stat_grants(c).items():
                deltas[k] += v
        for c in removed_coords:
            for k, v in board.node_stat_grants(c).items():
                deltas[k] -= v
        return {k: deltas[k] for k in ("DEX", "INT", "STR", "WILL") if deltas[k] != 0}

    def format_stat_deltas(
        self, removed_coords: set[tuple[int, int]], added_coords: set[tuple[int, int]]
    ) -> str:
        deltas = self.net_stat_changes(removed_coords, added_coords)
        if not deltas:
            return "no core stat change"
        return ", ".join(f"{v:+d} {k}" for k, v in deltas.items())


HINT_PASSES: list[tuple[str, callable]] = []


def register_hint_pass(name: str):
    """Decorator to register a generic post-optimization hint pass."""
    def decorator(fn):
        HINT_PASSES.append((name, fn))
        return fn
    return decorator


@register_hint_pass("Glyph Threshold, Synergy & Over-Investment")
def _hint_pass_glyph_synergy(ctx: HintContext) -> list[Hint]:
    """
    Check socketed Glyph bonus threshold requirements (with connected 1-3 pt repair packages),
    radius breakpoint previews (R=3 -> R=4 -> R=5), secondary-stat over-investment,
    and 1-point adjacent in-radius upgrades.
    """
    b = ctx.board
    if not b.glyph or b.glyph_coord is None:
        return []

    hints: list[Hint] = []
    cur_stat = b.glyph_stat_in_radius()
    req_stat = b.glyph_stat
    thresh = b.glyph_threshold

    if req_stat and thresh > 0 and cur_stat < thresh and b.glyph_strategy != "none":
        deficit = thresh - cur_stat
        repair = ctx.exact_threshold_repair()
        if repair is None:
            extra = (
                f" Even buying every {req_stat} node in radius cannot reach {thresh} {req_stat} on this board "
                f"at Lv.{b.glyph_level} (R={b.glyph_radius})."
            )
            sc_list = [ctx.to_screen(b.glyph_coord)]
        else:
            new_sel, lb_count = repair
            added = new_sel - b.selected_nodes
            removed = b.selected_nodes - new_sel
            k_pts = len(new_sel) - len(b.selected_nodes)
            new_stat = b.glyph_stat_in_radius(new_sel)
            add_str = ", ".join(f"{b.node_label(c)} at {ctx.to_screen(c)}" for c in sorted(added))
            reroute_str = f", dropping {len(removed)} path node{'s' if len(removed) != 1 else ''}" if removed else ""
            proven = lb_count >= len(new_sel)
            qualifier = "Minimum repair" if proven else "Best repair found"
            bound_note = "" if proven else f" (search limit reached; the minimum is at least +{lb_count - len(b.selected_nodes)})"
            extra = (
                f" {qualifier}: +{k_pts} Paragon point{'s' if k_pts != 1 else ''}{bound_note} — add {add_str}{reroute_str} "
                f"to reach {new_stat}/{thresh} {req_stat} ({ctx.format_stat_deltas(removed, added)}) "
                f"(or use --glyph-strategy=bonus)."
            )
            sc_list = [ctx.to_screen(c) for c in sorted(added)]

        hints.append(
            Hint(
                ctx.board_index,
                b,
                "Glyph Threshold Unmet",
                f"Glyph '{b.glyph_name}' only has {cur_stat}/{thresh} {req_stat} in radius "
                f"(needs +{deficit} {req_stat} to unlock its secondary & legendary bonus).{extra}",
                points_delta=0,
                screen_coords=sc_list,
            )
        )

    # Preview radius breakpoints (Lv.15 -> R=4, Lv.46 -> R=5) on the existing selected path when glyphLevel < 46
    # For magic/rare/normal-mode Glyphs whose governed stat threshold is already met, extra governed stat
    # does not scale the primary bonus unless the threshold was previously unmet.
    if req_stat and b.glyph_level < 46 and (b.glyph_mode in ("stat", "all") or cur_stat < thresh):
        bp_notes = []
        for bp_lvl, bp_rad in ((15, 4), (46, 5)):
            if b.glyph_radius < bp_rad:
                bp_stat = b.glyph_stat_in_radius(b.selected_nodes, radius=bp_rad)
                if bp_stat > cur_stat:
                    thresh_unlock = (
                        " (unlocks threshold!)"
                        if thresh > 0 and cur_stat < thresh <= bp_stat
                        else ""
                    )
                    bp_notes.append(
                        f"Lv.{bp_lvl} (R={bp_rad}) reaches {bp_stat}/{thresh} {req_stat} "
                        f"(+{bp_stat - cur_stat} {req_stat}{thresh_unlock})"
                    )
        if bp_notes:
            hints.append(
                Hint(
                    ctx.board_index,
                    b,
                    "Glyph Radius Breakpoint",
                    f"Glyph '{b.glyph_name}' is currently Lv.{b.glyph_level} (R={b.glyph_radius}, {cur_stat}/{thresh} {req_stat}) — "
                    f"on your existing path with 0 extra Paragon points, leveling to {'; '.join(bp_notes)}.",
                    points_delta=0,
                    screen_coords=[ctx.to_screen(b.glyph_coord)],
                )
            )

    # Check if a Secondary-Stat Glyph is heavily over-invested while Primary Stat nodes in radius are skipped,
    # or if a magic/rare-mode Glyph (which gets 0 primary scaling from governed stat above `thresh`) is over-invested.
    plain_stat_types = {NODE_TYPE.DEX, NODE_TYPE.INT, NODE_TYPE.STR, NODE_TYPE.WILL}
    if req_stat and req_stat not in (ctx.primary_stat, "ALL") and thresh > 0 and cur_stat >= thresh + 15:
        sec_only_targets = {
            u for u in b.target_nodes
            if b.in_glyph_radius(u) and b.grid_type[u] in b.glyph_stat_types
        }
        all_rad_stat_targets = {
            u for u in b.target_nodes
            if b.in_glyph_radius(u) and b.grid_type[u] in STAT_NODE_TYPES
        }
        unselected_prim_in_rad = [
            c for (c, nt) in b.grid_type.items()
            if c not in b.selected_nodes and b.in_glyph_radius(c) and nt == NODE_TYPE[ctx.primary_stat]
        ]
        if len(all_rad_stat_targets) >= 3 and len(unselected_prim_in_rad) >= 3:
            # Prefer unmarking only secondary-stat targets if that alone saves >=3 pts and preserves threshold;
            # otherwise test unmarking all in-radius stat targets, or paring plain stat targets down to `thresh`.
            pts_saved, removed, added = 0, set(), set()
            scope_desc = f"all {len(all_rad_stat_targets)} in-radius stat targets"
            if len(sec_only_targets) >= 3:
                s_pts, s_rem, s_add = ctx.marginal_ablation(sec_only_targets, force_exact=True)
                s_base = b.glyph_stat_in_radius((b.selected_nodes - s_rem) | s_add)
                if s_pts >= 3 and s_base >= thresh:
                    pts_saved, removed, added = s_pts, s_rem, s_add
                    scope_desc = f"{len(sec_only_targets)} in-radius {req_stat} targets"
            if pts_saved < 3:
                pts_saved, removed, added = ctx.marginal_ablation(all_rad_stat_targets, force_exact=True)
            base_g_stat = b.glyph_stat_in_radius((b.selected_nodes - removed) | added)
            if pts_saved < 3 or base_g_stat < thresh:
                plain_sec = {u for u in sec_only_targets if b.grid_type[u] in PLAIN_STAT_TYPES}
                if len(plain_sec) >= 3:
                    new_sel = ctx.pare_targets_to_threshold(plain_sec)
                    if new_sel is not None and len(b.selected_nodes) - len(new_sel) >= 3:
                        pts_saved = len(b.selected_nodes) - len(new_sel)
                        removed = b.selected_nodes - new_sel
                        added = new_sel - b.selected_nodes
                        base_g_stat = b.glyph_stat_in_radius(new_sel)
                        unmarked_cnt = len(plain_sec - new_sel)
                        scope_desc = f"{unmarked_cnt} excess in-radius {req_stat} targets"
            if pts_saved >= 3 and base_g_stat >= thresh:
                delta_str = ctx.format_stat_deltas(removed, added)
                scaling_loss = cur_stat - base_g_stat
                scaling_note = (
                    f", trading -{scaling_loss} {req_stat} of '{b.glyph_name}' primary bonus scaling ({cur_stat} -> {base_g_stat} {req_stat})"
                    if b.glyph_mode == "stat" and scaling_loss > 0
                    else ""
                )
                hints.append(
                    Hint(
                        ctx.board_index,
                        b,
                        "Secondary Glyph Over-Invested",
                        f"Secondary-stat Glyph '{b.glyph_name}' has {cur_stat}/{thresh} {req_stat} in radius "
                        f"(+{cur_stat - thresh} {req_stat} above its {thresh} threshold, while {len(unselected_prim_in_rad)} "
                        f"in-radius {ctx.primary_stat} nodes are unselected) — if socketed only for its {thresh} {req_stat} "
                        f"secondary threshold, unmarking {scope_desc} leaves the cluster transit path at "
                        f"{base_g_stat}/{thresh} {req_stat} and saves {pts_saved} Paragon points ({delta_str}{scaling_note}).",
                        points_delta=pts_saved,
                        screen_coords=[ctx.to_screen(b.glyph_coord)],
                    )
                )
    elif (
        req_stat == ctx.primary_stat
        and b.glyph_mode in ("magic", "rare")
        and thresh > 0
        and cur_stat >= thresh + 15
    ):
        plain_gov_targets = {
            u for u in b.target_nodes
            if b.in_glyph_radius(u) and b.grid_type[u] == NODE_TYPE[req_stat] and u not in b.auto_glyph_bonus_nodes
        }
        if len(plain_gov_targets) >= 3:
            new_sel = ctx.pare_targets_to_threshold(plain_gov_targets)
            if new_sel is not None and len(b.selected_nodes) - len(new_sel) >= 3:
                pts_saved = len(b.selected_nodes) - len(new_sel)
                removed = b.selected_nodes - new_sel
                added = new_sel - b.selected_nodes
                new_g_stat = b.glyph_stat_in_radius(new_sel)
                unmarked = sorted(ctx.to_screen(u) for u in (plain_gov_targets - new_sel))
                coords_str = ", ".join(str(sc) for sc in unmarked[:4])
                if len(unmarked) > 4:
                    coords_str += f" (+{len(unmarked) - 4} more)"
                mode_label = "Magic" if b.glyph_mode == "magic" else "Rare"
                hints.append(
                    Hint(
                        ctx.board_index,
                        b,
                        "Glyph Stat Over-Invested",
                        f"Glyph '{b.glyph_name}' scales with in-radius {mode_label} nodes rather than {req_stat} "
                        f"above its {thresh} {req_stat} threshold (currently {cur_stat}/{thresh} {req_stat}) — "
                        f"unmarking {len(unmarked)} excess in-radius {req_stat} target{'s' if len(unmarked) != 1 else ''} "
                        f"({coords_str}) keeps '{b.glyph_name}' at {new_g_stat}/{thresh} {req_stat} and saves "
                        f"{pts_saved} Paragon points ({ctx.format_stat_deltas(removed, added)}).",
                        points_delta=pts_saved,
                        screen_coords=[ctx.to_screen(b.glyph_coord)],
                    )
                )

    # Check 1-point adjacent unselected in-radius nodes that synergize with the Glyph or grant +7 Magic stats
    gov_adj = [
        c for c in ctx.adj_unselected_in_radius
        if (
            (b.glyph_mode in ("stat", "all") or cur_stat < thresh)
            and (b.grid_type[c] in b.glyph_stat_types or (req_stat and b.node_stat_grant_for(c, req_stat) > 0))
        )
        or (b.glyph_mode == "normal" and b.grid_type[c] in plain_stat_types and b.priorities.get(b.grid_type[c], 0.0) > 0)
        or (b.glyph_mode == "magic" and b.grid_type[c].name.startswith("MAGIC"))
        or (b.glyph_mode == "rare" and b.grid_type[c] == NODE_TYPE.RARE)
    ]
    magic_sec_adj = [
        c for c in ctx.adj_unselected_in_radius
        if c not in gov_adj and b.grid_type[c].name.startswith("MAGIC_") and b.node_priority(c) > 0
    ]

    if gov_adj:
        items = ", ".join(f"{b.node_label(c)} at {ctx.to_screen(c)}" for c in gov_adj[:4])
        hints.append(
            Hint(
                ctx.board_index,
                b,
                "In-Radius 1-Pt Upgrade",
                f"Unselected in-radius {items} {'is' if len(gov_adj) == 1 else 'are'} directly adjacent "
                f"to your path (costing only 1 Paragon point each to boost '{b.glyph_name}').",
                points_delta=0,
                screen_coords=[ctx.to_screen(c) for c in gov_adj[:4]],
            )
        )
    if magic_sec_adj:
        items = ", ".join(f"{b.node_label(c)} at {ctx.to_screen(c)}" for c in magic_sec_adj[:4])
        hints.append(
            Hint(
                ctx.board_index,
                b,
                "In-Radius 1-Pt Magic Stat",
                f"Unselected in-radius Magic stat node{'s' if len(magic_sec_adj) > 1 else ''} {items} "
                f"{'is' if len(magic_sec_adj) == 1 else 'are'} directly adjacent to your path "
                f"(+{MAGIC_STAT_GRANT} secondary stat for 1 Paragon point vs +{NORMAL_STAT_GRANT} on normal nodes).",
                points_delta=0,
                screen_coords=[ctx.to_screen(c) for c in magic_sec_adj[:4]],
            )
        )
    return hints


@register_hint_pass("Stat Target Efficiency, Shared Bridges & Swaps")
def _hint_pass_stat_target_swaps(ctx: HintContext) -> list[Hint]:
    """
    Detect individual stat target nodes (<STAT> +5 or MAGIC_<STAT> +7) that require >=2 points
    (including off-stat bridge nodes and 2-node shared-bridge pairs) or whose removal unlocks
    a validated 1-point adjacent swap on the ablated tree for fewer points.
    """
    b = ctx.board
    hints: list[Hint] = []
    old_g_stat = b.glyph_stat_in_radius(b.selected_nodes) if (b.glyph and b.glyph_threshold > 0) else 0
    reported_targets: set[tuple[int, int]] = set()

    for u in sorted(b.target_nodes):
        if u in b.auto_glyph_bonus_nodes:
            continue
        nt = b.grid_type[u]
        if nt not in STAT_NODE_TYPES:
            continue
        in_rad = b.in_glyph_radius(u)
        nbs = ctx.selected_neighbors(u)
        target_nbs = [nb for nb in neighbors4(u) if nb in b.target_nodes]
        # Inside the glyph radius, only test leaf targets (degree 1 in S*) or isolated targets
        # so we don't fragment clusters or trigger slow multi-component solves
        if in_rad and len(nbs) > 1 and len(target_nbs) > 0:
            continue

        pts_saved, removed, added = ctx.marginal_ablation({u}, force_exact=not in_rad)
        new_selected = (b.selected_nodes - removed) | added
        sc = ctx.to_screen(u)
        u_grants = b.node_stat_grants(u)
        u_stat_name = next(iter(u_grants.keys()))
        u_stat_val = u_grants[u_stat_name]

        # Case A: Disabling `u` saves >= 2 points (target + bridge node(s))
        if pts_saved >= 2:
            reported_targets.add(u)
            bridges = sorted(removed - {u})
            bridge_desc = ", ".join(f"{b.node_label(br)} at {ctx.to_screen(br)}" for br in bridges[:3])
            delta_str = ctx.format_stat_deltas(removed, added)

            # Check if glyph threshold or in-radius governed stat changes on `new_selected`
            thresh_note = ""
            if b.glyph and b.glyph_stat and b.glyph_threshold > 0:
                new_g_stat = b.glyph_stat_in_radius(new_selected)
                if new_g_stat != old_g_stat or (in_rad and b.glyph_stat == u_stat_name):
                    if new_g_stat >= b.glyph_threshold:
                        thresh_note = f" ('{b.glyph_name}' stays at {new_g_stat}/{b.glyph_threshold} {b.glyph_stat})"
                    else:
                        thresh_note = f" (warning: drops '{b.glyph_name}' to {new_g_stat}/{b.glyph_threshold} {b.glyph_stat})"

            # Check if a 2nd stat target shares `u`'s bridge node so removing both saves >= 3 points
            pair_note = ""
            for br in bridges:
                for br_nb in ctx.selected_neighbors(br):
                    if br_nb != u and br_nb in b.target_nodes and b.grid_type[br_nb] in STAT_NODE_TYPES:
                        peel_res = ctx.peel_ablation({u, br_nb})
                        if peel_res is not None and peel_res[0] >= pts_saved + 1:
                            p_saved, p_rem = peel_res
                            p_thresh_str = ""
                            if b.glyph and b.glyph_stat and b.glyph_threshold > 0:
                                p_g_stat = b.glyph_stat_in_radius(b.selected_nodes - p_rem)
                                if p_g_stat != old_g_stat:
                                    p_thresh_str = f", '{b.glyph_name}' {p_g_stat}/{b.glyph_threshold} {b.glyph_stat}"
                            pair_note = (
                                f"; or disable together with {b.node_label(br_nb)} at {ctx.to_screen(br_nb)} "
                                f"to save {p_saved} points ({ctx.format_stat_deltas(p_rem, set())}{p_thresh_str})"
                            )
                            reported_targets.add(br_nb)
                            break
                if pair_note:
                    break

            # Validate 1-pt swap candidates directly against the ablated tree `new_selected`
            valid_swaps = []
            for (r, c), cand_nt in b.grid_type.items():
                if (
                    cand_nt not in STAT_NODE_TYPES
                    or (r, c) in new_selected
                    or (r, c) in removed
                    or (r, c) in EXIT_COORDS.values()
                    or b.node_priority((r, c)) <= 0
                ):
                    continue
                if any(nb in new_selected for nb in neighbors4((r, c))):
                    valid_swaps.append((r, c))
            valid_swaps.sort(
                key=lambda c: (
                    b.in_glyph_radius(c) and b.grid_type[c] in b.glyph_stat_types,
                    b.node_priority(c),
                    next(iter(b.node_stat_grants(c).values()), 0),
                    -(abs(c[0] - u[0]) + abs(c[1] - u[1])),
                ),
                reverse=True,
            )

            swap_note = ""
            u_prio = b.node_priority(u)
            for cand in valid_swaps:
                if (
                    b.glyph
                    and b.glyph_stat
                    and b.glyph_threshold > 0
                    and old_g_stat >= b.glyph_threshold
                    and b.glyph_stat_in_radius(new_selected | {cand}) < b.glyph_threshold
                ):
                    continue
                c_grants = b.node_stat_grants(cand)
                c_stat_name = next(iter(c_grants.keys()))
                c_stat_val = c_grants[c_stat_name]
                if (
                    b.node_priority(cand) >= u_prio
                    or (c_stat_name == u_stat_name and c_stat_val >= u_stat_val)
                    or (b.in_glyph_radius(cand) and b.grid_type[cand] in b.glyph_stat_types)
                ):
                    comb_delta = ctx.format_stat_deltas(removed, added | {cand})
                    swap_note = (
                        f"; or swap for 1-pt adjacent {b.node_label(cand)} at {ctx.to_screen(cand)} "
                        f"to save {pts_saved - 1} net point{'s' if pts_saved - 1 > 1 else ''} ({comb_delta})"
                    )
                    break

            hints.append(
                Hint(
                    ctx.board_index,
                    b,
                    "Inefficient Stat Target",
                    f"Target {b.node_label(u)} at {sc} costs {pts_saved} Paragon points "
                    f"(requires bridge {bridge_desc}) — disabling it saves {pts_saved} points "
                    f"({delta_str}){thresh_note}{swap_note}{pair_note}.",
                    points_delta=pts_saved,
                    screen_coords=[sc],
                )
            )
        elif pts_saved == 1 and added:
            # Disabling `u` rerouted the path and saved 1 point (report if it doesn't add 0-priority off-stats)
            if all(b.priorities.get(b.grid_type[a], 0.0) > 0 for a in added):
                reported_targets.add(u)
                delta_str = ctx.format_stat_deltas(removed, added)
                added_labels = ", ".join(f"{b.node_label(a)} at {ctx.to_screen(a)}" for a in sorted(added)[:2])
                hints.append(
                    Hint(
                        ctx.board_index,
                        b,
                        "Path Reroute Savings",
                        f"Target {b.node_label(u)} at {sc} forces a detour — disabling it reroutes via "
                        f"{added_labels}, saving 1 Paragon point ({delta_str}).",
                        points_delta=1,
                        screen_coords=[sc],
                    )
                )

    # Detect 2-3 node connected stat target pockets behind a shared bridge (where removing 1 node alone
    # saves <= 1 pt because its neighbor keeps the bridge alive, but removing the pocket collapses the bridge).
    pocket_cands = {
        u for u in b.target_nodes
        if u not in b.auto_glyph_bonus_nodes
        and u not in reported_targets
        and b.grid_type[u] in STAT_NODE_TYPES
    }
    for comp in grid_components(pocket_cands):
        if not (2 <= len(comp) <= 3):
            continue
        peel_res = ctx.peel_ablation(comp)
        if peel_res is None or peel_res[0] <= len(comp):
            continue
        p_saved, p_rem = peel_res
        if b.glyph and b.glyph_stat and b.glyph_threshold > 0 and old_g_stat >= b.glyph_threshold:
            if b.glyph_stat_in_radius(b.selected_nodes - p_rem) < b.glyph_threshold:
                continue
        bridges = sorted(p_rem - comp)
        bridge_desc = ", ".join(f"{b.node_label(br)} at {ctx.to_screen(br)}" for br in bridges[:3])
        comp_sorted = sorted(comp)
        comp_desc = ", ".join(f"{b.node_label(c)} at {ctx.to_screen(c)}" for c in comp_sorted)
        thresh_note = ""
        if b.glyph and b.glyph_stat and b.glyph_threshold > 0:
            new_g_stat = b.glyph_stat_in_radius(b.selected_nodes - p_rem)
            if new_g_stat != old_g_stat:
                thresh_note = f" ('{b.glyph_name}' stays at {new_g_stat}/{b.glyph_threshold} {b.glyph_stat})"
        hints.append(
            Hint(
                ctx.board_index,
                b,
                "Inefficient Stat Target",
                f"Stat target pocket ({comp_desc}) costs {p_saved} Paragon points "
                f"(shares bridge {bridge_desc}) — disabling all {len(comp)} targets saves {p_saved} points "
                f"({ctx.format_stat_deltas(p_rem, set())}){thresh_note}.",
                points_delta=p_saved,
                screen_coords=[ctx.to_screen(c) for c in comp_sorted],
            )
        )
    return hints


@register_hint_pass("High-Cost Single Target Nodes & Parent Gate Spurs")
def _hint_pass_high_cost_single_targets(ctx: HintContext) -> list[Hint]:
    """
    Detect Rare or Magic single target nodes that require long dedicated path spurs,
    including cascading parent-board exit gate spurs when a leaf board has a single target.
    """
    b = ctx.board
    hints: list[Hint] = []

    for u in sorted(b.target_nodes):
        if u == b.glyph_coord or u in b.auto_glyph_bonus_nodes or (b.is_starter_board() and u == STARTER_START_COORD):
            continue
        nt = b.grid_type[u]
        if nt not in (NODE_TYPE.RARE, NODE_TYPE.MAGIC):
            continue
        # Only test isolated/leaf targets (not interior nodes of a multi-node Rare/Magic cluster)
        target_nbs = [nb for nb in neighbors4(u) if nb in b.target_nodes]
        if len(target_nbs) > 0 or len(ctx.selected_neighbors(u)) != 1:
            continue

        pts_saved, removed, added = ctx.marginal_ablation({u})
        if pts_saved < 3:
            continue

        sc = ctx.to_screen(u)
        path_nodes = pts_saved - 1
        reroute_note = f"; reroutes {len(added)} nodes" if added else ""
        delta_str = ctx.format_stat_deltas(removed, added)

        # If this is the ONLY target on a leaf board (1 active exit), report the exact savings of
        # removing the whole board with the remaining layout fully re-solved
        sole_board_note = ""
        effective_saved = pts_saved
        removal = ctx.board_removal() if ctx.is_sole_target_leaf_board() else None
        if removal is not None:
            effective_saved, full_deltas = removal
            full_delta_str = ", ".join(f"{v:+d} {k}" for k, v in full_deltas.items() if v != 0) or "no core stat change"
            sole_board_note = (
                f" (sole target on this leaf board: removing the whole board saves exactly "
                f"{effective_saved} Paragon points with the layout re-solved [{full_delta_str}])"
            )

        hints.append(
            Hint(
                ctx.board_index,
                b,
                "High-Cost Target Spur",
                f"Enabling {b.node_label(u)} at {sc} costs {pts_saved} Paragon points "
                f"(1 target + {path_nodes} path nodes{reroute_note}, {delta_str}){sole_board_note} — "
                f"consider disabling if not build-critical.",
                points_delta=effective_saved,
                screen_coords=[sc],
            )
        )
    return hints


@cache
def _cached_meta_data() -> dict:
    return load_meta_data()


def _eval_board_glyph_cost(
    bid: str,
    b_ref: ParagonBoard,
    s_exits: list[PARAGON_EXIT],
    meta_data: dict,
    fixed_rot: int | None = None,
) -> tuple[tuple[int, int, int, int, int], str, str, int, int, int, int, int] | None:
    """
    Evaluate the minimum Paragon points on board `bid` to connect screen exits `s_exits` + its Glyph socket
    (`sock_pts`) and to reach `b_ref`'s Glyph bonus threshold (`thresh_pts`), plus its maximum in-radius
    stat capacity (`max_stat`). Uses fast Dijkstra + in-radius greedy BFS so each board takes < 5ms.
    """
    cand = ParagonBoard(
        {
            "id": bid,
            "nodes": {},
            "rotation": 0,
            "position": dict(b_ref.position),
            "glyph": b_ref.glyph,
            "glyphLevel": b_ref.glyph_level,
        },
        meta_data,
    )
    if cand.glyph_coord is None:
        return None
    all_exits = set(EXIT_COORDS.values())

    def dijkstra(src: tuple[int, int], allow_exits: set[tuple[int, int]]):
        dist = {src: 0.0}
        prev: dict[tuple[int, int], tuple[int, int] | None] = {src: None}
        pq = [(0.0, src)]
        while pq:
            d, u = heappop(pq)
            if d > dist[u]:
                continue
            for nb in neighbors4(u):
                if cand.grid_type.get(nb, NODE_TYPE.DEAD) == NODE_TYPE.DEAD:
                    continue
                if nb in all_exits and nb not in allow_exits:
                    continue
                w = 0.0 if nb in (cand.glyph_coord, src) or nb in allow_exits else cand.node_weight(nb, set())
                nd = d + w
                if nb not in dist or nd < dist[nb]:
                    dist[nb] = nd
                    prev[nb] = u
                    heappush(pq, (nd, nb))
        return dist, prev

    def trace(prev: dict, target: tuple[int, int]) -> set[tuple[int, int]]:
        out = set()
        cur = target
        while cur is not None:
            out.add(cur)
            cur = prev.get(cur)
        return out

    thresh = cand.glyph_threshold
    stat = cand.glyph_stat
    cands = [
        c
        for c, nt in cand.grid_type.items()
        if nt != NODE_TYPE.DEAD
        and cand.in_glyph_radius(c)
        and (cand.node_stat_grant_for(c, stat) > 0 if stat else False)
    ]
    max_stat = cand.glyph_stat_in_radius({c for c, nt in cand.grid_type.items() if nt != NODE_TYPE.DEAD})

    def greedy_fill(init_sel: set[tuple[int, int]], allow_exits: set[tuple[int, int]]) -> set[tuple[int, int]] | None:
        if not stat or thresh <= 0:
            return init_sel
        tree = set(init_sel)
        if cand.glyph_stat_in_radius(tree) >= thresh:
            return tree
        if cand.glyph_stat_in_radius(tree | set(cands)) < thresh:
            return None
        while cand.glyph_stat_in_radius(tree) < thresh:
            dist = {u: 0 for u in tree}
            parent: dict[tuple[int, int], tuple[int, int]] = {}
            q = deque(sorted(tree))
            while q:
                u = q.popleft()
                for nb in neighbors4(u):
                    if cand.grid_type.get(nb, NODE_TYPE.DEAD) == NODE_TYPE.DEAD or (
                        nb in all_exits and nb not in allow_exits
                    ):
                        continue
                    if nb not in dist:
                        dist[nb] = dist[u] + 1
                        parent[nb] = u
                        q.append(nb)
            rem_cands = [c for c in cands if c not in tree and c in dist]
            if not rem_cands:
                return None
            need = thresh - cand.glyph_stat_in_radius(tree)

            def score(c: tuple[int, int]):
                path = []
                cur = c
                while cur not in tree:
                    path.append(cur)
                    cur = parent[cur]
                pts = len(path)
                st_gain = sum(cand.node_stat_grant_for(x, stat) for x in path if cand.in_glyph_radius(x))
                eff_gain = min(need, st_gain)
                return (
                    1 if st_gain >= need else 0,
                    -pts if st_gain >= need else eff_gain / pts,
                    st_gain / pts,
                    cand.node_priority(c),
                )

            best_c = max(rem_cands, key=score)
            cur = best_c
            while cur not in tree:
                tree.add(cur)
                cur = parent[cur]
        return tree

    rots = [fixed_rot] if fixed_rot is not None else list(range(4))
    best = None
    for r in rots:
        u_exits = [EXIT_COORDS[PARAGON_EXIT((int(sd) - r) % 4)] for sd in s_exits]
        u_exits_set = set(u_exits)
        d_g, p_g = dijkstra(cand.glyph_coord, u_exits_set)
        if any(e not in d_g for e in u_exits):
            continue
        if len(u_exits) == 1:
            base_sel = trace(p_g, u_exits[0])
        elif len(u_exits) == 2:
            d_e0, p_e0 = dijkstra(u_exits[0], u_exits_set)
            d_e1, p_e1 = dijkstra(u_exits[1], u_exits_set)
            best_v = min(
                d_g.keys() & d_e0.keys() & d_e1.keys(),
                key=lambda v: d_g[v]
                + d_e0[v]
                + d_e1[v]
                - (0.0 if v in u_exits_set or v == cand.glyph_coord else 2.0 * cand.node_weight(v, set())),
            )
            base_sel = trace(p_g, best_v) | trace(p_e0, best_v) | trace(p_e1, best_v)
        else:
            base_sel = set()
            for e in u_exits:
                base_sel |= trace(p_g, e)
        sock_pts = len(base_sel) - 1
        best_t = greedy_fill(base_sel, u_exits_set)
        if len(u_exits) == 1 and stat and thresh > 0:
            d_e0, p_e0 = dijkstra(u_exits[0], u_exits_set)
            for sc in cands:
                if cand.node_stat_grant_for(sc, stat) >= 7 and sc not in base_sel:
                    d_sc, p_sc = dijkstra(sc, u_exits_set)
                    common = d_g.keys() & d_e0.keys() & d_sc.keys()
                    if not common:
                        continue
                    best_v = min(
                        common,
                        key=lambda v: d_g[v]
                        + d_e0[v]
                        + d_sc[v]
                        - (
                            0.0
                            if v in u_exits_set or v in (cand.glyph_coord, sc)
                            else 2.0 * cand.node_weight(v, set())
                        ),
                    )
                    sc_sel = trace(p_g, best_v) | trace(p_e0, best_v) | trace(p_sc, best_v)
                    t_sc = greedy_fill(sc_sel, u_exits_set)
                    if t_sc is not None and (
                        best_t is None
                        or (len(t_sc), -cand.glyph_stat_in_radius(t_sc))
                        < (len(best_t), -cand.glyph_stat_in_radius(best_t))
                    ):
                        best_t = t_sc
        thresh_pts = (len(best_t) - 1) if best_t is not None else 999
        g_stat = cand.glyph_stat_in_radius(best_t) if best_t is not None else 0
        key = (thresh_pts, sock_pts, -max_stat, -g_stat, r)
        if best is None or key < best[0]:
            best = (key, cand.board_name, bid, r, sock_pts, thresh_pts, g_stat, max_stat)
    return best


@register_hint_pass("Unused Legendary Board Replacement")
def _hint_pass_unused_legendary_board_swap(ctx: HintContext) -> list[Hint]:
    """
    When a non-starter board has a socketed Glyph but its Legendary node is not selected,
    check if any unused board for that class can connect the same active exit gates and
    reach the socketed Glyph's bonus threshold in fewer Paragon points (or in the same
    minimum points with higher in-radius stat capacity).
    """
    b = ctx.board
    if b.is_starter_board() or not b.glyph or b.glyph_coord is None:
        return []
    if any(b.grid_type[c] == NODE_TYPE.LEGENDARY for c in b.selected_nodes):
        return []

    s_exit_names = b.screen_active_exits()
    if not s_exit_names:
        return []
    s_exits = [PARAGON_EXIT[name] for name in s_exit_names]

    meta_data = _cached_meta_data()
    cur_eval = _eval_board_glyph_cost(b.board_type_id, b, s_exits, meta_data, fixed_rot=b.rotation)
    if cur_eval is None:
        return []
    _, _, _, _, cur_sock, cur_thresh, _, cur_max = cur_eval

    used_ids = {ob.board_type_id for ob in ctx.all_boards}
    cls_name = b.d4_class.value
    unused_ids = sorted(
        k
        for k in meta_data.keys()
        if k.startswith(f"Paragon_{cls_name}_")
        and not k.endswith("00")
        and k != "Paragon_Spirit_0"
        and k not in used_ids
    )
    if not unused_ids:
        return []

    alts = []
    for uid in unused_ids:
        ev = _eval_board_glyph_cost(uid, b, s_exits, meta_data)
        if ev is not None:
            alts.append(ev)
    if not alts:
        return []
    alts.sort(key=lambda x: x[0])
    _, bname, uid, r, sock, thresh, g_st, max_st = alts[0]

    thresh_saved = cur_thresh - thresh if (cur_thresh < 999 and thresh < 999) else 0
    sock_saved = cur_sock - sock
    pts_saved = max(thresh_saved, sock_saved)
    if pts_saved <= 0 and not (thresh == cur_thresh and sock == cur_sock and max_st > cur_max):
        return []

    leg_coords = [c for c, nt in b.grid_type.items() if nt == NODE_TYPE.LEGENDARY]
    leg_label = b.node_label(leg_coords[0]) if leg_coords else f"Legendary '{b.board_name}'"
    rares_used = [b.node_label(c) for c in sorted(b.selected_nodes) if b.grid_type[c] == NODE_TYPE.RARE]
    rare_note = (
        f" (if you only need this board for its Glyph socket / transit path and not {', '.join(rares_used)})"
        if rares_used
        else ""
    )

    exits_str = "/".join(s_exit_names)
    if pts_saved > 0:
        parts = []
        if sock_saved > 0:
            parts.append(f"connects the {exits_str} exit(s) + Glyph socket in {sock} pts instead of {cur_sock} (-{sock_saved} pt{'s' if sock_saved != 1 else ''})")
        if thresh_saved > 0 and b.glyph_stat and b.glyph_threshold > 0:
            parts.append(
                f"reaches '{b.glyph_name}' threshold ({g_st}/{b.glyph_threshold} {b.glyph_stat}) in "
                f"{thresh} Paragon points instead of {cur_thresh} (saving {thresh_saved} point{'s' if thresh_saved != 1 else ''}, "
                f"max {max_st} {b.glyph_stat} in radius)"
            )
        detail = " and ".join(parts) if parts else f"saves {pts_saved} Paragon point{'s' if pts_saved != 1 else ''}"
    else:
        detail = (
            f"connects the {exits_str} exit(s) and reaches '{b.glyph_name}' threshold in the same {thresh} Paragon points "
            f"while increasing maximum in-radius {b.glyph_stat} capacity from {cur_max} to {max_st} {b.glyph_stat} (+{max_st - cur_max} {b.glyph_stat})"
        )

    msg = (
        f"{leg_label} is unused on this board — switching to unused board '{bname}' ({uid}, rotated {r * 90}°) "
        f"{detail}{rare_note}."
    )
    return [
        Hint(
            ctx.board_index,
            b,
            "Alternative Board",
            msg,
            points_delta=max(0, pts_saved),
            screen_coords=[ctx.to_screen(b.glyph_coord)],
        )
    ]


@register_hint_pass("Rare & Magic Cluster Marginal & Periphery Cost")
def _hint_pass_cluster_marginal_cost(ctx: HintContext) -> list[Hint]:
    """
    Compute the exact marginal Paragon point cost of each Rare/Magic target cluster outside
    the Glyph radius, including semi-transit clusters, keep-Rare periphery pruning, and
    single peripheral Magic node detours.
    """
    b = ctx.board
    hints: list[Hint] = []

    cluster_targets = {u for u in b.target_nodes if b.grid_type[u] in CLUSTER_NODE_TYPES}
    for comp in grid_components(cluster_targets):
        if len(comp) <= 1:
            continue
        if not any(b.grid_type[c] in (NODE_TYPE.RARE, NODE_TYPE.MAGIC, NODE_TYPE.LEGENDARY) for c in comp):
            continue

        # 1) Check single peripheral MAGIC nodes on the border of `comp` that force a path detour
        for u in sorted(comp):
            if b.grid_type[u] != NODE_TYPE.MAGIC:
                continue
            ext_nbs = [nb for nb in ctx.selected_neighbors(u) if nb not in comp and nb not in b.target_nodes]
            if not ext_nbs or len(ctx.selected_neighbors(u)) != 2 or ctx.peel_ablation({u}) is not None:
                continue
            # Ensure `comp - {u}` stays a single connected component so we don't split the cluster
            if not is_grid_connected(comp - {u}):
                continue

            u_saved, u_rem, u_add = ctx.marginal_ablation({u}, force_exact=True)
            if u_saved >= 1 and u_add and all(b.priorities.get(b.grid_type[a], 0.0) > 0 for a in u_add):
                sc_u = ctx.to_screen(u)
                added_desc = ", ".join(f"{b.node_label(a)} at {ctx.to_screen(a)}" for a in sorted(u_add)[:2])
                hints.append(
                    Hint(
                        ctx.board_index,
                        b,
                        "Cluster Edge Detour",
                        f"Peripheral MAGIC node at {sc_u} forces a detour — unmarking it reroutes via "
                        f"{added_desc}, saving {u_saved} Paragon point{'s' if u_saved != 1 else ''} "
                        f"({ctx.format_stat_deltas(u_rem, u_add)}).",
                        points_delta=u_saved,
                        screen_coords=[sc_u],
                    )
                )

        rare_coords = [c for c in sorted(comp) if b.grid_type[c] in (NODE_TYPE.RARE, NODE_TYPE.LEGENDARY)]
        if rare_coords:
            anchor_c = rare_coords[0]
            cluster_title = b.node_label(anchor_c) + " cluster"
        else:
            anchor_c = min(comp)
            cluster_title = "Magic cluster"
        sc = ctx.to_screen(anchor_c)

        # Find peelable peripheral Magic nodes if the user wants to KEEP the Rare node(s) in `comp`
        # (exclude in-radius MAGIC nodes on boards whose Glyph is in `magic` mode)
        peelable_magic: list[tuple[int, int]] = []
        cand_magics = [
            c for c in sorted(comp)
            if b.grid_type[c] == NODE_TYPE.MAGIC and not (b.in_glyph_radius(c) and b.glyph_mode == "magic")
        ]
        cur_peel: set[tuple[int, int]] = set()
        for m_c in cand_magics:
            if ctx.peel_ablation(cur_peel | {m_c}) is not None:
                cur_peel.add(m_c)
        if cur_peel:
            peel_res = ctx.peel_ablation(cur_peel)
            if peel_res is not None and peel_res[0] >= 2:
                peelable_magic = sorted(cur_peel)

        p_coords_str = ", ".join(str(ctx.to_screen(c)) for c in peelable_magic[:4])

        def make_pruning_hint() -> Hint:
            return Hint(
                ctx.board_index,
                b,
                "Cluster Periphery Pruning",
                f"{cluster_title} around {sc} has {len(peelable_magic)} non-transit Magic leaf nodes "
                f"({p_coords_str}) — dropping them while keeping {b.node_label(anchor_c)} saves "
                f"{len(peelable_magic)} Paragon points.",
                points_delta=len(peelable_magic),
                screen_coords=[sc],
            )

        if any(b.in_glyph_radius(c) for c in comp):
            if len(peelable_magic) >= 2 and rare_coords:
                hints.append(make_pruning_hint())
            continue

        # 2) Full cluster ablation + Keep-Rare periphery peeling for out-of-radius clusters
        pts_saved, removed, added = ctx.marginal_ablation(comp, force_exact=True)
        path_overhead = pts_saved - len(comp)

        if path_overhead >= 1 and pts_saved >= 4:
            delta_str = ctx.format_stat_deltas(removed, added)
            peel_note = (
                f"; or keep {b.node_label(anchor_c)} and drop {len(peelable_magic)} leaf Magic nodes "
                f"({p_coords_str}) to save {len(peelable_magic)} points"
                if (peelable_magic and rare_coords)
                else ""
            )
            hints.append(
                Hint(
                    ctx.board_index,
                    b,
                    "Cluster Spur Cost",
                    f"{cluster_title} around {sc} ({len(comp)} target nodes) requires {path_overhead} "
                    f"dedicated path node{'s' if path_overhead > 1 else ''} — disabling this cluster "
                    f"saves {pts_saved} Paragon points ({delta_str}){peel_note}.",
                    points_delta=pts_saved,
                    screen_coords=[sc],
                )
            )
        elif path_overhead <= 0 and pts_saved >= 3 and pts_saved * 2 >= len(comp):
            # Semi-transit or side-attached cluster where 0-2 nodes carry the highway and 3-5 nodes hang off the side
            highway_nodes = len(comp) - pts_saved
            delta_str = ctx.format_stat_deltas(removed, added)
            peel_note = (
                f"; or keep {b.node_label(anchor_c)} and drop {len(peelable_magic)} peripheral Magic nodes "
                f"({p_coords_str}) to save {len(peelable_magic)} points"
                if (peelable_magic and rare_coords)
                else ""
            )
            usage_desc = (
                "is not needed for transit on the main path"
                if highway_nodes == 0
                else f"only uses {highway_nodes} node{'s' if highway_nodes != 1 else ''} on the main path"
            )
            hints.append(
                Hint(
                    ctx.board_index,
                    b,
                    "Transit Cluster Periphery",
                    f"{cluster_title} around {sc} ({len(comp)} target nodes) {usage_desc} — disabling the cluster "
                    f"saves {pts_saved} Paragon points ({delta_str}){peel_note}.",
                    points_delta=pts_saved,
                    screen_coords=[sc],
                )
            )
        elif len(peelable_magic) >= 3 and rare_coords:
            hints.append(make_pruning_hint())
    return hints


@register_hint_pass("Manual Normal Stat Targets Outside Radius")
def _hint_pass_manual_path_targets(ctx: HintContext) -> list[Hint]:
    """
    Check normal +5 stat nodes (DEX, INT, STR, WILL) marked as required targets that do not feed
    the socketed Glyph (outside the Glyph radius, or non-governed inside a non-normal Glyph's radius).
    """
    b = ctx.board
    manual_nodes = {
        u for u in b.target_nodes
        if u not in b.auto_glyph_bonus_nodes
        and b.grid_type[u] in PLAIN_STAT_TYPES
        and not (b.is_starter_board() and u == STARTER_START_COORD)
        and not b.feeds_glyph(u)
    }
    if not manual_nodes:
        return []

    pts_saved, removed, added = ctx.marginal_ablation(manual_nodes, force_exact=True)
    sc_list = sorted(ctx.to_screen(u) for u in manual_nodes)
    coords_str = ", ".join(str(sc) for sc in sc_list[:5])
    if len(sc_list) > 5:
        coords_str += f" (+{len(sc_list) - 5} more)"
    scope_label = (
        "outside the Glyph radius"
        if all(not b.in_glyph_radius(u) for u in manual_nodes)
        else "not feeding the socketed Glyph"
    )

    if pts_saved > 0 or (pts_saved == 0 and added):
        delta_str = ctx.format_stat_deltas(removed, added)
        return [
            Hint(
                ctx.board_index,
                b,
                "Manual Path Targets",
                f"{len(manual_nodes)} normal stat node{'s' if len(manual_nodes) > 1 else ''} {scope_label} "
                f"({coords_str}) {'are' if len(manual_nodes) > 1 else 'is'} marked as mandatory target"
                f"{'s' if len(manual_nodes) > 1 else ''} — unmarking {'them' if len(manual_nodes) > 1 else 'it'} "
                f"saves {pts_saved} Paragon point{'s' if pts_saved != 1 else ''} ({delta_str}) "
                f"(or use --relax-path-nodes).",
                points_delta=pts_saved,
                screen_coords=sc_list,
            )
        ]

    return [
        Hint(
            ctx.board_index,
            b,
            "Manual Path Targets",
            f"{len(manual_nodes)} normal stat node{'s' if len(manual_nodes) > 1 else ''} {scope_label} "
            f"({coords_str}) {'are' if len(manual_nodes) > 1 else 'is'} marked as mandatory target"
            f"{'s' if len(manual_nodes) > 1 else ''} (currently on an optimal path, but can be unmarked "
            f"as path nodes rather than fixed targets, or use --relax-path-nodes).",
            points_delta=0,
            screen_coords=sc_list,
        )
    ]


@register_hint_pass("Nearby & Reroute-Accessible Rare/Legendary Nodes")
def _hint_pass_nearby_unselected_rares(ctx: HintContext) -> list[Hint]:
    """
    Find unselected Rare (especially +10 Core Stat Rares) or Legendary nodes that can be unlocked
    for 0-2 additional Paragon points either by a direct 1-2 step extension from the current path
    or by an exact fixed-exit path reroute.
    """
    b = ctx.board
    hints: list[Hint] = []
    old_g_stat = b.glyph_stat_in_radius(b.selected_nodes) if (b.glyph and b.glyph_threshold > 0) else 0

    # Multi-source BFS up to distance 4 from `b.selected_nodes`
    dist: dict[tuple[int, int], int] = {c: 0 for c in b.selected_nodes}
    parent: dict[tuple[int, int], tuple[int, int]] = {}
    q = deque(sorted(b.selected_nodes))
    while q:
        u = q.popleft()
        d = dist[u]
        if d >= 4:
            continue
        r, c = u
        for nb in ((r + 1, c), (r - 1, c), (r, c + 1), (r, c - 1)):
            if nb not in b.grid_type or b.grid_type[nb] == NODE_TYPE.DEAD or nb in EXIT_COORDS.values():
                continue
            if nb not in dist:
                dist[nb] = d + 1
                parent[nb] = u
                q.append(nb)

    for (r, c), nt in sorted(b.grid_type.items()):
        if (r, c) in b.selected_nodes:
            continue
        if nt not in (NODE_TYPE.RARE, NODE_TYPE.LEGENDARY):
            continue
        d = dist.get((r, c))
        if d is None:
            continue
        # Skip in-radius Rare nodes at distance 1 that Pass 1 already reported in `gov_adj`
        if b.in_glyph_radius((r, c)) and d == 1 and (
            b.glyph_mode == "rare"
            or (
                (b.glyph_mode in ("stat", "all") or old_g_stat < b.glyph_threshold)
                and bool(b.glyph_stat and b.node_stat_grant_for((r, c), b.glyph_stat) > 0)
            )
        ):
            continue
        info = b.special_nodes.get((r, c))
        has_core_stat = bool(info and info[1])
        sc = ctx.to_screen((r, c))
        adj_magics = sum(
            1
            for nb in ((r + 1, c), (r - 1, c), (r, c + 1), (r, c - 1))
            if b.grid_type.get(nb, NODE_TYPE.DEAD).name.startswith("MAGIC")
        )
        cluster_note = f" (with {adj_magics} adjacent Magic cluster nodes)" if adj_magics else ""

        ins = ctx.insertion_cost({(r, c)})
        if ins is not None:
            pts_added, removed, added = ins
            if removed and pts_added < d and (
                pts_added <= 1 or (pts_added == 2 and (has_core_stat or nt == NODE_TYPE.LEGENDARY))
            ):
                delta_str = ctx.format_stat_deltas(removed, added)
                thresh_note = ""
                if b.glyph and b.glyph_stat and b.glyph_threshold > 0:
                    new_g_stat = b.glyph_stat_in_radius((b.selected_nodes - removed) | added)
                    if new_g_stat != old_g_stat:
                        if new_g_stat >= b.glyph_threshold:
                            thresh_note = f"; '{b.glyph_name}' {new_g_stat}/{b.glyph_threshold} {b.glyph_stat}"
                        else:
                            thresh_note = f"; warning: drops '{b.glyph_name}' to {new_g_stat}/{b.glyph_threshold} {b.glyph_stat}"
                hints.append(
                    Hint(
                        ctx.board_index,
                        b,
                        f"Reroute +{pts_added}-Pt Rare/Legendary",
                        f"Unselected {b.node_label((r, c))} at {sc}{cluster_note} is {d} steps from your "
                        f"current path, but rerouting {len(removed)} path nodes unlocks it for only "
                        f"+{pts_added} additional Paragon point{'s' if pts_added != 1 else ''} ({delta_str}{thresh_note}).",
                        points_delta=0,
                        screen_coords=[sc],
                    )
                )
                continue

        if d == 1:
            hints.append(
                Hint(
                    ctx.board_index,
                    b,
                    "Nearby 1-Pt Rare/Legendary",
                    f"Unselected {b.node_label((r, c))} at {sc}{cluster_note} is directly adjacent "
                    f"to your active path (can be unlocked for just 1 Paragon point).",
                    points_delta=0,
                    screen_coords=[sc],
                )
            )
        elif d == 2 and (has_core_stat or nt == NODE_TYPE.LEGENDARY):
            bridge = parent[(r, c)]
            hints.append(
                Hint(
                    ctx.board_index,
                    b,
                    "Nearby 2-Pt Rare/Legendary",
                    f"Unselected {b.node_label((r, c))} at {sc}{cluster_note} is only 2 steps "
                    f"from your path via {b.node_label(bridge)} at {ctx.to_screen(bridge)}.",
                    points_delta=0,
                    screen_coords=[sc],
                )
            )
    return hints


@register_hint_pass("Cross-Board Glyph Socket Swaps")
def _hint_pass_glyph_socket_swaps(ctx: HintContext) -> list[Hint]:
    """
    Check if swapping this board's socketed Glyph with another board's Glyph increases
    primary-stat radius capacity or immediate primary bonus scaling on current selected nodes.
    """
    b = ctx.board
    if not b.glyph or b.glyph_coord is None or not b.glyph_stat:
        return []

    def eval_glyph_on_board(glyph_board: ParagonBoard, target_board: ParagonBoard) -> tuple[bool, int, str]:
        """Return `(threshold_met, primary_score, score_unit)` if `glyph_board`'s Glyph is socketed on `target_board`."""
        if target_board.glyph_coord is None:
            return (False, 0, "")
        gr, gc = target_board.glyph_coord
        rad = glyph_board.glyph_radius
        g_stat = glyph_board.glyph_stat
        g_mode = glyph_board.glyph_mode
        stat_sum = 0
        normal_cnt = 0
        magic_cnt = 0
        rare_cnt = 0
        for c in target_board.selected_nodes:
            if abs(c[0] - gr) + abs(c[1] - gc) <= rad:
                stat_sum += target_board.node_stat_grant_for(c, g_stat)
                nt = target_board.grid_type[c]
                if nt in (NODE_TYPE.DEX, NODE_TYPE.INT, NODE_TYPE.STR, NODE_TYPE.WILL):
                    normal_cnt += 1
                elif nt.name.startswith("MAGIC"):
                    magic_cnt += 1
                elif nt == NODE_TYPE.RARE:
                    rare_cnt += 1
        met = stat_sum >= glyph_board.glyph_threshold
        if g_mode == "normal":
            return (met, normal_cnt, "normal nodes")
        if g_mode == "magic":
            return (met, magic_cnt, "Magic nodes")
        if g_mode == "rare":
            return (met, rare_cnt, "Rare nodes")
        return (met, stat_sum, g_stat)

    hints: list[Hint] = []

    # 1) Immediate 0-point socket swap on current selected nodes (report on lower board_index of the pair)
    my_met, my_score, my_unit = eval_glyph_on_board(b, b)
    for o_idx, ob in enumerate(ctx.all_boards, 1):
        if o_idx <= ctx.board_index or not ob.glyph or ob.glyph_coord is None or ob.glyph == b.glyph:
            continue
        o_met, o_score, o_unit = eval_glyph_on_board(ob, ob)
        my_on_o_met, my_on_o_score, _ = eval_glyph_on_board(b, ob)
        o_on_my_met, o_on_my_score, _ = eval_glyph_on_board(ob, b)
        if not (my_on_o_met and o_on_my_met):
            continue
        unlocks_thresh = not my_met or not o_met
        if unlocks_thresh:
            unlocked_names = []
            if not my_met:
                unlocked_names.append(
                    f"'{b.glyph_name}' ({b.glyph_stat_in_radius(b.selected_nodes)}/{b.glyph_threshold} -> "
                    f"{ob.glyph_stat_in_radius(ob.selected_nodes) if b.glyph_stat == ob.glyph_stat else sum(ob.node_stat_grant_for(c, b.glyph_stat) for c in ob.selected_nodes if abs(c[0] - ob.glyph_coord[0]) + abs(c[1] - ob.glyph_coord[1]) <= b.glyph_radius)}/{b.glyph_threshold} {b.glyph_stat})"
                )
            if not o_met:
                unlocked_names.append(
                    f"'{ob.glyph_name}' ({ob.glyph_stat_in_radius(ob.selected_nodes)}/{ob.glyph_threshold} -> "
                    f"{sum(b.node_stat_grant_for(c, ob.glyph_stat) for c in b.selected_nodes if abs(c[0] - b.glyph_coord[0]) + abs(c[1] - b.glyph_coord[1]) <= ob.glyph_radius)}/{ob.glyph_threshold} {ob.glyph_stat})"
                )
            hints.append(
                Hint(
                    ctx.board_index,
                    b,
                    "0-Pt Glyph Socket Swap",
                    f"Swapping '{b.glyph_name}' (#{ctx.board_index} '{b.board_name}') with '{ob.glyph_name}' "
                    f"(#{o_idx} '{ob.board_name}') on your current path unlocks the bonus threshold for "
                    f"{' and '.join(unlocked_names)} for 0 Paragon points (while keeping both Glyphs active).",
                    points_delta=0,
                    screen_coords=[ctx.to_screen(b.glyph_coord)],
                )
            )
        elif my_on_o_score >= my_score and o_on_my_score >= o_score and (
            my_on_o_score > my_score or o_on_my_score > o_score
        ):
            d1 = my_on_o_score - my_score
            d2 = o_on_my_score - o_score
            gains = []
            unchanged = []
            if d1 > 0:
                gains.append(f"+{d1} {my_unit} for '{b.glyph_name}' ({my_score} -> {my_on_o_score})")
            else:
                unchanged.append(f"'{b.glyph_name}' stays at {my_score} {my_unit}")
            if d2 > 0:
                gains.append(f"+{d2} {o_unit} for '{ob.glyph_name}' ({o_score} -> {o_on_my_score})")
            else:
                unchanged.append(f"'{ob.glyph_name}' stays at {o_score} {o_unit}")
            proxy_note = ""
            if b.glyph_mode != "stat" or ob.glyph_mode != "stat":
                proxy_note = (
                    f" ({', '.join(unchanged)}; verify boosted node affixes when moving node-amplifying glyphs)"
                    if unchanged
                    else " (verify boosted node affixes when moving node-amplifying glyphs)"
                )
            elif unchanged:
                proxy_note = f" ({', '.join(unchanged)})"
            hints.append(
                Hint(
                    ctx.board_index,
                    b,
                    "0-Pt Glyph Socket Swap",
                    f"Swapping '{b.glyph_name}' (#{ctx.board_index} '{b.board_name}') with '{ob.glyph_name}' "
                    f"(#{o_idx} '{ob.board_name}') on your current path gains "
                    f"{' and '.join(gains)} for 0 Paragon points{proxy_note}.",
                    points_delta=0,
                    screen_coords=[ctx.to_screen(b.glyph_coord)],
                )
            )

    # 2) Radius stat ceiling swap: Secondary-Stat Glyph occupying a higher Primary-Stat ceiling board
    # Evaluate each board's ceiling using the INCOMING glyph's radius (`ob.glyph_radius` vs `b.glyph_radius`).
    if b.glyph_stat not in (ctx.primary_stat, "ALL"):
        my_sec_cap = ctx.max_radius_stat(b.glyph_stat, radius=b.glyph_radius)
        best_swap = None
        for o_idx, ob in enumerate(ctx.all_boards, 1):
            if o_idx == ctx.board_index or not ob.glyph or ob.glyph_coord is None:
                continue
            o_ctx = ctx.shared_contexts.get(o_idx)
            if o_ctx is None:
                continue
            if ob.glyph_stat == ctx.primary_stat and ob.glyph_mode in ("stat", "all", "normal"):
                my_prim_cap = ctx.max_radius_stat(ctx.primary_stat, radius=ob.glyph_radius)
                o_prim_cap = o_ctx.max_radius_stat(ctx.primary_stat, radius=ob.glyph_radius)
                o_sec_cap = o_ctx.max_radius_stat(b.glyph_stat, radius=b.glyph_radius)
                if my_prim_cap > o_prim_cap and o_sec_cap >= max(b.glyph_threshold, my_sec_cap):
                    gain = my_prim_cap - o_prim_cap
                    cand = (
                        o_sec_cap >= my_sec_cap,
                        ob.glyph_mode == "stat",
                        gain,
                        o_sec_cap,
                        o_idx,
                        ob,
                        o_prim_cap,
                        my_prim_cap,
                    )
                    if best_swap is None or cand > best_swap:
                        best_swap = cand
        if best_swap is not None:
            _, _, gain, o_sec_cap, o_idx, ob, o_prim_cap, my_prim_cap = best_swap
            sec_cmp = (
                f"the same {o_sec_cap} {b.glyph_stat} radius ceiling"
                if o_sec_cap == my_sec_cap
                else f"a higher {o_sec_cap} {b.glyph_stat} radius ceiling (vs. {my_sec_cap} here)"
            )
            hints.append(
                Hint(
                    ctx.board_index,
                    b,
                    "Glyph Socket Swap",
                    f"Board #{ctx.board_index} '{b.board_name}' has a higher in-radius {ctx.primary_stat} ceiling "
                    f"({my_prim_cap} {ctx.primary_stat} at R={ob.glyph_radius}) than #{o_idx} '{ob.board_name}' "
                    f"({o_prim_cap} {ctx.primary_stat}), while #{o_idx} '{ob.board_name}' has {sec_cmp} — "
                    f"consider moving '{b.glyph_name}' ({b.glyph_stat}) to #{o_idx} '{ob.board_name}' and socketing "
                    f"'{ob.glyph_name}' ({ctx.primary_stat}) here (+{gain} {ctx.primary_stat} radius ceiling before pathing costs).",
                    points_delta=0,
                    screen_coords=[ctx.to_screen(b.glyph_coord)],
                )
            )
    return hints


def run_optimization_hints(
    boards: list[ParagonBoard],
    solver: str = "auto",
    progress_file=sys.stderr,
) -> list[Hint]:
    """Execute all registered hint passes across `boards`, reporting progress if needed."""
    all_hints: list[Hint] = []
    t0 = time.monotonic()
    shown_progress = False
    is_tty = hasattr(progress_file, "isatty") and progress_file.isatty()

    contexts: dict[int, HintContext] = {}
    for idx, b in enumerate(boards, 1):
        contexts[idx] = HintContext(idx, b, boards, solver=solver, shared_contexts=contexts)

    total_steps = len(boards) * len(HINT_PASSES)
    step = 0
    for idx, b in enumerate(boards, 1):
        ctx = contexts[idx]
        for pass_name, pass_fn in HINT_PASSES:
            step += 1
            elapsed = time.monotonic() - t0
            if elapsed >= 2.0:
                shown_progress = True
                msg = f"Analyzing optimization hints [{step}/{total_steps}]: #{idx} {b.board_name} ({pass_name})..."
                if is_tty:
                    print(f"\r\033[K{msg}", end="", file=progress_file, flush=True)
                elif step == 1 or step % 5 == 0:
                    print(msg, file=progress_file, flush=True)
            all_hints.extend(pass_fn(ctx))

    if shown_progress and is_tty:
        print("\r\033[K", end="", file=progress_file, flush=True)
    return all_hints


def _format_hint_bullet(h: Hint, color: bool, indent: str = "     ") -> str:
    RESET = "\033[0m" if color else ""
    C_SAVE = "\033[1;38;2;110;245;160m" if color else ""
    C_INFO = "\033[1;38;2;255;190;105m" if color else ""
    if h.points_delta > 0:
        badge = f"{C_SAVE}[-{h.points_delta} pt{'s' if h.points_delta != 1 else ''}]{RESET}"
    else:
        badge = f"{C_INFO}[{h.category}]{RESET}"
    return f"{indent}• {badge} {h.message}"


def format_board_hints(board_hints: list[Hint], color: bool = True) -> str:
    """Format the hints for a single board to be displayed directly beneath its visualization."""
    if not board_hints:
        return ""
    RESET = "\033[0m" if color else ""
    C_HEADER = "\033[1;38;2;255;215;100m" if color else ""
    lines = [f"   {C_HEADER}Hints (x, y):{RESET}"]
    for h in sorted(board_hints, key=lambda x: (-x.points_delta, x.category)):
        lines.append(_format_hint_bullet(h, color, indent="     "))
    return "\n".join(lines)


def print_hints(hints: list[Hint], color: bool = True, file=sys.stderr):
    RESET = "\033[0m" if color else ""
    C_HEADER = "\033[1;38;2;255;215;100m" if color else ""
    C_BOARD = "\033[1;38;2;130;210;255m" if color else ""

    print(f"\n{C_HEADER}╭─ Paragon Build Optimization Hints (Screen Coordinates x, y) ─╮{RESET}", file=file)
    print("  Note: Hints evaluate alternative local edits; point savings can overlap — re-run --hints after editing.", file=file)
    if not hints:
        print("  No inefficient targets or high-cost spurs detected.", file=file)
        return

    by_board: dict[int, list[Hint]] = {}
    for h in hints:
        by_board.setdefault(h.board_index, []).append(h)

    for b_idx in sorted(by_board.keys()):
        b_hints = by_board[b_idx]
        first = b_hints[0]
        print(f"\n  {C_BOARD}#{b_idx} {first.board_name} ({first.board_id}){RESET}", file=file)
        for h in sorted(b_hints, key=lambda x: (-x.points_delta, x.category)):
            print(_format_hint_bullet(h, color, indent="    "), file=file)
    print("", file=file)


def enforce_budget(
    boards: list[ParagonBoard],
    budget: int | None = MAX_PARAGON_POINTS,
    solver: str = "auto",
) -> tuple[list[ParagonBoard], bool, list[str]]:
    """
    Ensure the optimized `boards` use at most `budget` purchased Paragon points (default `342`,
    the Diablo 4 maximum; pass `None` or `<= 0` for unlimited).

    When the initial solution exceeds `budget`:
      1. Relaxes out-of-radius / non-Glyph plain `+5` stat path targets (0 loss to Glyphs or clusters).
      2. Iteratively trims excess in-radius plain `+5` Glyph stat targets down toward each socketed
         Glyph's bonus threshold (`25` or `40`), prioritizing 2-3 pt bridge detours and secondary-stat
         Glyphs first while keeping every Glyph's bonus threshold active.
      3. Never drops Rare, Magic, or Legendary target nodes. If the minimum-threshold tree still exceeds
         `budget`, returns `feasible = False` together with actionable cluster/leaf-board removal notes.

    Returns `(boards, feasible, notes)`.
    """
    if not boards or budget is None or budget <= 0:
        return (boards, True, [])

    def total_pts(bs: list[ParagonBoard]) -> int:
        return sum(len(b.purchased_nodes()) for b in bs)

    initial_pts = total_pts(boards)
    if initial_pts <= budget:
        return (boards, True, [])

    orig_b_pts = {id(b): len(b.purchased_nodes()) for b in boards}
    orig_g_stat = {id(b): b.glyph_stat_in_radius() for b in boards}
    req_thresh = {
        id(b): (min(b.glyph_threshold, orig_g_stat[id(b)]) if (b.glyph and b.glyph_stat and b.glyph_threshold > 0) else 0)
        for b in boards
    }
    ctxs: dict[int, HintContext] = {}
    ctx_by_id: dict[int, HintContext] = {}
    for idx, b in enumerate(boards, 1):
        ctx = HintContext(idx, b, boards, solver=solver, shared_contexts=ctxs)
        ctxs[idx] = ctx
        ctx_by_id[id(b)] = ctx

    corridor_saved_by_board: dict[int, tuple[int, int]] = {}  # id(b) -> (num_relaxed_targets, pts_saved)
    trimmed_in_radius: dict[int, list[tuple[int, int]]] = {id(b): [] for b in boards}

    def ablate_single_target(b: ParagonBoard, u: tuple[int, int]) -> set[tuple[int, int]] | None:
        """Fast O(1) leaf-spur walk when `u` has degree 1 in `b.selected_nodes`, else exact fixed-exit solve."""
        ctx = ctx_by_id[id(b)]
        if len(ctx.selected_neighbors(u)) == 1:
            spur, anchor = ctx.trace_leaf_spur(u)
            if anchor is not None and anchor in ctx.base_required:
                return b.selected_nodes - set(spur)
        sol = ctx.solve_without({u})
        return sol[2] if sol is not None else None

    cur_pts = initial_pts

    # Step 1: Relax non-Glyph plain +5 stat corridors on boards that still have them
    for b in boards:
        if cur_pts <= budget:
            break
        ctx = ctx_by_id[id(b)]
        non_glyph_plain = {
            c for c in b.target_nodes
            if b.grid_type[c] in PLAIN_STAT_TYPES and not b.feeds_glyph(c)
        }
        if not non_glyph_plain:
            continue
        need = cur_pts - budget
        trimmed_any_single = False
        while need > 0 and non_glyph_plain:
            best_u = None
            best_sel = None
            best_saved = 0
            for u in sorted(non_glyph_plain, key=lambda c: b.node_priority(c)):
                cand_sel = ablate_single_target(b, u)
                if cand_sel is None:
                    continue
                saved = len(b.selected_nodes) - len(cand_sel)
                if saved > best_saved and b.glyph_stat_in_radius(cand_sel) >= req_thresh[id(b)]:
                    best_saved = saved
                    best_u = u
                    best_sel = cand_sel
            if best_u is None:
                break
            b.target_nodes.remove(best_u)
            b.relaxed_nodes.add(best_u)
            non_glyph_plain.remove(best_u)
            b.selected_nodes = best_sel
            cur_pts -= best_saved
            need -= best_saved
            prev_n, prev_s = corridor_saved_by_board.get(id(b), (0, 0))
            corridor_saved_by_board[id(b)] = (prev_n + 1, prev_s + best_saved)
            trimmed_any_single = True

        # If single-node trims didn't reach budget (e.g. degree-2 corridor chain), relax all non_glyph_plain on b
        if cur_pts > budget and non_glyph_plain:
            sol = ctx.solve_without(non_glyph_plain)
            cand_sel = sol[2] if sol is not None else None
            if cand_sel is not None and b.glyph_stat_in_radius(cand_sel) >= req_thresh[id(b)]:
                saved = len(b.selected_nodes) - len(cand_sel)
                if saved > 0 or not trimmed_any_single:
                    b.target_nodes -= non_glyph_plain
                    b.relaxed_nodes |= non_glyph_plain
                    if saved > 0:
                        b.selected_nodes = cand_sel
                        cur_pts -= saved
                        prev_n, prev_s = corridor_saved_by_board.get(id(b), (0, 0))
                        corridor_saved_by_board[id(b)] = (prev_n + len(non_glyph_plain), prev_s + saved)

    # Step 2: Greedily trim excess in-radius plain +5 Glyph stat targets down toward each Glyph's bonus threshold
    already_pared: set[int] = set()
    while cur_pts > budget:
        need = cur_pts - budget
        best_cand = None  # (score_tuple, b, u, cand_sel, saved)
        for b in boards:
            ctx = ctx_by_id[id(b)]
            cur_g = b.glyph_stat_in_radius()
            r_thresh = req_thresh[id(b)]
            if cur_g <= r_thresh:
                continue
            is_sec_glyph = 1 if (b.glyph_stat and b.glyph_stat != ctx.primary_stat) else 0
            plain_in_rad = [
                c for c in b.target_nodes
                if b.grid_type[c] in PLAIN_STAT_TYPES and b.in_glyph_radius(c)
            ]
            for u in plain_in_rad:
                cand_sel = ablate_single_target(b, u)
                if cand_sel is None:
                    continue
                saved = len(b.selected_nodes) - len(cand_sel)
                g_lost = max(0, cur_g - b.glyph_stat_in_radius(cand_sel))
                if saved <= 0 or cur_g - g_lost < r_thresh:
                    continue
                zero_loss = 1 if g_lost == 0 else 0
                eff = saved / max(1, g_lost)
                exact_fit = 1 if saved <= need else 0
                cushion = cur_g - r_thresh
                score = (zero_loss, eff, is_sec_glyph, exact_fit, cushion, -b.node_priority(u), -u[0], -u[1])
                if best_cand is None or score > best_cand[0]:
                    best_cand = (score, b, u, cand_sel, saved)

        if best_cand is not None:
            _, b, u, cand_sel, saved = best_cand
            b.target_nodes.discard(u)
            b.relaxed_nodes.add(u)
            trimmed_in_radius[id(b)].append(u)
            b.selected_nodes = cand_sel
            cur_pts -= saved
            continue

        # If no single plain +5 stat target saved points (e.g. tightly coupled in-radius stat pocket),
        # pare a board with remaining excess Glyph stat down to its minimum-point threshold tree
        pared_board = False
        cand_boards = sorted(
            [b for b in boards if id(b) not in already_pared and b.glyph_stat_in_radius() > req_thresh[id(b)]],
            key=lambda x: (
                -(1 if (x.glyph_stat and x.glyph_stat != ctx_by_id[id(x)].primary_stat) else 0),
                -(x.glyph_stat_in_radius() - req_thresh[id(x)]),
            ),
        )
        for b in cand_boards:
            already_pared.add(id(b))
            plain_in_rad = {
                c for c in b.target_nodes
                if b.grid_type[c] in PLAIN_STAT_TYPES and b.in_glyph_radius(c)
            }
            if not plain_in_rad:
                continue
            min_sel = ctx_by_id[id(b)].pare_targets_to_threshold(plain_in_rad, thresh=req_thresh[id(b)])
            if min_sel is None or len(min_sel) >= len(b.selected_nodes):
                continue

            max_save = len(b.selected_nodes) - len(min_sel)
            add_back_budget = max(0, max_save - need)
            cur_sel = set(min_sel)
            kept_targets = (b.target_nodes - plain_in_rad) | (plain_in_rad & cur_sel)
            while add_back_budget > 0:
                adj_cands = [
                    c for c in (plain_in_rad - cur_sel)
                    if any(nb in cur_sel for nb in neighbors4(c))
                ]
                if not adj_cands:
                    break
                best_add = max(adj_cands, key=lambda c: (b.node_stat_grant_for(c, b.glyph_stat or ""), b.node_priority(c)))
                cur_sel.add(best_add)
                kept_targets.add(best_add)
                add_back_budget -= 1

            dropped_in_rad = sorted(plain_in_rad - kept_targets)
            b.target_nodes = kept_targets
            b.relaxed_nodes |= set(dropped_in_rad)
            b.selected_nodes = cur_sel
            trimmed_in_radius[id(b)].extend(dropped_in_rad)
            cur_pts = total_pts(boards)
            pared_board = True
            break

        if not pared_board:
            break

    # Step 3: Build human-readable notes explaining how we got under budget (or why it is infeasible)
    feasible = cur_pts <= budget
    notes: list[str] = []
    if feasible:
        notes.append(
            f"Initial optimal tree required {initial_pts} Paragon points "
            f"({initial_pts - budget} point{'s' if initial_pts - budget != 1 else ''} over --budget {budget})."
        )
        notes.append(
            f"How we got under budget ({initial_pts} -> {cur_pts} Paragon points, "
            f"all Legendary/Rare/Magic nodes and Glyph bonus thresholds preserved):"
        )
        for idx, b in enumerate(boards, 1):
            b_saved = orig_b_pts[id(b)] - len(b.purchased_nodes())
            if b_saved <= 0 and not trimmed_in_radius[id(b)] and id(b) not in corridor_saved_by_board:
                continue
            g_old = orig_g_stat[id(b)]
            g_new = b.glyph_stat_in_radius()
            g_note = ""
            if b.glyph and b.glyph_stat and b.glyph_threshold > 0:
                if g_new != g_old:
                    g_note = f" ('{b.glyph_name}': {g_old}/{b.glyph_threshold} -> {g_new}/{b.glyph_threshold} {b.glyph_stat})"
                else:
                    g_note = f" ('{b.glyph_name}' unchanged at {g_new}/{b.glyph_threshold} {b.glyph_stat})"
            parts = []
            if id(b) in corridor_saved_by_board:
                n_rel, s_rel = corridor_saved_by_board[id(b)]
                if s_rel > 0:
                    parts.append(f"rerouted {n_rel} non-Glyph plain stat path node{'s' if n_rel != 1 else ''}")
            if trimmed_in_radius[id(b)]:
                t_coords = [ctx_by_id[id(b)].to_screen(c) for c in trimmed_in_radius[id(b)]]
                coord_preview = ", ".join(str(c) for c in t_coords[:4])
                if len(t_coords) > 4:
                    coord_preview += f", ... (+{len(t_coords) - 4} more)"
                parts.append(
                    f"trimmed {len(t_coords)} excess in-radius {b.glyph_stat or 'stat'} target"
                    f"{'s' if len(t_coords) != 1 else ''} ({coord_preview})"
                )
            action_str = " and ".join(parts) if parts else "adjusted pathing"
            notes.append(
                f"  • #{idx} {b.board_name}: {action_str} -> saved {b_saved} Paragon point"
                f"{'s' if b_saved != 1 else ''}{g_note}"
            )
    else:
        over = cur_pts - budget
        notes.append(
            f"No solution possible within --budget {budget} while keeping all selected "
            f"Legendary/Rare/Magic nodes and Glyph bonus thresholds "
            f"(minimum required: {cur_pts} Paragon points, {over} point{'s' if over != 1 else ''} over budget)."
        )
        if cur_pts < initial_pts:
            notes.append(
                f"Relaxing plain stat corridors and trimming excess in-radius Glyph stat nodes down to their "
                f"bonus thresholds reduced the build from {initial_pts} -> {cur_pts} Paragon points "
                f"(-{initial_pts - cur_pts} pts), leaving {over} point{'s' if over != 1 else ''} still to cut."
            )
        cut_hints: list[Hint] = []
        for idx in range(1, len(boards) + 1):
            cut_hints.extend(_hint_pass_high_cost_single_targets(ctxs[idx]))
            cut_hints.extend(_hint_pass_cluster_marginal_cost(ctxs[idx]))
        cut_hints = [h for h in cut_hints if h.points_delta > 0]
        cut_hints.sort(key=lambda h: (-h.points_delta, h.board_index))
        if cut_hints:
            notes.append(f"Suggested Rare/Magic cluster or leaf-board cuts to free the remaining {over} point{'s' if over != 1 else ''}:")
            for h in cut_hints[:5]:
                notes.append(f"  • [-{h.points_delta} pt{'s' if h.points_delta != 1 else ''}] #{h.board_index} {h.board_name}: {h.message}")

    return (boards, feasible, notes)


def print_budget_report(
    budget: int,
    feasible: bool,
    notes: list[str],
    color: bool = True,
    file=sys.stderr,
) -> None:
    """Print the budget enforcement summary block if any action or warning was generated."""
    if not notes:
        return
    RESET = "\033[0m" if color else ""
    C_OK = "\033[1;38;2;110;245;160m" if color else ""
    C_WARN = "\033[1;38;2;255;110;110m" if color else ""
    header_c = C_OK if feasible else C_WARN
    status = "Adjusted to Fit Budget" if feasible else "No Solution Possible Within Budget"
    print(f"\n{header_c}╭─ Budget Enforcement (--budget {budget}: {status}) ─╮{RESET}", file=file)
    for line in notes:
        print(f"  {line}", file=file)
    print(file=file)
