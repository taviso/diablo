// Dedicated Web Worker (and Node-testable TypedArray Dreyfus-Wagner solver)
// Powers the Diablo 4 Paragon Optimizer Web Application via Pyodide + TypedArray DP.

function popcount32(x) {
  x = x - ((x >>> 1) & 0x55555555);
  x = (x & 0x33333333) + ((x >>> 2) & 0x33333333);
  return (((x + (x >>> 4)) & 0x0f0f0f0f) * 0x01010101) >>> 24;
}

function ctz32(x) {
  if (x === 0) return 32;
  return 31 - Math.clz32(x & -x);
}

function toTypedArray(input, Ctor) {
  if (input instanceof Ctor) return input;
  if (input && typeof input.toJs === "function") {
    const jsVal = input.toJs();
    if (typeof input.destroy === "function") input.destroy();
    return Ctor.from(jsVal);
  }
  return Ctor.from(input);
}

/**
 * Exact Node-Weighted Steiner Tree solver using Dreyfus-Wagner dynamic programming.
 * Direct flat-TypedArray port of libsteiner.cpp::solve_board_all_exits.
 */
function solveBoardAllExitsTypedArray(
  V,
  wIn,
  degIn,
  adjFlatIn,
  kBase,
  compSizesIn,
  compVertsFlatIn,
  numExits,
  exitVertsIn,
  maxActiveExits
) {
  V = V | 0;
  kBase = kBase | 0;
  numExits = numExits | 0;
  maxActiveExits = maxActiveExits | 0;

  if (V <= 0 || kBase < 0 || numExits < 0 || numExits > 4) {
    return null;
  }

  const bBits = kBase > 0 ? kBase - 1 : 0;
  const totalBits = bBits + numExits;
  if (totalBits > 20) {
    return null;
  }

  const w = toTypedArray(wIn, Float64Array);
  const deg = toTypedArray(degIn, Int32Array);
  const adjFlat = toTypedArray(adjFlatIn, Int32Array);
  const compSizes = toTypedArray(compSizesIn, Int32Array);
  const compVertsFlat = toTypedArray(compVertsFlatIn, Int32Array);
  const exitVerts = toTypedArray(exitVertsIn, Int32Array);

  const adjOffsets = new Int32Array(V + 1);
  for (let i = 0; i < V; i++) {
    adjOffsets[i + 1] = adjOffsets[i] + deg[i];
  }

  const compOffsets = new Int32Array(kBase + 1);
  const compOf = new Int32Array(V);
  compOf.fill(-1);
  for (let c = 0; c < kBase; c++) {
    const start = compOffsets[c];
    const end = start + compSizes[c];
    compOffsets[c + 1] = end;
    for (let idx = start; idx < end; idx++) {
      compOf[compVertsFlat[idx]] = c;
    }
  }

  const isExitOrig = new Uint8Array(V);
  for (let e = 0; e < numExits; e++) {
    const u = exitVerts[e];
    if (u >= 0 && u < V) isExitOrig[u] = 1;
  }

  const mergeList = [];
  for (let u = 0; u < V; u++) {
    if (compOf[u] >= 0 || isExitOrig[u] || deg[u] >= 3) {
      mergeList.push(u);
    }
  }
  const mergeVerts = Int32Array.from(mergeList);
  const nMerge = mergeVerts.length;

  const numMasks = 1 << totalBits;
  const fullBaseMask = (1 << bBits) - 1;
  const INF = 1e18;
  const totalStates = numMasks * V;

  const dp = new Float64Array(totalStates);
  dp.fill(INF);
  const pType = new Uint8Array(totalStates);
  const pArg1 = new Int32Array(totalStates);
  pArg1.fill(-1);

  for (let c = 0; c < bBits; c++) {
    const mOffset = (1 << c) * V;
    const start = compOffsets[c];
    const end = compOffsets[c + 1];
    for (let idx = start; idx < end; idx++) {
      dp[mOffset + compVertsFlat[idx]] = 0.0;
    }
  }
  for (let e = 0; e < numExits; e++) {
    const u = exitVerts[e];
    if (u >= 0 && u < V) {
      dp[(1 << (bBits + e)) * V + u] = 0.0;
    }
  }

  // Pre-allocated flat binary min-heap for Dijkstra transitions
  const maxHeapCap = V * 8 + 32;
  const heapD = new Float64Array(maxHeapCap);
  const heapU = new Int32Array(maxHeapCap);
  const heapPush = (pos, dVal, uVal) => {
    while (pos > 0) {
      const parent = (pos - 1) >> 1;
      if (heapD[parent] <= dVal) break;
      heapD[pos] = heapD[parent];
      heapU[pos] = heapU[parent];
      pos = parent;
    }
    heapD[pos] = dVal;
    heapU[pos] = uVal;
  };

  for (let mask = 1; mask < numMasks; mask++) {
    if (popcount32(mask >>> bBits) > maxActiveExits) continue;
    const mOffset = mask * V;
    const lowbit = mask & -mask;

    for (let sub = (mask - 1) & mask; sub > 0; sub = (sub - 1) & mask) {
      if ((sub & lowbit) !== 0) {
        const other = mask ^ sub;
        const subOffset = sub * V;
        const otherOffset = other * V;
        for (let i = 0; i < nMerge; i++) {
          const v = mergeVerts[i];
          const cand = dp[subOffset + v] + dp[otherOffset + v];
          if (cand < dp[mOffset + v]) {
            dp[mOffset + v] = cand;
            pType[mOffset + v] = 2;
            pArg1[mOffset + v] = sub;
          }
        }
      }
    }

    let heapSize = 0;
    const halfInf = INF * 0.5;
    for (let v = 0; v < V; v++) {
      const d0 = dp[mOffset + v];
      if (d0 < halfInf) {
        heapPush(heapSize++, d0, v);
      }
    }

    while (heapSize > 0) {
      const d = heapD[0];
      const u = heapU[0];
      heapSize--;
      if (heapSize > 0) {
        const lastD = heapD[heapSize];
        const lastU = heapU[heapSize];
        let pos = 0;
        const half = heapSize >> 1;
        while (pos < half) {
          let child = (pos << 1) + 1;
          const right = child + 1;
          if (right < heapSize && heapD[right] < heapD[child]) {
            child = right;
          }
          if (lastD <= heapD[child]) break;
          heapD[pos] = heapD[child];
          heapU[pos] = heapU[child];
          pos = child;
        }
        heapD[pos] = lastD;
        heapU[pos] = lastU;
      }

      if (d > dp[mOffset + u]) continue;
      const costViaU = d + w[u];
      const start = adjOffsets[u];
      const end = adjOffsets[u + 1];
      for (let idx = start; idx < end; idx++) {
        const nb = adjFlat[idx];
        if (costViaU < dp[mOffset + nb]) {
          dp[mOffset + nb] = costViaU;
          pType[mOffset + nb] = 1;
          pArg1[mOffset + nb] = u;
          heapPush(heapSize++, costViaU, nb);
        }
      }
    }
  }

  const numExitMasks = 1 << numExits;
  const outCosts = new Float64Array(numExitMasks);
  outCosts.fill(INF);
  const outSelected = new Uint8Array(numExitMasks * V);

  for (let emask = 0; emask < numExitMasks; emask++) {
    if (popcount32(emask) > maxActiveExits) continue;

    let fullMask = fullBaseMask | (emask << bBits);
    let bestCost = INF;
    let bestRoot = -1;

    if (kBase > 0) {
      const lastStart = compOffsets[kBase - 1];
      const lastEnd = compOffsets[kBase];
      if (fullMask === 0) {
        bestCost = 0.0;
        bestRoot = compVertsFlat[lastStart];
      } else {
        const fmOffset = fullMask * V;
        for (let idx = lastStart; idx < lastEnd; idx++) {
          const u = compVertsFlat[idx];
          const c = dp[fmOffset + u];
          if (c < bestCost) {
            bestCost = c;
            bestRoot = u;
          }
        }
      }
    } else if (emask > 0) {
      const firstE = ctz32(emask);
      const remMask = fullMask ^ (1 << firstE);
      const u = exitVerts[firstE];
      if (u >= 0 && u < V) {
        if (remMask === 0) {
          bestCost = w[u];
          bestRoot = u;
        } else if (dp[remMask * V + u] < INF * 0.5) {
          bestCost = dp[remMask * V + u] + w[u];
          bestRoot = u;
        }
        fullMask = remMask;
      }
    }

    if (bestRoot < 0 || bestCost >= INF * 0.5) continue;
    outCosts[emask] = bestCost;

    const selOffset = emask * V;
    for (let idx = 0; idx < compOffsets[kBase]; idx++) {
      outSelected[selOffset + compVertsFlat[idx]] = 1;
    }
    for (let e = 0; e < numExits; e++) {
      if ((emask >>> e) & 1) {
        const u = exitVerts[e];
        if (u >= 0 && u < V) outSelected[selOffset + u] = 1;
      }
    }
    if (fullMask > 0) {
      const stackM = [fullMask];
      const stackV = [bestRoot];
      while (stackM.length > 0) {
        const m = stackM.pop();
        const v = stackV.pop();
        outSelected[selOffset + v] = 1;
        const idx = m * V + v;
        const pt = pType[idx];
        if (pt === 1) {
          stackM.push(m);
          stackV.push(pArg1[idx]);
        } else if (pt === 2) {
          const sub = pArg1[idx];
          stackM.push(sub, m ^ sub);
          stackV.push(v, v);
        }
      }
    }
  }

  return [Array.from(outCosts), Array.from(outSelected)];
}

// Export for Node.js unit testing when not in a browser Worker
if (typeof module !== "undefined" && module.exports) {
  module.exports = { solveBoardAllExitsTypedArray };
}

// Browser Web Worker initialization & message loop
if (typeof self !== "undefined" && typeof module === "undefined") {
  let pyodideReadyPromise = null;
  let pyodideInstance = null;

  const PYTHON_BRIDGE_CODE = `
import copy
import json
import time
from collections import Counter

import paragon
from paragon import (
    BOARD_NAMES,
    BOARD_SPECIAL_NODES,
    EXIT_COORDS,
    GLYPH_DATA,
    MAX_PARAGON_POINTS,
    NODE_TYPE,
    PARAGON_EXIT,
    STARTER_START_COORD,
    ParagonBoard,
    enforce_budget,
    get_glyph_radius,
    is_grid_connected,
    load_meta_data,
    optimize_layout_and_paths,
    parse_glyph_strategy,
    register_external_solver,
    run_optimization_hints,
    unwrap_all_paragon_configs,
)

_META = load_meta_data()

def _board_core_stats(b, nodes_set=None):
    totals = Counter()
    for coord in b.purchased_nodes(nodes_set):
        for st, val in b.node_stat_grants(coord).items():
            totals[st] += val
    return totals

def _serialize_board_state(b, idx, orig_board=None, board_hints=None):
    free_raw = b.free_coords()
    orig_sel_raw = orig_board.selected_nodes if orig_board is not None else set(b.input_nodes)
    orig_free_raw = orig_board.free_coords(orig_sel_raw) if orig_board is not None else b.free_coords(orig_sel_raw)
    orig_purchased_count = len(orig_sel_raw - orig_free_raw)
    purchased_count = len(b.purchased_nodes())

    g_name = b.glyph_name if b.glyph else None
    g_stat = b.glyph_stat if b.glyph else None
    g_radius = b.glyph_radius
    g_stat_val = b.glyph_stat_in_radius() if b.glyph else 0

    cells = []
    for r in range(21):
        for c in range(21):
            coord = (r, c)
            nt = b.grid_type.get(coord, NODE_TYPE.DEAD)
            if nt == NODE_TYPE.DEAD:
                continue
            sr, sc = b.unrotated_to_screen(r, c)
            sx, sy = sc, sr
            raw_idx = r * 21 + c
            grant = b.node_stat_grants(coord)
            cells.append({
                "sx": sx,
                "sy": sy,
                "raw_r": r,
                "raw_c": c,
                "raw_idx": raw_idx,
                "type": nt.name,
                "label": b.node_label(coord),
                "stat_grant": dict(grant) if grant else {},
                "in_glyph_radius": b.in_glyph_radius(coord),
                "feeds_glyph": b.feeds_glyph(coord),
                "is_start": b.is_starter_board() and coord == STARTER_START_COORD,
                "is_free": coord in free_raw,
                "is_target": coord in b.target_nodes,
                "is_relaxed": coord in b.relaxed_nodes,
                "is_auto_glyph": coord in b.auto_glyph_bonus_nodes,
                "is_selected": coord in b.selected_nodes,
                "was_selected": coord in orig_sel_raw,
            })

    active_exits = []
    for e_dir, e_coord in EXIT_COORDS.items():
        if e_coord in b.selected_nodes:
            sr, sc = b.unrotated_to_screen(e_coord[0], e_coord[1])
            active_exits.append({"dir": e_dir.name, "sx": sc, "sy": sr})

    g_screen = None
    if b.glyph_coord is not None:
        gsr, gsc = b.unrotated_to_screen(b.glyph_coord[0], b.glyph_coord[1])
        g_screen = [gsc, gsr]

    pos = b.position or {"x": 0, "y": 0}
    return {
        "index": idx,
        "id": b.board_type_id,
        "display_name": b.board_name,
        "class_name": b.d4_class.name if b.d4_class else "Unknown",
        "x": pos.get("x", 0),
        "y": pos.get("y", 0),
        "rotation": b.rotation,
        "rotation_deg": b.rotation * 90,
        "is_starter": b.is_starter_board(),
        "glyph_id": b.glyph,
        "glyph_name": g_name,
        "glyph_level": b.glyph_level,
        "glyph_radius": g_radius,
        "glyph_stat_name": g_stat,
        "glyph_stat_val": g_stat_val,
        "glyph_threshold": b.glyph_threshold,
        "glyph_met": (g_stat_val >= b.glyph_threshold) if b.glyph_threshold > 0 else True,
        "glyph_strategy": b.glyph_strategy,
        "glyph_screen": g_screen,
        "purchased_count": purchased_count,
        "original_purchased_count": orig_purchased_count,
        "target_count": len(b.target_nodes),
        "core_stats": dict(_board_core_stats(b)),
        "active_exits": active_exits,
        "cells": cells,
        "hints": [
            {
                "category": h.category,
                "message": h.message,
                "points_delta": h.points_delta,
                "screen_coords": [list(pt) for pt in h.screen_coords],
            }
            for h in (board_hints or [])
        ],
    }

def web_run_pipeline(raw_json_str, options_json_str):
    opts = json.loads(options_json_str)
    data = json.loads(raw_json_str)
    configs = unwrap_all_paragon_configs(data)
    prof_idx = min(max(0, int(opts.get("profile_index", 0))), len(configs) - 1)
    prof_label, raw_boards = configs[prof_idx]

    prio_stats = [s.strip().upper() for s in opts.get("prioritize_stats", []) if s.strip()]
    relax_nodes = bool(opts.get("relax_path_nodes", False))
    strategy_str = opts.get("glyph_strategy", "preserve") or "preserve"
    input_glyphs = {e.get("glyph", "") for e in raw_boards if isinstance(e, dict) and e.get("glyph")}
    strategy_spec = parse_glyph_strategy(strategy_str, extra_glyph_names=input_glyphs)
    budget_val = int(opts.get("budget", MAX_PARAGON_POINTS))
    enable_hints = bool(opts.get("enable_hints", True))
    preview_only = bool(opts.get("preview_only", False))

    # Parse baseline boards (default preserve, no relax)
    orig_boards = [ParagonBoard(copy.deepcopy(entry), _META) for entry in raw_boards]
    orig_boards = [ob for ob in orig_boards if ob.target_nodes or ob.relaxed_nodes or ob.is_starter_board()]
    for ob in orig_boards:
        ob.selected_nodes = set(ob.input_nodes)
        if ob.is_starter_board():
            ob.selected_nodes.add(STARTER_START_COORD)

    input_is_connected = all(
        is_grid_connected(ob.selected_nodes) and ob.target_nodes.issubset(ob.selected_nodes)
        for ob in orig_boards
    )
    if not input_is_connected and not preview_only:
        orig_boards = optimize_layout_and_paths(orig_boards, solver="auto")

    orig_points = sum(len(ob.purchased_nodes()) for ob in orig_boards)
    orig_stats = Counter()
    for ob in orig_boards:
        orig_stats.update(_board_core_stats(ob))

    if preview_only:
        boards = orig_boards
        solve_ms = 0.0
        hints_ms = 0.0
        budget_info = None
        all_hints = []
    else:
        t0 = time.perf_counter()
        boards = []
        for entry in raw_boards:
            b = ParagonBoard(
                copy.deepcopy(entry),
                _META,
                prioritize_stats=prio_stats if prio_stats else None,
                relax_path_nodes=relax_nodes,
                glyph_strategy=strategy_spec,
            )
            for u_idx in entry.get("user_targets", []):
                coord = (int(u_idx) // b.size, int(u_idx) % b.size)
                if b.grid_type.get(coord, NODE_TYPE.DEAD) != NODE_TYPE.DEAD:
                    b.target_nodes.add(coord)
                    b.relaxed_nodes.discard(coord)
            boards.append(b)
        boards = [b for b in boards if b.target_nodes or b.relaxed_nodes or b.is_starter_board()]
        boards = optimize_layout_and_paths(boards, solver="auto")
        initial_opt_pts = sum(len(b.purchased_nodes()) for b in boards)
        budget_info = None
        if budget_val > 0 and initial_opt_pts > budget_val:
            boards, b_ok, b_notes = enforce_budget(boards, budget=budget_val, solver="auto")
            budget_info = {
                "budget": budget_val,
                "initial_points": initial_opt_pts,
                "final_points": sum(len(b.purchased_nodes()) for b in boards),
                "feasible": b_ok,
                "notes": b_notes,
            }
        t1 = time.perf_counter()
        solve_ms = (t1 - t0) * 1000.0

        all_hints = []
        t2 = time.perf_counter()
        if enable_hints:
            active_boards = [b for b in boards if b.selected_nodes]
            if active_boards:
                all_hints = run_optimization_hints(active_boards, solver="auto")
        t3 = time.perf_counter()
        hints_ms = (t3 - t2) * 1000.0

    opt_points = sum(len(b.purchased_nodes()) for b in boards)
    total_targets = sum(len(b.target_nodes) for b in boards)
    opt_stats = Counter()
    for b in boards:
        opt_stats.update(_board_core_stats(b))

    stat_keys = ["DEX", "INT", "STR", "WILL"]
    stat_deltas = {k: opt_stats.get(k, 0) - orig_stats.get(k, 0) for k in stat_keys}

    hints_by_board = {}
    for h in all_hints:
        hints_by_board.setdefault(id(h.board), []).append(h)

    orig_by_id = {ob.board_type_id: ob for ob in orig_boards}
    serialized_boards = [
        _serialize_board_state(
            b,
            i + 1,
            orig_board=orig_by_id.get(b.board_type_id),
            board_hints=hints_by_board.get(id(b), []),
        )
        for i, b in enumerate(boards)
    ]

    out_json = [b.export_data() for b in boards if b.selected_nodes]
    return json.dumps({
        "profile_label": prof_label or f"Profile #{prof_idx + 1}",
        "profiles": [
            {"index": i, "label": lbl or f"Profile #{i + 1}", "board_count": len(bl)}
            for i, (lbl, bl) in enumerate(configs)
        ],
        "class_name": serialized_boards[0]["class_name"] if serialized_boards else "Unknown",
        "input_is_connected": input_is_connected,
        "total_targets": total_targets,
        "original_points": orig_points,
        "optimized_points": opt_points,
        "points_saved": orig_points - opt_points,
        "solve_ms": round(solve_ms, 1),
        "hints_ms": round(hints_ms, 1),
        "original_stats": {k: orig_stats.get(k, 0) for k in stat_keys},
        "optimized_stats": {k: opt_stats.get(k, 0) for k in stat_keys},
        "stat_deltas": stat_deltas,
        "budget_info": budget_info,
        "boards": serialized_boards,
        "raw_boards": raw_boards,
        "output_json": out_json,
        "preview_only": preview_only,
    })
`;

  async function ensurePyodideLoaded() {
    if (pyodideInstance) return pyodideInstance;
    if (pyodideReadyPromise) return pyodideReadyPromise;

    pyodideReadyPromise = (async () => {
      self.postMessage({ type: "status", status: "loading", detail: "Loading optimizer runtime..." });
      const pyodideModule = await import("https://cdn.jsdelivr.net/pyodide/v0.27.3/full/pyodide.mjs");
      const pyodide = await pyodideModule.loadPyodide({
        indexURL: "https://cdn.jsdelivr.net/pyodide/v0.27.3/full/",
      });

      self.postMessage({ type: "status", status: "loading", detail: "Loading Paragon board data..." });
      const binStr = atob(self.PARAGON_ZIP_B64);
      const bytes = new Uint8Array(binStr.length);
      for (let i = 0; i < binStr.length; i++) {
        bytes[i] = binStr.charCodeAt(i);
      }
      pyodide.unpackArchive(bytes.buffer, "zip", { extractDir: "/home/pyodide" });

      await pyodide.runPythonAsync(PYTHON_BRIDGE_CODE);
      const registerFn = pyodide.globals.get("register_external_solver");
      registerFn((V, w, deg, adjFlat, kBase, compSizes, compFlat, numExits, exitVerts, maxExits) => {
        const res = solveBoardAllExitsTypedArray(
          V, w, deg, adjFlat, kBase, compSizes, compFlat, numExits, exitVerts, maxExits
        );
        return res ? pyodide.toPy(res) : null;
      });
      registerFn.destroy();

      pyodideInstance = pyodide;
      self.postMessage({ type: "status", status: "ready", detail: "Ready" });
      return pyodide;
    })();

    return pyodideReadyPromise;
  }

  self.addEventListener("message", async (ev) => {
    const { id, cmd, payload } = ev.data || {};
    try {
      const pyodide = await ensurePyodideLoaded();
      if (cmd === "ping") {
        self.postMessage({ id, ok: true, result: "pong" });
        return;
      }
      if (cmd === "run_pipeline") {
        const fn = pyodide.globals.get("web_run_pipeline");
        const resStr = fn(payload.rawJson, JSON.stringify(payload.options || {}));
        fn.destroy();
        self.postMessage({ id, ok: true, result: JSON.parse(resStr) });
        return;
      }
      throw new Error(`Unknown worker command: ${cmd}`);
    } catch (err) {
      self.postMessage({
        id,
        ok: false,
        error: err && err.message ? err.message : String(err),
      });
    }
  });
}
