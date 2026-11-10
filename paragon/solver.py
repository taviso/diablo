"""
Exact Node-Weighted Steiner Tree solvers (graph reductions, C++ / Python Dreyfus-Wagner DP,
Z3 MaxSMT via /usr/bin/z3, and SciPy/HiGHS MILP) and global 2D board arrangement & rotation optimizer.
"""

import ctypes
import os
import re
import shutil
import subprocess
import sys
import time
from collections import deque
from functools import cache
from heapq import heapify, heappop, heappush
from itertools import combinations

from .board import ParagonBoard, grid_components, is_grid_connected, neighbors4
from .game_data import (
    DIR_DELTA,
    EXIT_COORDS,
    NODE_TYPE,
    OPPOSITE_EXIT,
    PARAGON_EXIT,
    STARTER_START_COORD,
)


def load_native_solver():
    """Load (or compile on demand when missing/outdated) the C++ Dreyfus-Wagner shared library."""
    base_dir = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
    so_path = os.path.join(base_dir, "libsteiner.so")
    cpp_path = os.path.join(base_dir, "libsteiner.cpp")

    needs_build = not os.path.exists(so_path)
    if not needs_build and os.path.exists(cpp_path):
        try:
            needs_build = os.path.getmtime(cpp_path) > os.path.getmtime(so_path)
        except OSError:
            pass

    if needs_build and os.path.exists(cpp_path):
        try:
            subprocess.run(
                ["g++", "-std=c++17", "-O3", "-shared", "-fPIC", cpp_path, "-o", so_path],
                check=True,
                capture_output=True,
            )
        except Exception:
            if not os.path.exists(so_path):
                return None

    if os.path.exists(so_path):
        try:
            lib = ctypes.CDLL(so_path)
            lib.solve_board_all_exits.argtypes = [
                ctypes.c_int,
                ctypes.POINTER(ctypes.c_double),
                ctypes.POINTER(ctypes.c_int),
                ctypes.POINTER(ctypes.c_int),
                ctypes.c_int,
                ctypes.POINTER(ctypes.c_int),
                ctypes.POINTER(ctypes.c_int),
                ctypes.c_int,
                ctypes.POINTER(ctypes.c_int),
                ctypes.c_int,
                ctypes.POINTER(ctypes.c_double),
                ctypes.POINTER(ctypes.c_uint8),
            ]
            lib.solve_board_all_exits.restype = ctypes.c_int
            return lib
        except Exception:
            return None
    return None


NATIVE_LIB = load_native_solver()

DEFAULT_Z3_TIMEOUT_SEC = 30


def find_z3_bin() -> str | None:
    """Locate the z3 CLI executable (e.g. /usr/bin/z3 or Z3_BIN env var) if installed."""
    env_bin = os.environ.get("Z3_BIN")
    if env_bin and os.path.isfile(env_bin) and os.access(env_bin, os.X_OK):
        return env_bin
    if os.path.isfile("/usr/bin/z3") and os.access("/usr/bin/z3", os.X_OK):
        return "/usr/bin/z3"
    return shutil.which("z3")


def _eval_smt2_z3(smt_text: str, dump_tag: str = "", timeout_sec: int | None = None) -> str:
    """
    Evaluate an SMT-LIB2 optimization script using `/usr/bin/z3 -T:<timeout> -in`.
    If `PARAGON_DUMP_SMT` is set in the environment, writes `<PARAGON_DUMP_SMT>_<dump_tag>.smt2`.
    """
    dump_prefix = os.environ.get("PARAGON_DUMP_SMT")
    if dump_prefix:
        out_path = f"{dump_prefix}_{dump_tag}.smt2" if dump_tag else dump_prefix
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(smt_text)

    z3_bin = find_z3_bin()
    if z3_bin is None:
        raise RuntimeError(
            "'/usr/bin/z3' is required for '--solver z3' (install via `sudo apt install z3`)."
        )

    eff_timeout = timeout_sec if timeout_sec is not None else int(os.environ.get("Z3_TIMEOUT", DEFAULT_Z3_TIMEOUT_SEC))
    proc = subprocess.run(
        [z3_bin, f"-T:{eff_timeout}", "-in"],
        input=smt_text,
        text=True,
        capture_output=True,
        timeout=eff_timeout + 5,
        check=False,
    )
    out = proc.stdout.strip()
    if out.startswith("timeout") or out.startswith("unknown"):
        raise RuntimeError(
            f"Z3 solver timed out after {eff_timeout}s ({dump_tag or 'query'})."
        )
    return out


def _reduce_graph_exact(
    board: ParagonBoard,
    alive_init: set[tuple[int, int]],
    terms_init: set[tuple[int, int]],
    protected: set[tuple[int, int]],
) -> tuple[set[tuple[int, int]], set[tuple[int, int]], list[set[tuple[int, int]]]]:
    """
    Apply optimality-preserving node-weighted Steiner tree graph reductions to `(alive_init, terms_init)`,
    keeping `protected` coordinates unpruned. Returns `(alive, terms, comps)`.
    """
    alive = set(alive_init)
    terms = set(terms_init)

    def get_nbs(u: tuple[int, int]) -> list[tuple[int, int]]:
        return [v for v in neighbors4(u) if v in alive]

    def get_comps() -> list[set[tuple[int, int]]]:
        return grid_components(terms & alive)

    while True:
        changed = False
        comps = get_comps()
        if len(comps) <= 1 and not protected:
            break
        comp_id = {u: idx for idx, c in enumerate(comps) for u in c}
        comp_nbs = [set() for _ in comps]
        vert_comps: dict[tuple[int, int], set[int]] = {}
        for idx, c in enumerate(comps):
            for u in c:
                for v in get_nbs(u):
                    if v not in c:
                        comp_nbs[idx].add(v)
                        vert_comps.setdefault(v, set()).add(idx)

        def reach(u: tuple[int, int]) -> set:
            rset = set()
            u_cs = vert_comps.get(u, ())
            for c_id in u_cs:
                rset.add(("C", c_id))
            for x in get_nbs(u):
                if x not in comp_id:
                    if not (u_cs and (u_cs & vert_comps.get(x, set()))):
                        rset.add(("V", x))
            return rset

        def endpoints_redundant(e1: tuple, e2: tuple, allow_direct_edge: bool = False) -> bool:
            if (e1[0] == "C" and e2[0] == "V" and e2[1] in comp_nbs[e1[1]]) or (
                e2[0] == "C" and e1[0] == "V" and e1[1] in comp_nbs[e2[1]]
            ):
                return True
            if e1[0] == "V" and e2[0] == "V":
                u1, u2 = e1[1], e2[1]
                if (allow_direct_edge and u2 in get_nbs(u1)) or any(
                    u1 in comp_nbs[c] and u2 in comp_nbs[c] for c in range(len(comps))
                ):
                    return True
            return False

        non_terms = sorted(alive - terms - protected)
        for v in non_terms:
            if v not in alive:
                continue
            rv = reach(v)
            if len(rv) <= 1:
                alive.remove(v)
                changed = True
                continue
            if len(rv) == 2:
                e1, e2 = tuple(rv)
                if endpoints_redundant(e1, e2):
                    alive.remove(v)
                    changed = True
                    continue

            wv = board.node_weight(v, terms)
            v_comps = {item[1] for item in rv if item[0] == "C"}
            v_verts = [item[1] for item in rv if item[0] == "V"]
            v_vert_comps = {
                u: {comp_id[nb] for nb in get_nbs(u) if nb in terms}
                for u in v_verts
            }
            cand_z = set()
            for c_id in v_comps:
                cand_z.update(comp_nbs[c_id])
            for u in v_verts:
                cand_z.add(u)
                for nb2 in get_nbs(u):
                    if nb2 not in terms:
                        cand_z.add(nb2)
                for c_id in v_vert_comps[u]:
                    cand_z.update(comp_nbs[c_id])
            cand_z.discard(v)
            for z in cand_z:
                if z not in alive:
                    continue
                wz = board.node_weight(z, terms)
                if wz <= wv:
                    rz = reach(z)
                    z_comps = {item[1] for item in rz if item[0] == "C"}
                    if not v_comps.issubset(z_comps):
                        continue
                    z_verts = {item[1] for item in rz if item[0] == "V"} | {z}
                    if all(u in z_verts or bool(v_vert_comps[u] & z_comps) for u in v_verts):
                        alive.remove(v)
                        changed = True
                        break

        if changed:
            continue

        # Maximal degree-2 non-terminal chain reductions (self-loops, 0-cost bypasses,
        # 1-node common-neighbor shortcuts, and 2-exit component cut dominance)
        non_terms_set = alive - terms - protected
        r_map = {u: reach(u) for u in non_terms_set}
        deg2 = {u for u, ru in r_map.items() if len(ru) == 2}

        def walk_deg2(curr_item: tuple, prev_item: tuple, seen_set: set):
            path = []
            while curr_item[0] == "V" and curr_item[1] in deg2 and curr_item[1] not in seen_set:
                cv = curr_item[1]
                seen_set.add(cv)
                path.append(cv)
                nxts = [x for x in r_map[cv] if x != prev_item]
                if not nxts:
                    break
                prev_item, curr_item = ("V", cv), nxts[0]
            return path, curr_item

        vis_d2 = set()
        for u in sorted(deg2):
            if u in vis_d2 or u not in alive:
                continue
            ru = list(r_map[u])
            if len(ru) != 2:
                continue

            vis_d2.add(u)
            p1, e1 = walk_deg2(ru[0], ("V", u), vis_d2)
            p2, e2 = walk_deg2(ru[1], ("V", u), vis_d2)
            chain = list(reversed(p1)) + [u] + p2
            chain_set = set(chain)
            if e1 == e2:
                alive.difference_update(chain)
                changed = True
                break
            if len(chain) >= 2:
                if endpoints_redundant(e1, e2, allow_direct_edge=True):
                    alive.difference_update(chain)
                    changed = True
                    break
                nbs1 = comp_nbs[e1[1]] if e1[0] == "C" else set(get_nbs(e1[1]))
                nbs2 = comp_nbs[e2[1]] if e2[0] == "C" else set(get_nbs(e2[1]))
                if (nbs1 & nbs2) - chain_set:
                    alive.difference_update(chain)
                    changed = True
                    break
                if e1[0] == "C" and e2[0] == "C" and len(comps) >= 3:
                    ca, cb = e1[1], e2[1]
                    if len(comp_nbs[ca]) == 2 and len(comp_nbs[cb]) == 2:
                        xa = next(iter(comp_nbs[ca] - chain_set))
                        xb = next(iter(comp_nbs[cb] - chain_set))
                        r_xa = {item for item in reach(xa) if item[0] == "C" and item[1] not in (ca, cb)}
                        r_xb = {item for item in reach(xb) if item[0] == "C" and item[1] not in (ca, cb)}
                        if xa == xb or (r_xa & r_xb):
                            alive.difference_update(chain)
                            changed = True
                            break

        if changed:
            continue
        if len(comps) > 1:
            for idx, nbs in enumerate(comp_nbs):
                if len(nbs) == 1:
                    terms.update(nbs)
                    changed = True
                elif len(nbs) == 2:
                    u1, u2 = sorted(nbs)
                    if u1 in deg2 and u2 in deg2:
                        p1, end1 = walk_deg2(("V", u1), ("C", idx), set())
                        p2, end2 = walk_deg2(("V", u2), ("C", idx), set())
                        if p1 and p2 and not (set(p1) & set(p2)):
                            w1 = sum(board.node_weight(x, terms) for x in p1)
                            w2 = sum(board.node_weight(x, terms) for x in p2)
                            if end1[0] == "C" and end1[1] != idx and w1 <= w2:
                                terms.update(p1)
                                changed = True
                                break
                            if end2[0] == "C" and end2[1] != idx and w2 <= w1:
                                terms.update(p2)
                                changed = True
                                break
        if not changed:
            break

    return alive, terms, get_comps()


def _solve_board_z3(
    board: ParagonBoard,
    alive_init: set[tuple[int, int]],
    base_terms: set[tuple[int, int]],
    weight_base_terms: set[tuple[int, int]],
    is_starter: bool,
    num_exits: int,
    exits_order: list[PARAGON_EXIT],
    eff_max_exits: int,
    is_valid_selection,
) -> dict[int, tuple[int, float, set[tuple[int, int]]]]:
    """
    Exact MaxSMT Node-Weighted Steiner Tree solver using `/usr/bin/z3 -T:<timeout> -in`.
    Uses lexicographic optimization (`:opt.priority lex`):
      1. Primary objective (`:id card`): minimize total selected non-terminal nodes.
      2. Secondary objective (`:id prio`): maximize total node priority among minimum-cardinality trees.
    """
    if find_z3_bin() is None:
        raise RuntimeError(
            "Could not find 'z3' binary ('/usr/bin/z3' or on PATH) for '--solver z3'."
        )

    num_emasks = 1 << num_exits
    results: dict[int, tuple[int, float, set[tuple[int, int]]]] = {}
    t0 = time.monotonic()
    shown_progress = False
    is_tty = hasattr(sys.stderr, "isatty") and sys.stderr.isatty()

    valid_emasks = [
        emask
        for emask in range(num_emasks)
        if is_starter or (0 < bin(emask).count("1") <= eff_max_exits)
    ]

    for step_idx, emask in enumerate(valid_emasks, 1):
        req_coords = set(base_terms)
        infeasible = False
        for e_idx in range(num_exits):
            if (emask >> e_idx) & 1:
                ec = EXIT_COORDS[exits_order[e_idx]]
                if ec not in alive_init:
                    infeasible = True
                    break
                req_coords.add(ec)
        if infeasible or not req_coords:
            continue

        if len(valid_emasks) > 1 and (time.monotonic() - t0) >= 2.0:
            shown_progress = True
            msg = f"Solving with Z3 on {board.board_name} ({board.board_type_id}) [exit mask {step_idx}/{len(valid_emasks)}]..."
            if is_tty:
                print(f"\r\033[K{msg}", end="", file=sys.stderr, flush=True)
            else:
                print(msg, file=sys.stderr, flush=True)

        alive, terms, comps = _reduce_graph_exact(board, alive_init, req_coords, protected=set())
        # Place the largest terminal component at index 0 as the arborescence root
        comps.sort(key=lambda c: (-len(c), min(c)))
        comp_of = {u: cid for cid, comp in enumerate(comps) for u in comp}
        k = len(comps)
        if k <= 1:
            sel_coords = set(terms)
            if is_valid_selection(sel_coords, emask):
                total_w = sum(board.node_weight(c, weight_base_terms) for c in sel_coords)
                results[emask] = (len(sel_coords), total_w, sel_coords)
            continue

        non_terms = sorted(alive - terms)
        M = len(non_terms)
        nt_idx = {v: k + i for i, v in enumerate(non_terms)}
        N = k + M
        s_adj: list[set[int]] = [set() for _ in range(N)]
        for cid, comp in enumerate(comps):
            for u in comp:
                for nb in neighbors4(u):
                    if nb not in alive or nb in comp:
                        continue
                    oid = comp_of[nb] if nb in comp_of else nt_idx[nb]
                    s_adj[cid].add(oid)
                    s_adj[oid].add(cid)
        for i, v in enumerate(non_terms):
            vid = k + i
            for nb in neighbors4(v):
                if nb in nt_idx:
                    oid = nt_idx[nb]
                    s_adj[vid].add(oid)
                    s_adj[oid].add(vid)

        # Verify all k terminal components are reachable from root 0 in s_adj
        bfs_dist = {0: 0}
        q = deque([0])
        while q:
            u = q.popleft()
            for nb in s_adj[u]:
                if nb not in bfs_dist:
                    bfs_dist[nb] = bfs_dist[u] + 1
                    q.append(nb)
        if any(c not in bfs_dist for c in range(k)):
            continue

        # 0-1 BFS helper on s_adj (terminal components < k cost 0, non-terminals >= k cost 1)
        def bfs01(sources) -> tuple[dict[int, int], dict[int, int | None]]:
            dist01 = {u: 0 for u in sources}
            prev: dict[int, int | None] = {u: None for u in sources}
            dq = deque(sources)
            while dq:
                u = dq.popleft()
                for nb in s_adj[u]:
                    w = 0 if nb < k else 1
                    nd = dist01[u] + w
                    if nb not in dist01 or nd < dist01[nb]:
                        dist01[nb] = nd
                        prev[nb] = u
                        if w == 0:
                            dq.appendleft(nb)
                        else:
                            dq.append(nb)
            return dist01, prev

        # Greedy shortest-path Steiner tree (Takahashi-Matsuyama) gives a feasible upper bound
        # `ub_nonterms` on the optimal non-terminal count. Any arborescence over at most
        # k + ub_nonterms contracted nodes has depth <= k - 1 + ub_nonterms, so this depth
        # bound (and the cardinality bound below) never cuts off an optimal tree.
        tree = {0}
        ub_nonterms = 0
        while any(c not in tree for c in range(k)):
            dist01, prev = bfs01(tree)
            hit = min((c for c in range(k) if c not in tree and c in dist01), key=lambda c: dist01[c], default=None)
            while hit is not None and hit not in tree:
                tree.add(hit)
                if hit >= k:
                    ub_nonterms += 1
                hit = prev[hit]
        max_d = min(N - 1, k - 1 + ub_nonterms)

        lines = [
            f"; Board: {board.board_name} ({board.board_type_id}), exit_mask={emask}, k={k} terminal components, M={M} non-terminals",
            f"; Root component 0: {sorted(comps[0])}",
            "(set-option :produce-models true)",
            "(set-option :opt.priority lex)",
        ]
        for i, v_coord in enumerate(non_terms):
            vid = k + i
            lines.append(f"(declare-const x_{vid} Bool) ; {v_coord} {board.grid_type[v_coord].name}")
        for v in range(1, N):
            lb = max(1, bfs_dist.get(v, 1))
            ub = max(lb, max_d)
            lines.append(f"(declare-const d_{v} Int)")
            lines.append(f"(assert (and (>= d_{v} {lb}) (<= d_{v} {ub})))")

        def d_expr(u: int) -> str:
            return "0" if u == 0 else f"d_{u}"

        def x_expr(u: int) -> str:
            return "true" if u < k else f"x_{u}"

        for c in range(1, k):
            ors = [f"(and {x_expr(u)} (> d_{c} {d_expr(u)}))" for u in sorted(s_adj[c])]
            if not ors:
                infeasible = True
                break
            lines.append(f"(assert (or {' '.join(ors)})) ; comp {c}: {sorted(comps[c])}")
        if infeasible:
            continue

        for v in range(k, N):
            p_ors = [f"(and {x_expr(u)} (> d_{v} {d_expr(u)}))" for u in sorted(s_adj[v])]
            c_ors = [f"(and {x_expr(u)} (> {d_expr(u)} d_{v}))" for u in sorted(s_adj[v]) if u != 0]
            lines.append(f"(assert (=> x_{v} (or {' '.join(p_ors)})))")
            if c_ors:
                lines.append(f"(assert (=> x_{v} (or {' '.join(c_ors)})))")
            lines.append(f"(assert-soft (not x_{v}) :weight 1 :id card)")
        if M > 0:
            lines.append(f"(assert ((_ at-most {ub_nonterms}) {' '.join(f'x_{v}' for v in range(k, N))}))")

        # Exact 0-1 BFS cutset layer clauses from each terminal component
        seen_layer_clauses: set[str] = set()
        for t_root in range(k):
            dist01, _ = bfs01((t_root,))
            max_td = max(dist01.get(c, 0) for c in range(k))
            for layer_d in range(1, max_td + 1):
                layer_vars = [f"x_{v}" for v in range(k, N) if dist01.get(v) == layer_d]
                if layer_vars:
                    clause = f"(assert (or {' '.join(layer_vars)}))"
                    if clause not in seen_layer_clauses:
                        seen_layer_clauses.add(clause)
                        lines.append(clause)

        for i, v_coord in enumerate(non_terms):
            vid = k + i
            p_int = int(round(board.node_priority(v_coord) * 2000))
            if p_int > 0:
                lines.append(f"(assert-soft x_{vid} :weight {p_int} :id prio)")

        lines.append("(check-sat)")
        if M > 0:
            vars_str = " ".join(f"x_{v}" for v in range(k, N))
            lines.append(f"(get-value ({vars_str}))")

        smt_text = "\n".join(lines) + "\n"
        out = _eval_smt2_z3(smt_text, dump_tag=f"{board.board_type_id}_emask{emask}")

        if not out.startswith("sat"):
            continue

        sel_coords = set(terms)
        for m in re.finditer(r"\(x_(\d+)\s+true\)", out):
            vid = int(m.group(1))
            sel_coords.add(non_terms[vid - k])

        if not is_valid_selection(sel_coords, emask):
            continue
        total_w = sum(board.node_weight(c, weight_base_terms) for c in sel_coords)
        results[emask] = (len(sel_coords), total_w, sel_coords)

    if shown_progress and is_tty:
        print("\r\033[K", end="", file=sys.stderr, flush=True)
    return results


def _solve_board_milp(
    board: ParagonBoard,
    verts: list[tuple[int, int]],
    vert_idx: dict[tuple[int, int], int],
    adj: list[list[int]],
    terms: set[tuple[int, int]],
    is_starter: bool,
    num_exits: int,
    exit_v_indices: list[int],
    eff_max_exits: int,
    is_valid_selection,
) -> dict[int, tuple[int, float, set[tuple[int, int]]]]:
    """
    Exact polynomial-size single-commodity flow MILP solver via SciPy/HiGHS.
    Used automatically when a board has too many disconnected terminal clusters
    for exponential bitmask DP (`total_bits > 15`), or when `--solver milp` is selected.
    """
    import numpy as np
    from scipy.optimize import Bounds, LinearConstraint, milp
    from scipy.sparse import csc_matrix

    V = len(verts)
    num_emasks = 1 << num_exits
    base_req = {vert_idx[u] for u in terms if u in vert_idx}
    results = {}

    for emask in range(num_emasks):
        if not is_starter and (emask == 0 or bin(emask).count("1") > eff_max_exits):
            continue
        req = set(base_req)
        infeasible_exit = False
        for e_idx in range(num_exits):
            if (emask >> e_idx) & 1:
                ev = exit_v_indices[e_idx]
                if ev < 0:
                    infeasible_exit = True
                    break
                req.add(ev)
        if infeasible_exit or not req:
            continue

        # Connected components of required vertices in the reduced graph
        vis = set()
        req_comps = []
        for rv in sorted(req):
            if rv in vis:
                continue
            comp = set()
            q = deque([rv])
            vis.add(rv)
            while q:
                u = q.popleft()
                comp.add(u)
                for nb in adj[u]:
                    if nb in req and nb not in vis:
                        vis.add(nb)
                        q.append(nb)
            req_comps.append(comp)

        K = len(req_comps)
        if K <= 1:
            sel_coords = {verts[i] for i in req}
            if is_valid_selection(sel_coords, emask):
                total_w = sum(board.node_weight(c, board.target_nodes) for c in sel_coords)
                results[emask] = (len(sel_coords), total_w, sel_coords)
            continue

        # Map each vertex in `verts` to contracted node ID:
        #   0 .. K-1 : required terminal components (0 = flow source, 1..K-1 = unit sinks)
        #   K .. K+N_non-1 : non-required candidate vertices
        node_map = {}
        for c_id, comp in enumerate(req_comps):
            for u in comp:
                node_map[u] = c_id
        non_req = [i for i in range(V) if i not in req]
        for idx, u in enumerate(non_req):
            node_map[u] = K + idx

        N_non = len(non_req)
        N_nodes = K + N_non
        T_flow = float(K - 1)

        # Build directed arcs between contracted nodes (no arcs entering source 0)
        arcs_set = set()
        for u in range(V):
            cu = node_map[u]
            for v in adj[u]:
                cv = node_map[v]
                if cu != cv and cv != 0:
                    arcs_set.add((cu, cv))
        arcs = sorted(arcs_set)
        num_arcs = len(arcs)
        if num_arcs == 0:
            continue

        # Variables: [x_0 .. x_{N_non-1}, f_0 .. f_{num_arcs-1}]
        num_vars = N_non + num_arcs
        c_obj = np.zeros(num_vars, dtype=float)
        for idx, u in enumerate(non_req):
            c_obj[idx] = board.node_weight(verts[u], terms)

        integrality = np.zeros(num_vars, dtype=int)
        integrality[:N_non] = 1
        lb = np.zeros(num_vars, dtype=float)
        ub = np.empty(num_vars, dtype=float)
        ub[:N_non] = 1.0
        ub[N_non:] = T_flow

        # Constraints:
        # 1) Rows 0 .. N_nodes-2: flow conservation at contracted nodes 1 .. N_nodes-1
        #    sum_{(u, v)} f_{uv} - sum_{(v, w)} f_{vw} == (1.0 if v < K else 0.0)
        # 2) Rows N_nodes-1 .. N_nodes-1+N_non-1: inflow capacity at non-required nodes v = K+idx
        #    sum_{(u, v)} f_{uv} - T_flow * x_idx <= 0
        num_rows = (N_nodes - 1) + N_non
        rows = []
        cols = []
        vals = []

        for a_idx, (cu, cv) in enumerate(arcs):
            f_col = N_non + a_idx
            # Inflow to cv (cv >= 1 always since cv != 0)
            rows.append(cv - 1)
            cols.append(f_col)
            vals.append(1.0)
            # Outflow from cu (if cu >= 1)
            if cu >= 1:
                rows.append(cu - 1)
                cols.append(f_col)
                vals.append(-1.0)
            # Capacity constraint if cv >= K
            if cv >= K:
                cap_row = (N_nodes - 1) + (cv - K)
                rows.append(cap_row)
                cols.append(f_col)
                vals.append(1.0)

        for idx in range(N_non):
            cap_row = (N_nodes - 1) + idx
            rows.append(cap_row)
            cols.append(idx)
            vals.append(-T_flow)

        A_mat = csc_matrix((vals, (rows, cols)), shape=(num_rows, num_vars))
        b_l = np.zeros(num_rows, dtype=float)
        b_u = np.zeros(num_rows, dtype=float)
        # Sinks 1 .. K-1 have net inflow == 1.0
        b_l[: K - 1] = 1.0
        b_u[: K - 1] = 1.0
        # Capacity rows have lower bound -inf and upper bound 0.0
        b_l[N_nodes - 1 :] = -np.inf

        res = milp(
            c=c_obj,
            integrality=integrality,
            bounds=Bounds(lb, ub),
            constraints=LinearConstraint(A_mat, b_l, b_u),
            options={"mip_rel_gap": 0.0},
        )
        if not res.success or res.x is None:
            continue

        sel_indices = set(req)
        for idx, u in enumerate(non_req):
            if res.x[idx] > 0.5:
                sel_indices.add(u)
        sel_coords = {verts[i] for i in sel_indices}
        if not is_valid_selection(sel_coords, emask):
            continue
        total_w = sum(board.node_weight(c, board.target_nodes) for c in sel_coords)
        results[emask] = (len(sel_coords), total_w, sel_coords)

    return results


def solve_board_exact(
    board: ParagonBoard,
    is_starter: bool,
    max_exits: int,
    solver: str = "auto",
    fixed_terminals: set[tuple[int, int]] | None = None,
) -> dict[int, tuple[int, float, set[tuple[int, int]]]]:
    """
    Compute exact minimum Node-Weighted Steiner Trees for `board`.
    Returns a dict mapping `exit_mask` -> (total_node_count, weighted_cost, selected_coords_set).
    If `fixed_terminals` is provided, solves for that exact terminal set with 0 exit bits (emask=0).
    """
    alive = {coord for coord, nt in board.grid_type.items() if nt != NODE_TYPE.DEAD}
    if fixed_terminals is not None:
        base_terms = set(fixed_terminals)
        is_starter = True
        max_exits = 0
    else:
        base_terms = set(board.target_nodes)
        if is_starter:
            if STARTER_START_COORD not in alive:
                raise ValueError(
                    f"Starter board '{board.board_type_id}' has DEAD start node at {STARTER_START_COORD}."
                )
            base_terms.add(STARTER_START_COORD)
            if max_exits > 0:
                if EXIT_COORDS[PARAGON_EXIT.TOP] not in alive:
                    raise ValueError(
                        f"Starter board '{board.board_type_id}' has DEAD TOP exit at {EXIT_COORDS[PARAGON_EXIT.TOP]}."
                    )
                base_terms.add(EXIT_COORDS[PARAGON_EXIT.TOP])

    weight_base_terms = board.target_nodes if fixed_terminals is None else base_terms
    exits_order = [PARAGON_EXIT.TOP, PARAGON_EXIT.RIGHT, PARAGON_EXIT.BOTTOM, PARAGON_EXIT.LEFT]
    protected_exits = set() if is_starter else (set(EXIT_COORDS.values()) | {STARTER_START_COORD})

    alive, terms, comps = _reduce_graph_exact(board, alive, base_terms, protected=protected_exits)

    def get_nbs(u: tuple[int, int]) -> list[tuple[int, int]]:
        return [v for v in neighbors4(u) if v in alive]

    # Dominated-boundary component elimination: if every neighbor of terminal component C_a is also a
    # neighbor of C_b (N(C_a) <= N(C_b)), any tree connecting C_a to another terminal/exit already touches C_b,
    # so C_b can be kept as 0-weight vertices without consuming a DP bit.
    passive_terms: set[tuple[int, int]] = set()
    if len(comps) >= 2:
        c_nbs = [{v for u in c for v in get_nbs(u) if v not in c} for c in comps]
        drop_c = set()
        for b_idx in range(len(comps)):
            for a_idx in range(len(comps)):
                if a_idx == b_idx or a_idx in drop_c or not c_nbs[a_idx]:
                    continue
                if c_nbs[a_idx].issubset(c_nbs[b_idx]):
                    drop_c.add(b_idx)
                    passive_terms.update(comps[b_idx])
                    break
        if drop_c:
            comps = [c for idx, c in enumerate(comps) if idx not in drop_c]

    verts = sorted(alive)
    vert_idx = {u: i for i, u in enumerate(verts)}
    V = len(verts)
    k_base = len(comps)
    weights = [0.0 if u in terms else board.node_weight(u, terms) for u in verts]
    adj = [[vert_idx[nb] for nb in get_nbs(u)] for u in verts]

    num_exits = 0 if is_starter else 4
    exit_v_indices = [0] if is_starter else [vert_idx.get(EXIT_COORDS[e], -1) for e in exits_order]
    eff_max_exits = 0 if is_starter else max_exits
    num_emasks = 1 << num_exits
    INF = 1e18

    b_bits = max(0, k_base - 1)
    total_bits = b_bits + num_exits

    def is_valid_selection(sel_coords: set[tuple[int, int]], emask: int) -> bool:
        if not base_terms.issubset(sel_coords):
            return False
        for e_idx in range(num_exits):
            if (emask >> e_idx) & 1 and EXIT_COORDS[exits_order[e_idx]] not in sel_coords:
                return False
        return is_grid_connected(sel_coords)

    if solver == "z3":
        return _solve_board_z3(
            board,
            alive,
            terms,
            weight_base_terms,
            is_starter,
            num_exits,
            exits_order,
            eff_max_exits,
            is_valid_selection,
        )

    max_dw_bits = 17 if NATIVE_LIB is not None else 12
    if solver == "milp" or (solver == "auto" and total_bits > 16):
        try:
            return _solve_board_milp(
                board,
                verts,
                vert_idx,
                adj,
                terms,
                is_starter,
                num_exits,
                exit_v_indices,
                eff_max_exits,
                is_valid_selection,
            )
        except ImportError:
            if solver == "milp":
                raise RuntimeError("SciPy is required for '--solver milp' but is not installed.")
            if total_bits > max_dw_bits and find_z3_bin() is not None:
                return _solve_board_z3(
                    board,
                    alive,
                    terms,
                    weight_base_terms,
                    is_starter,
                    num_exits,
                    exits_order,
                    eff_max_exits,
                    is_valid_selection,
                )

    if total_bits > max_dw_bits:
        raise RuntimeError(
            f"Board '{board.board_type_id}' has too many disconnected target clusters "
            f"({k_base} components / {total_bits} bits after reduction) for "
            f"{'native C++' if NATIVE_LIB is not None else 'pure-Python'} Dreyfus-Wagner DP; "
            f"install 'scipy' (--solver milp) or 'z3' (--solver z3)."
        )

    if NATIVE_LIB is not None:
        w_arr = (ctypes.c_double * V)(*weights)
        deg_list = [len(a) for a in adj]
        adj_flat = [x for a in adj for x in a]
        deg_arr = (ctypes.c_int * V)(*deg_list)
        adj_arr = (ctypes.c_int * len(adj_flat))(*adj_flat)
        comp_sizes = (ctypes.c_int * k_base)(*[len(c) for c in comps])
        comp_flat = [vert_idx[u] for c in comps for u in sorted(c)]
        comp_arr = (ctypes.c_int * len(comp_flat))(*comp_flat)
        exit_arr = (ctypes.c_int * len(exit_v_indices))(*exit_v_indices)
        out_costs = (ctypes.c_double * num_emasks)()
        out_sel = (ctypes.c_uint8 * (num_emasks * V))()

        status = NATIVE_LIB.solve_board_all_exits(
            V, w_arr, deg_arr, adj_arr,
            k_base, comp_sizes, comp_arr,
            num_exits, exit_arr, eff_max_exits,
            out_costs, out_sel,
        )
        if status != 0:
            raise RuntimeError(
                f"Native Steiner solver failed on board '{board.board_type_id}' (status={status})."
            )

        results = {}
        for emask in range(num_emasks):
            if not is_starter and (emask == 0 or bin(emask).count("1") > eff_max_exits):
                continue
            if out_costs[emask] >= INF / 2:
                continue
            sel_coords = {verts[i] for i in range(V) if out_sel[emask * V + i]} | passive_terms
            if not is_valid_selection(sel_coords, emask):
                continue
            total_w = sum(board.node_weight(c, weight_base_terms) for c in sel_coords)
            results[emask] = (len(sel_coords), total_w, sel_coords)
        return results

    # Pure-Python Dreyfus-Wagner fallback if native library is unavailable
    num_masks = 1 << total_bits
    full_base_mask = (1 << b_bits) - 1
    dp = [[INF] * V for _ in range(num_masks)]
    parent = [[None] * V for _ in range(num_masks)]

    for c_idx in range(b_bits):
        m = 1 << c_idx
        for u_coord in comps[c_idx]:
            dp[m][vert_idx[u_coord]] = 0.0
    for e_idx in range(num_exits):
        u = exit_v_indices[e_idx]
        if u >= 0:
            dp[1 << (b_bits + e_idx)][u] = 0.0

    for mask in range(1, num_masks):
        if bin(mask >> b_bits).count("1") > eff_max_exits:
            continue
        dp_mask = dp[mask]
        par_mask = parent[mask]
        lowbit = mask & -mask
        sub = (mask - 1) & mask
        while sub > 0:
            if sub & lowbit:
                other = mask ^ sub
                dp_sub = dp[sub]
                dp_other = dp[other]
                for v in range(V):
                    cand = dp_sub[v] + dp_other[v]
                    if cand < dp_mask[v]:
                        dp_mask[v] = cand
                        par_mask[v] = ("MERGE", sub, other)
            sub = (sub - 1) & mask

        pq = [(dp_mask[v], v) for v in range(V) if dp_mask[v] < INF / 2]
        heapify(pq)
        while pq:
            d, u = heappop(pq)
            if d > dp_mask[u]:
                continue
            cost_via_u = d + weights[u]
            for nb in adj[u]:
                if cost_via_u < dp_mask[nb]:
                    dp_mask[nb] = cost_via_u
                    par_mask[nb] = ("STEP", u)
                    heappush(pq, (cost_via_u, nb))

    results = {}
    root_comp = [vert_idx[u] for u in sorted(comps[k_base - 1])] if k_base > 0 else []
    for emask in range(num_emasks):
        if not is_starter and (emask == 0 or bin(emask).count("1") > eff_max_exits):
            continue
        full_mask = full_base_mask | (emask << b_bits)
        best_cost = INF
        best_root = -1
        if k_base > 0:
            if full_mask == 0:
                best_cost = 0.0
                best_root = root_comp[0]
            else:
                for u in root_comp:
                    if dp[full_mask][u] < best_cost:
                        best_cost = dp[full_mask][u]
                        best_root = u
        elif emask > 0:
            first_e = (emask & -emask).bit_length() - 1
            rem_mask = full_mask ^ (1 << first_e)
            u = exit_v_indices[first_e]
            if u >= 0:
                if rem_mask == 0:
                    best_cost = weights[u]
                    best_root = u
                elif dp[rem_mask][u] < INF / 2:
                    best_cost = dp[rem_mask][u] + weights[u]
                    best_root = u
                full_mask = rem_mask

        if best_root < 0 or best_cost >= INF / 2:
            continue

        sel_coords = set(terms)
        for e_idx in range(num_exits):
            if (emask >> e_idx) & 1 and exit_v_indices[e_idx] >= 0:
                sel_coords.add(verts[exit_v_indices[e_idx]])
        if full_mask > 0:
            stack = [(full_mask, best_root)]
            while stack:
                m, v = stack.pop()
                sel_coords.add(verts[v])
                info = parent[m][v]
                if info is None:
                    continue
                if info[0] == "STEP":
                    stack.append((m, info[1]))
                elif info[0] == "MERGE":
                    stack.append((info[1], v))
                    stack.append((info[2], v))
        if not is_valid_selection(sel_coords, emask):
            continue
        total_w = sum(board.node_weight(c, weight_base_terms) for c in sel_coords)
        results[emask] = (len(sel_coords), total_w, sel_coords)
    return results


def _starter_layout_entry(starter: ParagonBoard, max_exits: int, solver: str) -> tuple[int, float, set[tuple[int, int]]]:
    """Solve (or reuse the cached) starter board tree for `max_exits` (TOP gate required iff `max_exits > 0`)."""
    key = (frozenset(starter.target_nodes), solver, max_exits > 0)
    if key not in starter.exit_solutions:
        res = solve_board_exact(starter, is_starter=True, max_exits=max_exits, solver=solver)
        if 0 not in res:
            raise ValueError(f"Starter board '{starter.board_type_id}' targets/exits are disconnected or infeasible.")
        starter.exit_solutions[key] = res[0]
    return starter.exit_solutions[key]


def _board_exit_solutions(b: ParagonBoard, solver: str) -> dict[int, tuple[int, float, set[tuple[int, int]]]]:
    """Solve (or reuse the cached) all-exit-mask Steiner trees for non-starter board `b` (up to 4 active exits)."""
    key = (frozenset(b.target_nodes), solver)
    if key not in b.exit_solutions:
        b.exit_solutions[key] = solve_board_exact(b, is_starter=False, max_exits=4, solver=solver)
    return b.exit_solutions[key]


def _search_layout(
    starter: ParagonBoard,
    starter_sel: set[tuple[int, int]],
    non_starter_boards: list[ParagonBoard],
    board_solutions: list[dict[int, tuple[int, float, set[tuple[int, int]]]]],
    max_exits: int,
):
    """
    Exhaustively search all valid non-overlapping 2D board trees rooted at the starter's TOP gate.
    Returns `(best_global_key, best_plan)` where `best_plan` is a list of `(b_idx, x, y, rot, umask)`.
    Does not mutate any board.
    """
    M = len(non_starter_boards)

    # For each non-starter board i and each screen-space exit mask `smask` in 1..15,
    # find the best rotation `rot` in {0, 1, 2, 3}.
    # Recall: unrotated exit `e` rotated clockwise by `rot` goes to screen exit `(e + rot) % 4`.
    # Thus screen exit `s` comes from unrotated exit `(s - rot) % 4`.
    # Tie-breaker on rotation matches d4-paragon's canonical rotation preference.
    best_for_screen_mask: list[dict[int, tuple[int, float, int, int]]] = []
    for i in range(M):
        sol = board_solutions[i]
        b = non_starter_boards[i]
        s_map = {}
        for smask in range(1, 16):
            if bin(smask).count("1") > max_exits:
                continue
            best_cand = None
            for rot in range(4):
                umask = 0
                for s_dir in range(4):
                    if (smask >> s_dir) & 1:
                        u_dir = (s_dir - rot) % 4
                        umask |= 1 << u_dir
                if umask not in sol:
                    continue
                cnt, _, sel_coords = sol[umask]
                prio_sum = sum(b.node_priority(c) for c in sel_coords if c not in b.target_nodes)
                cand = (cnt, -round(prio_sum, 9), rot, umask)
                if best_cand is None or cand < best_cand:
                    best_cand = cand
            if best_cand is not None:
                s_map[smask] = best_cand
        best_for_screen_mask.append(s_map)

    # Search all valid non-overlapping 2D grid trees rooted at (0, -1) (attached to starter TOP exit)
    # Canonical BFS expansion order: TOP (0), RIGHT (1), LEFT (3), BOTTOM (2)
    canonical_dir_order = [
        PARAGON_EXIT.TOP,
        PARAGON_EXIT.RIGHT,
        PARAGON_EXIT.LEFT,
        PARAGON_EXIT.BOTTOM,
    ]

    best_global_key = None
    best_plan = None

    def search_trees(
        used_mask: int,
        queue: list[tuple[int, int, int]],
        occupied: set[tuple[int, int]],
        placements: list[tuple[int, int, int, int, int]],
        acc_nodes: int,
        acc_neg_prio: float,
    ):
        nonlocal best_global_key, best_plan

        placed_count = len(placements)
        if placed_count == M:
            # Prefer canonical shapes (higher degree boards placed earlier) when costs are tied
            deg_tiebreak = tuple(-bin(p[4]).count("1") for p in placements)
            key = (acc_nodes, round(acc_neg_prio, 9), deg_tiebreak)
            if best_global_key is None or key < best_global_key:
                best_global_key = key
                best_plan = list(placements)
            return

        if not queue:
            return

        # Prune if even minimum possible remaining nodes exceeds best_global_key
        if best_global_key is not None and acc_nodes > best_global_key[0]:
            return

        x, y, entry_dir = queue[0]
        rest_queue = queue[1:]
        boards_left_after = M - placed_count - 1
        max_new_children = boards_left_after - len(rest_queue)
        min_new_children = 1 if (boards_left_after > 0 and len(rest_queue) == 0) else 0

        # Candidate outgoing child screen directions from (x, y)
        avail_dirs = [d for d in canonical_dir_order if d != entry_dir]

        # Generate subsets of avail_dirs of size k in [min_new_children .. min(len(avail_dirs), max_new_children)]
        for k_ch in range(min_new_children, min(len(avail_dirs), max_new_children) + 1):
            for child_dirs in combinations(avail_dirs, k_ch):
                new_positions = []
                collision = False
                for d in child_dirs:
                    dx, dy = DIR_DELTA[d]
                    np = (x + dx, y + dy)
                    if np in occupied:
                        collision = True
                        break
                    new_positions.append((np[0], np[1], int(OPPOSITE_EXIT[d])))
                if collision:
                    continue

                smask = 1 << entry_dir
                for d in child_dirs:
                    smask |= 1 << int(d)

                new_occupied = occupied | {(np[0], np[1]) for np in new_positions}
                new_queue = rest_queue + new_positions

                for b_idx in range(M):
                    if (used_mask >> b_idx) & 1:
                        continue
                    if smask not in best_for_screen_mask[b_idx]:
                        continue
                    cnt, neg_prio, rot, umask = best_for_screen_mask[b_idx][smask]
                    placements.append((b_idx, x, y, rot, umask))
                    search_trees(
                        used_mask | (1 << b_idx),
                        new_queue,
                        new_occupied,
                        placements,
                        acc_nodes + cnt,
                        acc_neg_prio + neg_prio,
                    )
                    placements.pop()

    starter_neg_prio = -sum(
        starter.node_priority(c) for c in starter_sel if c not in starter.target_nodes
    )
    initial_occupied = {(0, 0), (0, -1)}
    initial_queue = [(0, -1, int(PARAGON_EXIT.BOTTOM))]
    search_trees(0, initial_queue, initial_occupied, [], len(starter_sel), starter_neg_prio)
    return best_global_key, best_plan


def _solve_glyph_threshold_repair_impl(
    board: ParagonBoard,
    solver: str = "auto",
    max_solves: int = 500,
) -> tuple[set[tuple[int, int]], int, frozenset[tuple[int, int]]] | None:
    """
    Internal implementation of exact Glyph threshold repair returning
    `(selected_nodes, lower_bound_count, chosen_candidates)`.
    """
    if not board.glyph or board.glyph_coord is None or not board.glyph_stat or board.glyph_threshold <= 0:
        return None
    stat, thresh = board.glyph_stat, board.glyph_threshold
    if board.glyph_stat_in_radius(board.selected_nodes) >= thresh:
        return (set(board.selected_nodes), len(board.selected_nodes), frozenset())

    active_exit_coords = board.active_exit_coords()
    base = board.required_terminals()

    cands = [
        c for c, nt in board.grid_type.items()
        if nt != NODE_TYPE.DEAD and c not in base and c not in EXIT_COORDS.values()
        and board.in_glyph_radius(c) and board.node_stat_grant_for(c, stat) > 0
    ]
    cands_set = set(cands)
    if board.glyph_stat_in_radius(base | cands_set) < thresh:
        return None
    # Larger stat grants and higher priorities first so a strong incumbent is found early
    cands.sort(key=lambda c: (-board.node_stat_grant_for(c, stat), -board.node_priority(c), c))

    @cache
    def solve_extra(extra: frozenset[tuple[int, int]]) -> tuple[int, float, set[tuple[int, int]]] | None:
        return solve_board_exact(
            board,
            is_starter=True,
            max_exits=0,
            solver=solver,
            fixed_terminals=base | extra,
        ).get(0)

    def prio_key(sel: set[tuple[int, int]]) -> float:
        return -round(sum(board.node_priority(c) for c in sel), 9)

    def cand_key(
        sol: tuple[int, float, set[tuple[int, int]]],
        st: int,
        chosen: frozenset[tuple[int, int]],
    ) -> tuple[int, int, float, float]:
        # Among equal-minimum-point solutions meeting `thresh`, prefer trees that do not pull in
        # unselected zero-stat MAGIC/RARE cluster nodes unless the Glyph scales with them.
        zero_stat_pen = (
            0
            if board.glyph_mode in ("magic", "rare")
            else sum(
                1
                for c in (sol[2] - base)
                if board.grid_type[c] in (NODE_TYPE.MAGIC, NODE_TYPE.RARE) and not board.node_stat_grants(c)
            )
        )
        pk = prio_key(sol[2])
        if board.glyph_mode in ("stat", "all"):
            return (sol[0], zero_stat_pen, -float(st), pk)
        return (sol[0], zero_stat_pen, pk, -float(st))

    # Precompute 1-candidate trees for tight monotonic lower bounds `max(parent_cnt, s1[c][0])`
    s1: dict[tuple[int, int], tuple[int, float, set[tuple[int, int]]]] = {}
    best: tuple[tuple[int, int, float, float], frozenset[tuple[int, int]], tuple[int, float, set[tuple[int, int]]]] | None = None
    best_partial: tuple[int, frozenset[tuple[int, int]], tuple[int, float, set[tuple[int, int]]]] = (
        board.glyph_stat_in_radius(board.selected_nodes),
        frozenset(),
        (len(board.selected_nodes), 0.0, set(board.selected_nodes)),
    )

    solves = 0
    for c in cands:
        if c in board.selected_nodes:
            continue
        sol_c = solve_extra(frozenset({c}))
        solves += 1
        if sol_c is not None:
            s1[c] = sol_c
            st_c = board.glyph_stat_in_radius(sol_c[2])
            if st_c >= thresh:
                ck = cand_key(sol_c, st_c, frozenset({c}))
                if best is None or ck < best[0]:
                    best = (ck, frozenset({c}), sol_c)
            elif st_c > best_partial[0]:
                best_partial = (st_c, frozenset({c}), sol_c)

    root = (len(board.selected_nodes), 0.0, set(board.selected_nodes))
    root_stat = board.glyph_stat_in_radius(root[2])
    tick = 0
    # Heap entries: (bound_cnt, is_lazy, -stat_or_est, prio_key, tick, chosen, excl, sol_or_None)
    heap = [(root[0], 0, -root_stat, prio_key(root[2]), tick, frozenset(), frozenset(), root)]
    complete = True
    lower_bound = root[0]
    tie_solves_at_opt = 0

    while heap:
        cnt, is_lazy, neg_stat, p_key, _, chosen, excl, sol = heappop(heap)
        lower_bound = cnt
        if best is not None and cnt > best[0][0]:
            return (best[2][2], best[0][0], best[1])

        if is_lazy:
            if best is not None and cnt == best[0][0]:
                if tie_solves_at_opt >= 24:
                    return (best[2][2], best[0][0], best[1])
                tie_solves_at_opt += 1
            if solves >= max_solves:
                complete = False
                break
            sol = solve_extra(chosen)
            solves += 1
            if sol is None:
                continue
            st = board.glyph_stat_in_radius(sol[2])
            pk = prio_key(sol[2])
            if st >= thresh:
                ck = cand_key(sol, st, chosen)
                if best is None or ck < best[0]:
                    best = (ck, chosen, sol)
            elif st > best_partial[0]:
                best_partial = (st, chosen, sol)
            if best is not None and sol[0] >= best[0][0]:
                continue
            tick += 1
            heappush(heap, (sol[0], 0, -st, pk, tick, chosen, excl, sol))
            continue

        cur_excl = set(excl)
        for c in cands:
            if c in sol[2] or c in cur_excl:
                continue
            if board.glyph_stat_in_radius(base | (cands_set - cur_excl)) < thresh:
                break
            nxt = chosen | {c}
            if not chosen and c in s1:
                child = s1[c]
                st_c = board.glyph_stat_in_radius(child[2])
                if best is None or child[0] <= best[0][0]:
                    tick += 1
                    heappush(heap, (child[0], 0, -st_c, prio_key(child[2]), tick, nxt, frozenset(cur_excl), child))
            else:
                lb = max(cnt, s1[c][0]) if c in s1 else cnt
                if best is None or lb <= best[0][0]:
                    tick += 1
                    est_neg_stat = neg_stat - board.node_stat_grant_for(c, stat)
                    heappush(heap, (lb, 1, est_neg_stat, 0.0, tick, nxt, frozenset(cur_excl), None))
            cur_excl.add(c)

    if best is None:
        # Search limit reached before hitting threshold: complete greedily from best partial tree
        _, chosen_p, sol_p = best_partial
        greedy_chosen = set(chosen_p)
        cur_sel = set(sol_p[2])
        for c in cands:
            if board.glyph_stat_in_radius(base | greedy_chosen | cur_sel) >= thresh:
                break
            if c not in cur_sel:
                greedy_chosen.add(c)
                cur_sel.add(c)
        g_froz = frozenset(greedy_chosen)
        g_sol = solve_extra(g_froz)
        if g_sol is not None and board.glyph_stat_in_radius(g_sol[2]) >= thresh:
            g_st = board.glyph_stat_in_radius(g_sol[2])
            best = (cand_key(g_sol, g_st, g_froz), g_froz, g_sol)

    if best is None:
        return None
    return (best[2][2], best[0][0] if complete else min(best[0][0], lower_bound), best[1])


def solve_glyph_threshold_repair(
    board: ParagonBoard,
    solver: str = "auto",
    max_solves: int = 500,
) -> tuple[set[tuple[int, int]], int] | None:
    """
    Find the minimum-point selection on `board` (with its active exit gates fixed) whose
    governed Core Stat inside the Glyph radius meets `board.glyph_threshold`.
    Returns `(selected_nodes, lower_bound_count)`, or `None` if the threshold is unreachable.
    `selected_nodes` is a proven minimum when `len(selected_nodes) == lower_bound_count`.
    """
    res = _solve_glyph_threshold_repair_impl(board, solver=solver, max_solves=max_solves)
    if res is None:
        return None
    return (res[0], res[1])


def _needs_glyph_bonus_repair(b: ParagonBoard, sel: set[tuple[int, int]]) -> bool:
    return bool(
        b.auto_glyph_bonus
        and b.glyph
        and b.glyph_coord is not None
        and b.glyph_stat
        and b.glyph_threshold > 0
        and b.glyph_stat_in_radius(sel) < b.glyph_threshold
    )


def _repaired_board_solution(
    b: ParagonBoard,
    umask: int,
    unrep_sol: tuple[int, float, set[tuple[int, int]]],
    solver: str,
) -> tuple[int, float, set[tuple[int, int]], frozenset[tuple[int, int]]]:
    """
    Return `(cnt, total_w, selected_nodes, bonus_targets)` for board `b` at exit mask `umask`
    with `--auto-glyph-bonus` applied, caching repaired trees on `b.exit_solutions`.
    """
    if not _needs_glyph_bonus_repair(b, unrep_sol[2]):
        return (unrep_sol[0], unrep_sol[1], unrep_sol[2], frozenset())

    cache_key = (frozenset(b.target_nodes), solver, "repaired", umask)
    if cache_key not in b.exit_solutions:
        prev_sel = b.selected_nodes
        try:
            b.selected_nodes = unrep_sol[2]
            repair = _solve_glyph_threshold_repair_impl(b, solver=solver)
        finally:
            b.selected_nodes = prev_sel
        if repair is not None:
            new_sel, _, chosen = repair
            w_base = set(b.target_nodes) | {EXIT_COORDS[d] for d in PARAGON_EXIT if (umask >> int(d)) & 1}
            if b.is_starter_board():
                w_base.add(STARTER_START_COORD)
            new_w = sum(b.node_weight(c, w_base) for c in new_sel)
            b.exit_solutions[cache_key] = (len(new_sel), new_w, new_sel, frozenset(chosen))
        else:
            b.exit_solutions[cache_key] = (unrep_sol[0], unrep_sol[1], unrep_sol[2], frozenset())
    return b.exit_solutions[cache_key]


def _search_layout_with_bonus(
    starter: ParagonBoard,
    starter_sel: set[tuple[int, int]],
    non_starter_boards: list[ParagonBoard],
    board_solutions: list[dict[int, tuple[int, float, set[tuple[int, int]]]]],
    max_exits: int,
    solver: str,
):
    """
    Jointly optimize the 2D board layout, rotations, active exit masks, and `--auto-glyph-bonus`
    threshold repairs using lazy lower-bound refinement.
    Since unrepaired `(node_count, -priority)` is a strict lexicographic lower bound on repaired
    `(node_count, -priority)`, iteratively repairing only the winning plan's unrepaired `(board, umask)`
    entries is provably globally optimal while avoiding repairing non-competitive high-degree exit masks.
    """
    M = len(non_starter_boards)
    working_sols = [dict(sol) for sol in board_solutions]
    bonus_by_mask: list[dict[int, frozenset[tuple[int, int]]]] = [{} for _ in range(M)]
    repaired_masks: list[set[int]] = [set() for _ in range(M)]

    for b_idx, b in enumerate(non_starter_boards):
        for umask, sol in working_sols[b_idx].items():
            if not _needs_glyph_bonus_repair(b, sol[2]):
                repaired_masks[b_idx].add(umask)
                bonus_by_mask[b_idx][umask] = frozenset()

    while True:
        best_key, best_plan = _search_layout(starter, starter_sel, non_starter_boards, working_sols, max_exits)
        if best_plan is None:
            return None, None, working_sols, bonus_by_mask
        any_unrepaired = False
        for b_idx, _, _, _, umask in best_plan:
            if umask not in repaired_masks[b_idx]:
                any_unrepaired = True
                b = non_starter_boards[b_idx]
                rep_cnt, rep_w, rep_sel, rep_chosen = _repaired_board_solution(
                    b, umask, working_sols[b_idx][umask], solver
                )
                working_sols[b_idx][umask] = (rep_cnt, rep_w, rep_sel)
                bonus_by_mask[b_idx][umask] = rep_chosen
                repaired_masks[b_idx].add(umask)
        if not any_unrepaired:
            return best_key, best_plan, working_sols, bonus_by_mask


def _compute_layout_plan(
    boards: list[ParagonBoard],
    solver: str = "auto",
):
    """
    Solve the starter board and jointly search the globally optimal 2D layout + Glyph bonus repairs
    across `boards` (assuming `auto_glyph_bonus_nodes` have been stripped from `target_nodes`).
    Returns `(starter, starter_sel, starter_chosen, non_starter_boards, best_plan, working_sols, bonus_by_mask)`.
    """
    starter_boards = [b for b in boards if b.is_starter_board()]
    non_starter_boards = [b for b in boards if not b.is_starter_board()]
    if len(starter_boards) != 1:
        raise ValueError("Input must contain exactly one starter board (board ID ending in '00').")

    starter = starter_boards[0]
    M = len(non_starter_boards)
    max_exits = min(4, M) if M > 0 else 0

    starter_unrep = _starter_layout_entry(starter, max_exits, solver)
    starter_emask = (1 << int(PARAGON_EXIT.TOP)) if max_exits > 0 else 0
    _, _, starter_sel, starter_chosen = _repaired_board_solution(starter, starter_emask, starter_unrep, solver)
    if M == 0:
        return starter, starter_sel, starter_chosen, non_starter_boards, [], [], []

    board_solutions = [_board_exit_solutions(b, solver) for b in non_starter_boards]
    _, best_plan, working_sols, bonus_by_mask = _search_layout_with_bonus(
        starter, starter_sel, non_starter_boards, board_solutions, max_exits, solver
    )
    return starter, starter_sel, starter_chosen, non_starter_boards, best_plan, working_sols, bonus_by_mask


def optimize_layout_and_paths(
    boards: list[ParagonBoard],
    solver: str = "auto",
) -> list[ParagonBoard]:
    """
    Globally optimize intra-board Steiner trees, exit gate selections,
    board rotations, and 2D non-overlapping grid positions.
    Per-board exit-mask solutions are cached on each board (`exit_solutions`) for reuse by `layout_node_count`.
    """
    if not boards:
        return []

    for b in boards:
        if b.auto_glyph_bonus_nodes:
            b.target_nodes -= b.auto_glyph_bonus_nodes
            b.auto_glyph_bonus_nodes.clear()

    starter, starter_sel, starter_chosen, non_starter_boards, best_plan, working_sols, bonus_by_mask = (
        _compute_layout_plan(boards, solver=solver)
    )
    starter.selected_nodes = starter_sel
    if starter_chosen:
        bonus_targets = set(starter_chosen) - starter.target_nodes
        starter.auto_glyph_bonus_nodes = bonus_targets
        starter.target_nodes |= bonus_targets
    starter.position = {"x": 0, "y": 0}
    starter.rotation = 0
    starter.entry_gate_coord = STARTER_START_COORD

    if not non_starter_boards:
        return [starter]
    if best_plan is None:
        raise RuntimeError("Failed to find a valid non-overlapping 2D board arrangement.")

    ordered_boards = [starter]
    placed_pos: dict[tuple[int, int], ParagonBoard] = {(0, 0): starter}
    for b_idx, x, y, rot, umask in best_plan:
        b = non_starter_boards[b_idx]
        b.position = {"x": x, "y": y}
        b.rotation = rot
        b.selected_nodes = working_sols[b_idx][umask][2]
        b.entry_gate_coord = None
        for s_dir, (dx, dy) in DIR_DELTA.items():
            parent = placed_pos.get((x + dx, y + dy))
            if parent is not None and OPPOSITE_EXIT[s_dir].name in parent.screen_active_exits():
                sr, sc = EXIT_COORDS[s_dir]
                cand = b.screen_to_unrotated(sr, sc)
                if cand in b.selected_nodes:
                    b.entry_gate_coord = cand
                    break
        placed_pos[(x, y)] = b
        chosen = bonus_by_mask[b_idx].get(umask, frozenset())
        if chosen:
            bonus_targets = set(chosen) - b.target_nodes
            b.auto_glyph_bonus_nodes = bonus_targets
            b.target_nodes |= bonus_targets
        ordered_boards.append(b)

    return ordered_boards


def solve_layout_without(
    boards: list[ParagonBoard],
    exclude: set[int] | None = None,
    solver: str = "auto",
) -> tuple[int, dict[int, set[tuple[int, int]]]] | None:
    """
    Exactly re-solve the globally optimal layout of `boards` with the boards whose `id()` is in
    `exclude` removed (layout, exits, and rotations fully re-solved).
    Returns `(total_node_count, {id(board): selected_nodes})`, or `None` if no valid arrangement exists.
    Reuses per-board exit-mask solutions cached by `optimize_layout_and_paths`; does not mutate boards.
    """
    exclude = exclude or set()
    kept = [b for b in boards if id(b) not in exclude]
    if not any(b.is_starter_board() for b in kept):
        return (0, {})

    saved_state = [(b, set(b.target_nodes), set(b.selected_nodes)) for b in kept]
    try:
        for b in kept:
            if b.auto_glyph_bonus_nodes:
                b.target_nodes = b.target_nodes - b.auto_glyph_bonus_nodes

        starter, starter_sel, _, non_starter_boards, best_plan, working_sols, _ = _compute_layout_plan(
            kept, solver=solver
        )
        if non_starter_boards and best_plan is None:
            return None
        selections = {id(starter): starter_sel}
        for b_idx, _, _, _, umask in (best_plan or []):
            selections[id(non_starter_boards[b_idx])] = working_sols[b_idx][umask][2]
        return (sum(len(s) for s in selections.values()), selections)
    finally:
        for b, orig_targets, orig_sel in saved_state:
            b.target_nodes = orig_targets
            b.selected_nodes = orig_sel
