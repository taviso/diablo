#!/usr/bin/env python3
"""
Verification and regression test suite for the Diablo 4 Paragon Optimizer and Hints Engine.
Run via `make check` or `python3 -m unittest discover -s tests`.
"""

import copy
import glob
import json
import random
import sys
import unittest
from collections import Counter, deque

from paragon import (
    DIR_DELTA,
    EXIT_COORDS,
    GLYPH_DATA,
    MAX_PARAGON_POINTS,
    NODE_TYPE,
    OPPOSITE_EXIT,
    PARAGON_EXIT,
    STARTER_START_COORD,
    HintContext,
    ParagonBoard,
    enforce_budget,
    find_z3_bin,
    format_layout_map_unicode,
    is_grid_connected,
    load_meta_data,
    neighbors4,
    optimize_layout_and_paths,
    parse_glyph_strategy,
    run_optimization_hints,
    solve_board_exact,
    solve_glyph_threshold_repair,
    solve_layout_without,
    unwrap_all_paragon_configs,
    unwrap_paragon_boards,
)

try:
    import scipy.optimize  # noqa: F401
    HAS_SCIPY = True
except ImportError:
    HAS_SCIPY = False


class TestParagonOptimizer(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.meta = load_meta_data()
        with open("tests/eternal_rogue_sample.json", "r", encoding="utf-8") as f:
            cls.sample_raw = json.load(f)
        cls.boards = [ParagonBoard(entry, cls.meta) for entry in cls.sample_raw]
        cls.boards = optimize_layout_and_paths(cls.boards, solver="auto")
        cls.hints = run_optimization_hints(cls.boards, solver="auto")

    def test_sample_node_counts_and_target_preservation(self):
        """Verify exact 246-node optimum, per-board node counts, and 100% target preservation."""
        counts = [len(b.selected_nodes) for b in self.boards]
        self.assertEqual(counts, [48, 72, 69, 45, 12])
        self.assertEqual(sum(counts), 246)

        total_targets = sum(len(b.target_nodes) for b in self.boards)
        self.assertEqual(total_targets, 146)

        for b in self.boards:
            self.assertTrue(
                b.target_nodes.issubset(b.selected_nodes),
                f"Board {b.board_type_id} missing target nodes: {b.target_nodes - b.selected_nodes}",
            )
            if b.is_starter_board():
                self.assertIn(STARTER_START_COORD, b.selected_nodes)

    def _assert_valid_optimized_build(self, boards: list[ParagonBoard], label: str = ""):
        """Verify target preservation, intra-board grid connectivity, and 2D inter-board tree connectivity."""
        self.assertGreater(len(boards), 0, f"Empty board list ({label})")
        for b in boards:
            self.assertTrue(
                b.target_nodes.issubset(b.selected_nodes),
                f"[{label}] Board {b.board_type_id} missing target nodes: {b.target_nodes - b.selected_nodes}",
            )
            if b.is_starter_board():
                self.assertIn(STARTER_START_COORD, b.selected_nodes)

            sel = b.selected_nodes
            self.assertGreater(len(sel), 0)
            self.assertTrue(
                is_grid_connected(sel),
                f"[{label}] Selected nodes on {b.board_type_id} are disconnected.",
            )

        pos_map = {(b.position["x"], b.position["y"]): b for b in boards}
        self.assertEqual(len(pos_map), len(boards), f"[{label}] Overlapping 2D board positions.")
        self.assertIn((0, 0), pos_map)
        self.assertTrue(pos_map[(0, 0)].is_starter_board())

        board_vis = {(0, 0)}
        bq = deque([(0, 0)])
        while bq:
            x, y = bq.popleft()
            b = pos_map[(x, y)]
            active_dirs = set(b.screen_active_exits())
            for s_dir in PARAGON_EXIT:
                if s_dir.name not in active_dirs:
                    continue
                dx, dy = DIR_DELTA[s_dir]
                nx, ny = x + dx, y + dy
                if (nx, ny) in pos_map:
                    nb_board = pos_map[(nx, ny)]
                    opp_name = OPPOSITE_EXIT[s_dir].name
                    self.assertIn(
                        opp_name,
                        nb_board.screen_active_exits(),
                        f"[{label}] Board at {(x, y)} exits {s_dir.name} to {(nx, ny)}, but neighbor lacks {opp_name} exit.",
                    )
                    if (nx, ny) not in board_vis:
                        board_vis.add((nx, ny))
                        bq.append((nx, ny))
        self.assertEqual(len(board_vis), len(boards), f"[{label}] 2D board layout is not connected.")

    def test_intra_board_and_inter_board_connectivity(self):
        """Verify every board's selected_nodes is a connected grid subgraph and boards form a valid 2D tree."""
        self._assert_valid_optimized_build(self.boards, label="eternal_rogue_sample.json")

    def test_synthetic_3way_steiner_junction(self):
        """Verify exact Steiner solver chooses a 1-node 3-way junction over a 2-node pairwise path."""
        # On Paragon_Rogue_00, (6, 11) is adjacent to (6, 10), (5, 11), and (6, 12)
        entry = {
            "id": "Paragon_Rogue_00",
            "nodes": {},
            "rotation": 0,
            "position": {"x": 0, "y": 0},
        }
        b = ParagonBoard(entry, self.meta)
        terms = {(6, 10), (5, 11), (6, 12)}
        res_dw = solve_board_exact(b, is_starter=True, max_exits=0, solver="dw", fixed_terminals=terms)
        cnt, _, sel = res_dw[0]
        # 3 terminals + 1 Steiner junction at (6, 11) = 4 nodes total
        self.assertEqual(cnt, 4)
        self.assertEqual(sel, {(6, 10), (5, 11), (6, 12), (6, 11)})
        if find_z3_bin() is not None:
            res_z3 = solve_board_exact(b, is_starter=True, max_exits=0, solver="z3", fixed_terminals=terms)
            self.assertEqual(res_z3[0][0], cnt)
            self.assertEqual(res_z3[0][2], sel)

    @unittest.skipUnless(find_z3_bin() is not None, "z3 binary not installed")
    def test_z3_matches_dreyfus_wagner_on_all_sample_boards(self):
        """Verify --solver z3 matches --solver dw node count and weighted objective across all 5 sample boards."""
        for idx, b in enumerate(self.boards, 1):
            req = b.required_terminals()
            dw_res = solve_board_exact(b, is_starter=True, max_exits=0, solver="dw", fixed_terminals=req)[0]
            z3_res = solve_board_exact(b, is_starter=True, max_exits=0, solver="z3", fixed_terminals=req)[0]
            self.assertEqual(
                z3_res[0],
                dw_res[0],
                f"Board #{idx} ({b.board_name}) node count mismatch: Z3={z3_res[0]} vs DW={dw_res[0]}",
            )
            self.assertAlmostEqual(
                z3_res[1],
                dw_res[1],
                places=9,
                msg=f"Board #{idx} ({b.board_name}) weighted cost mismatch: Z3={z3_res[1]} vs DW={dw_res[1]}",
            )
            # On Boards 1, 2, 3 the optimal Steiner tree is unique; verify exact set equality
            if idx in (1, 2, 3):
                self.assertEqual(z3_res[2], dw_res[2])

    @unittest.skipUnless(HAS_SCIPY, "scipy not installed")
    def test_milp_matches_dreyfus_wagner_on_sample_boards(self):
        """Verify --solver milp matches --solver dw node count and weighted objective."""
        for idx in (0, 4):
            b = self.boards[idx]
            req = b.required_terminals()
            dw_res = solve_board_exact(b, is_starter=True, max_exits=0, solver="dw", fixed_terminals=req)[0]
            milp_res = solve_board_exact(b, is_starter=True, max_exits=0, solver="milp", fixed_terminals=req)[0]
            self.assertEqual(milp_res[0], dw_res[0])
            self.assertAlmostEqual(milp_res[1], dw_res[1], places=6)

    def test_priority_weight_normalization_under_all_stat_groups(self):
        """Verify board-wide priority discount sum stays < 0.5 even when all 7 stat groups are prioritized."""
        all_groups = ["ALL", "MAGIC", "DEX", "INT", "STR", "WILL", "RARE"]
        for bid in sorted(self.meta.keys())[:10]:
            b = ParagonBoard(
                {"id": bid, "nodes": {}, "glyph": "Rare_056_Dexterity_Main", "glyphLevel": 100},
                self.meta,
                prioritize_stats=all_groups,
            )
            discount_sum = sum(
                1.0 - b.node_weight(c, set())
                for c, nt in b.grid_type.items()
                if nt != NODE_TYPE.DEAD
            )
            self.assertLess(
                discount_sum,
                0.5,
                f"Board {bid} discount sum {discount_sum:.4f} >= 0.5; node count primacy could be violated.",
            )

    def test_finding_1_validated_stat_swaps(self):
        """Verify every 1-pt swap candidate in Pass 2 is unselected and adjacent to the ablated tree."""
        contexts = {}
        for idx, b in enumerate(self.boards, 1):
            contexts[idx] = HintContext(idx, b, self.boards, shared_contexts=contexts)

        swap_hints = [
            h for h in self.hints if h.category == "Inefficient Stat Target" and "swap for 1-pt adjacent" in h.message
        ]
        self.assertGreater(len(swap_hints), 0)
        for h in swap_hints:
            ctx = contexts[h.board_index]
            b = ctx.board
            target_sc = h.screen_coords[0]
            u = b.screen_to_unrotated(target_sc[0], target_sc[1])
            pts_saved, removed, added = ctx.marginal_ablation({u}, force_exact=not b.in_glyph_radius(u))
            self.assertGreaterEqual(pts_saved, 2)
            new_selected = (b.selected_nodes - removed) | added

            part = h.message.split("swap for 1-pt adjacent")[1].split("to save")[0]
            coord_str = part.split("at (")[1].split(")")[0]
            sr, sc_c = [int(x.strip()) for x in coord_str.split(",")]
            unrot_cand = b.screen_to_unrotated(sr, sc_c)
            self.assertNotIn(unrot_cand, new_selected)
            self.assertTrue(
                any(nb in new_selected for nb in neighbors4(unrot_cand)),
                f"Swap candidate {(sr, sc_c)} is not adjacent to ablated tree on board #{h.board_index}.",
            )

    def test_finding_2_secondary_glyph_overinvestment_qualification(self):
        """Verify Secondary Glyph Over-Invested quantifies primary scaling loss and scope."""
        over_hints = [h for h in self.hints if h.category == "Secondary Glyph Over-Invested"]
        self.assertEqual(len(over_hints), 1)
        h = over_hints[0]
        self.assertEqual(h.board_index, 2)
        self.assertEqual(h.points_delta, 9)
        self.assertIn("unmarking 14 in-radius INT targets", h.message)
        self.assertIn("trading -49 INT of 'Nightstalker' primary bonus scaling (74 -> 25 INT)", h.message)

    def test_finding_3_immutable_class_primary_stat(self):
        """Verify --prioritize-stat INT does not alter a Rogue board's intrinsic class primary_stat."""
        b = ParagonBoard(self.sample_raw[0], self.meta, prioritize_stats=["INT"])
        ctx = HintContext(1, b, [b])
        self.assertEqual(ctx.primary_stat, "DEX")

    def test_finding_4_whole_board_removal_and_leaf_guard(self):
        """Verify whole-board removal reports full child+parent stat loss and requires a 1-exit leaf board."""
        danse_hints = [
            h for h in self.hints if h.board_index == 5 and h.category == "High-Cost Target Spur"
        ]
        self.assertEqual(len(danse_hints), 1)
        h = danse_hints[0]
        self.assertEqual(h.points_delta, 17)
        self.assertIn(
            "removing the whole board saves exactly 17 Paragon points with the layout re-solved "
            "[-40 DEX, -20 INT, -30 STR, -5 WILL]",
            h.message,
        )

        # Verify against full 4-board re-optimization without board #5
        four_boards = [ParagonBoard(entry, self.meta) for entry in self.sample_raw[:4]]
        four_boards = optimize_layout_and_paths(four_boards, solver="auto")
        self.assertEqual(sum(len(b.purchased_nodes()) for b in four_boards), 241 - 17)

        # Verify a sole-target transit board (2 active exits) does NOT claim whole-board removal
        # and does not emit a Legendary Spur Cost hint
        transit_b = copy.deepcopy(self.boards[4])
        # Add a 2nd active exit gate to simulate a transit board
        for ec in EXIT_COORDS.values():
            if ec not in transit_b.selected_nodes:
                transit_b.selected_nodes.add(ec)
                break
        t_ctx = HintContext(5, transit_b, self.boards)
        t_hints = [
            th for name, fn in [p for p in __import__("paragon.hints", fromlist=["HINT_PASSES"]).HINT_PASSES if "Single Target" in p[0]]
            for th in fn(t_ctx)
        ]
        self.assertEqual(t_hints, [])

    def test_finding_5_and_6_glyph_swaps_and_barebones_socket(self):
        """Verify barebones glyph board requires socket and cross-board swaps respect incoming radius."""
        bare = ParagonBoard(
            {
                "id": "Paragon_Rogue_03",
                "nodes": {},
                "glyph": "Rare_054_Dexterity_Main",
                "glyphLevel": 46,
            },
            self.meta,
        )
        self.assertIsNotNone(bare.glyph_coord)
        self.assertIn(bare.glyph_coord, bare.target_nodes)

        # Verify 0-Pt Glyph Socket Swap triggers when Headhunter (#3) and Ranger (#4) are swapped
        swapped_boards = copy.deepcopy(self.boards)
        b3, b4 = swapped_boards[2], swapped_boards[3]
        b3.glyph, b4.glyph = b4.glyph, b3.glyph
        b3.glyph_name, b4.glyph_name = b4.glyph_name, b3.glyph_name
        b3.glyph_mode, b4.glyph_mode = b4.glyph_mode, b3.glyph_mode
        s_ctxs = {}
        for idx, sb in enumerate(swapped_boards, 1):
            s_ctxs[idx] = HintContext(idx, sb, swapped_boards, shared_contexts=s_ctxs)
        swap_fn = [fn for name, fn in __import__("paragon.hints", fromlist=["HINT_PASSES"]).HINT_PASSES if "Socket Swaps" in name][0]
        zero_pt_swaps = [h for h in swap_fn(s_ctxs[3]) if h.category == "0-Pt Glyph Socket Swap"]
        self.assertEqual(len(zero_pt_swaps), 1)
        self.assertIn("+5 DEX for 'Ranger'", zero_pt_swaps[0].message)
        self.assertIn("+2 normal nodes for 'Headhunter'", zero_pt_swaps[0].message)

    def test_reroute_rare_acquisition_and_glyph_breakpoints(self):
        """Verify Pass 6 finds Skulker for +1 pt via reroute and Pass 1 previews radius breakpoints & repairs."""
        skulker_hints = [
            h for h in self.hints if h.board_index == 5 and "Skulker" in h.message
        ]
        self.assertEqual(len(skulker_hints), 1)
        self.assertEqual(skulker_hints[0].category, "Reroute +1-Pt Rare/Legendary")
        self.assertIn("rerouting 7 path nodes unlocks it for only +1 additional Paragon point", skulker_hints[0].message)

        # Test Glyph Radius Breakpoint preview when a glyph is Lv.1 (R=3)
        low_lvl_boards = copy.deepcopy(self.boards)
        b1 = low_lvl_boards[0]
        b1.glyph_level = 1
        b1.glyph_radius = 3
        l_ctx = HintContext(1, b1, low_lvl_boards)
        synergy_fn = [fn for name, fn in __import__("paragon.hints", fromlist=["HINT_PASSES"]).HINT_PASSES if "Synergy" in name][0]
        bp_hints = [h for h in synergy_fn(l_ctx) if h.category == "Glyph Radius Breakpoint"]
        self.assertEqual(len(bp_hints), 1)
        self.assertIn("Lv.15 (R=4) reaches 75/40 DEX (+25 DEX)", bp_hints[0].message)
        self.assertIn("Lv.46 (R=5) reaches 105/40 DEX (+55 DEX)", bp_hints[0].message)

    def test_all_user_json_files_parse_and_solve(self):
        """Verify all user JSON files in tests/*.json parse, solve, and produce valid summaries and hints."""
        json_files = sorted(glob.glob("tests/*.json"))
        self.assertGreaterEqual(len(json_files), 3)
        expected_totals = {
            "tests/1_board_1_glyph.json": (1, 13),
            "tests/Shred_Endgame_Druid.json": (6, 231),
            "tests/eternal_rogue_sample.json": (5, 246),
        }
        for path in json_files:
            with open(path, "r", encoding="utf-8") as f:
                raw = unwrap_paragon_boards(json.load(f))
            boards = [ParagonBoard(entry, self.meta) for entry in raw]
            boards = [b for b in boards if len(b.target_nodes) > 0 or b.is_starter_board()]
            opt = optimize_layout_and_paths(boards, solver="auto")
            self._assert_valid_optimized_build(opt, label=path)
            layout_str = format_layout_map_unicode(opt, color=False)
            self.assertIn("2D Paragon Board Layout", layout_str)
            hints = run_optimization_hints(opt, solver="auto", progress_file=None)
            self.assertIsInstance(hints, list)
            if path in expected_totals:
                exp_boards, exp_nodes = expected_totals[path]
                self.assertEqual(len(opt), exp_boards, f"Unexpected board count for {path}")
                self.assertEqual(
                    sum(len(b.selected_nodes) for b in opt),
                    exp_nodes,
                    f"Unexpected total selected nodes for {path}",
                )
            for b in opt:
                exported = b.export_data()["nodes"]
                if b.is_starter_board():
                    start_idx_str = str(STARTER_START_COORD[0] * b.size + STARTER_START_COORD[1])
                    self.assertNotIn(
                        start_idx_str,
                        exported,
                        f"Starter board exported start gate {start_idx_str} in {path}",
                    )
            if path == "tests/Shred_Endgame_Druid.json":
                in_total = sum(len(e["nodes"]) for e in raw)
                out_total = sum(len(b.export_data()["nodes"]) for b in opt)
                self.assertEqual(out_total, in_total, f"Exported node count mismatch on {path}")
                vamp_boards = [b for b in opt if b.glyph == "S02_Rare_Vampiric_01"]
                self.assertEqual(len(vamp_boards), 1)
                self.assertEqual(vamp_boards[0].glyph_name, "Tears of Blood")
                self.assertEqual(vamp_boards[0].glyph_stat_in_radius(), 169)

    def test_all_maxroll_builds_parse_and_solve(self):
        """Verify all 233 unique Paragon configs across all profiles & steps in tests/builds/*.json parse and solve."""
        build_files = sorted(glob.glob("tests/builds/*.json"))
        self.assertEqual(len(build_files), 75)
        legacy_pre_s6 = {
            "tests/builds/Lightning_Storm_Endgame_Druid.json",
            "tests/builds/Whirlwind_Endgame_Barbarian.json",
        }
        total_configs = 0
        print(file=sys.stderr, flush=True)
        for path in build_files:
            with open(path, "r", encoding="utf-8") as f:
                configs = unwrap_all_paragon_configs(json.load(f))
            self.assertGreater(len(configs), 0, f"No paragon configs found in {path}")
            for label, raw in configs:
                total_configs += 1
                print(
                    f"  [{total_configs:3d}/233] {path} [{label}] ... ",
                    end="",
                    file=sys.stderr,
                    flush=True,
                )
                if path in legacy_pre_s6:
                    with self.assertRaises(ValueError, msg=f"Expected pre-S6 DEAD cell ValueError on {path} {label}"):
                        [ParagonBoard(e, self.meta) for e in raw]
                    print("ok (legacy pre-S6 rejected)", file=sys.stderr, flush=True)
                    continue
                boards = [ParagonBoard(e, self.meta) for e in raw]
                boards = [b for b in boards if b.target_nodes or b.relaxed_nodes or b.is_starter_board()]
                opt = optimize_layout_and_paths(boards, solver="auto")
                self._assert_valid_optimized_build(opt, label=f"{path} [{label}]")
                pts = sum(len(b.purchased_nodes()) for b in opt)
                print(f"ok ({len(opt)} boards, {pts} pts)", file=sys.stderr, flush=True)
        self.assertEqual(total_configs, 233)

        # Verify unwrap_paragon_boards selects .data.activeProfile and .paragon.position by default
        with open("tests/builds/Apocalypse_Endgame_Warlock.json", "r", encoding="utf-8") as f:
            apoc_doc = json.load(f)
        self.assertEqual(apoc_doc["data"]["activeProfile"], 4)
        self.assertEqual(apoc_doc["data"]["profiles"][4]["paragon"]["position"], 2)
        self.assertEqual(
            unwrap_paragon_boards(apoc_doc),
            apoc_doc["data"]["profiles"][4]["paragon"]["steps"][2]["data"],
        )
        # And falls back to the first non-empty profile when activeProfile is an empty leveling profile
        with open("tests/builds/Smite_Shred_by_Moxsy.json", "r", encoding="utf-8") as f:
            smite_doc = json.load(f)
        self.assertEqual(smite_doc["data"]["activeProfile"], 1)
        self.assertEqual(
            unwrap_paragon_boards(smite_doc),
            smite_doc["data"]["profiles"][2]["paragon"]["steps"][0]["data"],
        )

        # Verify exact --relax-path-nodes savings on standout Maxroll builds (in purchased Paragon points)
        expected_relaxed = {
            "tests/builds/Angie_will_crush_T12___O.json": (331, 290),
            "tests/builds/_S14__Charge_Barb___HC_Push_Variant.json": (342, 316),
            "tests/builds/Spiritborn.json": (342, 321),
            "tests/builds/2_Button_Necro.json": (252, 233),
            "tests/builds/Paladin_Arbiter_Endgame__Eternal_Non_Seasonal.json": (323, 312),
            "tests/builds/Minion_Endgame_Warlock.json": (342, 332),
        }
        for idx, (path, (exp_in_pts, exp_rel_pts)) in enumerate(expected_relaxed.items(), 1):
            print(
                f"  [relaxed {idx}/{len(expected_relaxed)}] {path} ... ",
                end="",
                file=sys.stderr,
                flush=True,
            )
            with open(path, "r", encoding="utf-8") as f:
                raw = unwrap_paragon_boards(json.load(f))
            b_rel = [ParagonBoard(e, self.meta, relax_path_nodes=True) for e in raw]
            b_rel = [b for b in b_rel if b.target_nodes or b.relaxed_nodes or b.is_starter_board()]
            in_pts = sum(len(b.purchased_nodes(b.input_nodes)) for b in b_rel)
            opt_rel = optimize_layout_and_paths(b_rel, solver="auto")
            self._assert_valid_optimized_build(opt_rel, label=f"relaxed {path}")
            rel_pts = sum(len(b.purchased_nodes()) for b in opt_rel)
            self.assertEqual(in_pts, exp_in_pts, f"Input points mismatch for {path}")
            self.assertEqual(rel_pts, exp_rel_pts, f"Relaxed points mismatch for {path}")
            print(f"ok ({in_pts} -> {rel_pts} pts)", file=sys.stderr, flush=True)
        print("  ... ", end="", file=sys.stderr, flush=True)

    def test_relax_path_nodes(self):
        """Verify --relax-path-nodes reroutes exported full-path builds without lowering any Glyph's in-radius stat."""
        with open("tests/builds/Paladin_Arbiter_Endgame__Eternal_Non_Seasonal.json", "r", encoding="utf-8") as f:
            raw = unwrap_paragon_boards(json.load(f))
        base = optimize_layout_and_paths([ParagonBoard(e, self.meta) for e in raw], solver="auto")
        relaxed = optimize_layout_and_paths(
            [ParagonBoard(e, self.meta, relax_path_nodes=True) for e in raw], solver="auto"
        )
        self._assert_valid_optimized_build(relaxed, label="relaxed Paladin")
        self.assertEqual(sum(len(b.selected_nodes) for b in base), 328)
        self.assertEqual(sum(len(b.selected_nodes) for b in relaxed), 317)
        base_by_id = {b.board_type_id: b for b in base}
        for b in relaxed:
            self.assertGreater(len(b.relaxed_nodes), 0)
            self.assertGreaterEqual(
                b.glyph_stat_in_radius(), base_by_id[b.board_type_id].glyph_stat_in_radius(),
                f"Glyph stat decreased on {b.board_name}",
            )
            for c in b.relaxed_nodes:
                self.assertIn(b.grid_type[c], (NODE_TYPE.DEX, NODE_TYPE.INT, NODE_TYPE.STR, NODE_TYPE.WILL))
                self.assertFalse(b.in_glyph_radius(c) and b.grid_type[c] in b.glyph_stat_types)

        # Target-only inputs (no plain stat targets outside glyph radii) are unaffected
        rogue = optimize_layout_and_paths(
            [ParagonBoard(e, self.meta, relax_path_nodes=True) for e in self.sample_raw], solver="auto"
        )
        self.assertEqual(sum(len(b.selected_nodes) for b in rogue), 246)

    def test_exact_board_removal_matches_full_reoptimization(self):
        """Verify cached layout re-solves match fresh full re-optimization when each board is removed."""
        total = sum(len(b.selected_nodes) for b in self.boards)
        for b in self.boards[1:]:
            cnt, selections = solve_layout_without(self.boards, {id(b)})
            fresh = optimize_layout_and_paths(
                [ParagonBoard(e, self.meta) for e in self.sample_raw if e["id"] != b.board_type_id],
                solver="auto",
            )
            self.assertEqual(cnt, sum(len(fb.selected_nodes) for fb in fresh), f"Removal mismatch for {b.board_name}")
            self.assertEqual(cnt, sum(len(s) for s in selections.values()))
        cost_hints = {h.board_name: h for h in self.hints if h.category == "Board Cost"}
        self.assertEqual(set(cost_hints), {"Exploit Weakness", "Cheap Shot"})
        self.assertIn("removing it frees 50 Paragon points", cost_hints["Cheap Shot"].message)
        self.assertIn("removing it only frees 65 net Paragon points", cost_hints["Exploit Weakness"].message)
        self.assertEqual(total, 246)
        self.assertEqual(sum(len(b.purchased_nodes()) for b in self.boards), 241)

    def test_exact_glyph_threshold_repair(self):
        """Verify exact Glyph threshold repair finds proven-minimum repairs that meet the threshold."""
        with open("tests/Shred_Endgame_Druid.json", "r", encoding="utf-8") as f:
            raw = json.load(f)
        opt = optimize_layout_and_paths([ParagonBoard(e, self.meta) for e in raw], solver="auto")
        expected = {"Keeper": 3, "Bane": 2}
        for idx, b in enumerate(opt, 1):
            if b.glyph_name not in expected:
                continue
            ctx = HintContext(idx, b, opt)
            new_sel, lb_count = ctx.exact_threshold_repair()
            self.assertEqual(len(new_sel), lb_count, f"Repair for {b.glyph_name} not proven minimal")
            self.assertEqual(len(new_sel) - len(b.selected_nodes), expected[b.glyph_name])
            self.assertGreaterEqual(b.glyph_stat_in_radius(new_sel), b.glyph_threshold)
            self.assertTrue(ctx.base_required.issubset(new_sel))
            # The current selection does not meet the threshold (otherwise no repair would be needed)
            self.assertLess(b.glyph_stat_in_radius(b.selected_nodes), b.glyph_threshold)
        hints = run_optimization_hints(opt, solver="auto", progress_file=None)
        repair_msgs = [h.message for h in hints if h.category == "Glyph Threshold Unmet"]
        self.assertEqual(len(repair_msgs), 2)
        self.assertTrue(any("'Keeper'" in m and "Minimum repair: +3 Paragon points" in m for m in repair_msgs))
        self.assertTrue(any("'Bane'" in m and "Minimum repair: +2 Paragon points" in m for m in repair_msgs))
        for m in repair_msgs:
            self.assertIn("--glyph-strategy=bonus", m)

    def test_glyph_strategy(self):
        """Verify --glyph-strategy supports global and per-Glyph strategies ('bonus', 'max', 'none', 'preserve')."""
        # 1. Barebones 1-board build (`tests/1_board_1_glyph.json`): 12 pts (20/40 WILL) -> 16 pts (40/40 WILL) -> 28 pts (85/40 WILL)
        with open("tests/1_board_1_glyph.json", "r", encoding="utf-8") as f:
            raw_1 = json.load(f)
        opt_def = optimize_layout_and_paths([ParagonBoard(e, self.meta) for e in raw_1], solver="auto")
        opt_bon = optimize_layout_and_paths(
            [ParagonBoard(e, self.meta, glyph_strategy="bonus") for e in raw_1], solver="auto"
        )
        opt_all = optimize_layout_and_paths(
            [ParagonBoard(e, self.meta, glyph_strategy="max") for e in raw_1], solver="auto"
        )
        self._assert_valid_optimized_build(opt_bon, label="1_board_1_glyph glyph_strategy=bonus")
        self.assertEqual(len(opt_def[0].selected_nodes), 13)
        self.assertEqual(opt_def[0].glyph_stat_in_radius(), 20)
        self.assertEqual(len(opt_bon[0].selected_nodes), 17)
        self.assertEqual(opt_bon[0].glyph_stat_in_radius(), 40)
        self.assertGreater(len(opt_bon[0].auto_glyph_bonus_nodes), 0)
        self.assertLess(len(opt_bon[0].selected_nodes), len(opt_all[0].selected_nodes))
        hints_bon_1 = run_optimization_hints(opt_bon, solver="auto", progress_file=None)
        self.assertEqual([h for h in hints_bon_1 if h.category == "Glyph Threshold Unmet"], [])
        self.assertEqual([h for h in hints_bon_1 if h.category in ("Inefficient Stat Target", "High-Cost Target Spur")], [])

        # 2. 6-board Druid build (`tests/Shred_Endgame_Druid.json`): repairs Keeper (+3) and Bane (+2)
        with open("tests/Shred_Endgame_Druid.json", "r", encoding="utf-8") as f:
            raw_d = json.load(f)
        druid_bon = optimize_layout_and_paths(
            [ParagonBoard(e, self.meta, glyph_strategy="bonus") for e in raw_d], solver="auto"
        )
        self._assert_valid_optimized_build(druid_bon, label="Druid glyph_strategy=bonus")
        self.assertEqual(sum(len(b.selected_nodes) for b in druid_bon), 236)
        for b in druid_bon:
            if b.glyph and b.glyph_threshold > 0:
                self.assertGreaterEqual(b.glyph_stat_in_radius(), b.glyph_threshold, f"{b.glyph_name} below threshold")
        hints_druid = run_optimization_hints(druid_bon, solver="auto", progress_file=None)
        self.assertEqual([h for h in hints_druid if h.category == "Glyph Threshold Unmet"], [])

        # Verify solve_layout_without matches fresh re-optimization with glyph_strategy="bonus"
        leaf_b = next(b for b in druid_bon if b.board_name == "Lust for Carnage")
        cnt_without, _ = solve_layout_without(druid_bon, {id(leaf_b)})
        fresh_without = optimize_layout_and_paths(
            [ParagonBoard(e, self.meta, glyph_strategy="bonus") for e in raw_d if e["id"] != leaf_b.board_type_id],
            solver="auto",
        )
        self.assertEqual(cnt_without, sum(len(b.selected_nodes) for b in fresh_without))

        # 3. Combined with --relax-path-nodes and --glyph-strategy=bonus: relaxes plain +5 stat nodes inside and
        #    outside the radius and jointly selects the 2D layout, exits, and minimum-point in-radius nodes to meet
        #    every socketed Glyph's bonus threshold (219 nodes = 218 Paragon points).
        druid_rel_bon = optimize_layout_and_paths(
            [ParagonBoard(e, self.meta, relax_path_nodes=True, glyph_strategy="bonus") for e in raw_d], solver="auto"
        )
        self._assert_valid_optimized_build(druid_rel_bon, label="Druid relax + glyph_strategy=bonus")
        self.assertEqual(sum(len(b.selected_nodes) for b in druid_rel_bon), 219)
        for b in druid_rel_bon:
            if b.glyph and b.glyph_threshold > 0:
                self.assertGreaterEqual(b.glyph_stat_in_radius(), b.glyph_threshold)

        # Verify solve_layout_without also matches fresh re-optimization under relax + glyph_strategy=bonus
        leaf_rel_b = next(b for b in druid_rel_bon if b.board_name == "Lust for Carnage")
        cnt_rel_without, _ = solve_layout_without(druid_rel_bon, {id(leaf_rel_b)})
        fresh_rel_without = optimize_layout_and_paths(
            [
                ParagonBoard(e, self.meta, relax_path_nodes=True, glyph_strategy="bonus")
                for e in raw_d
                if e["id"] != leaf_rel_b.board_type_id
            ],
            solver="auto",
        )
        self.assertEqual(cnt_rel_without, sum(len(b.selected_nodes) for b in fresh_rel_without))

        # 4. Combined with --relax-path-nodes and --glyph-strategy=max: relaxes out-of-radius plain +5 stat
        #    nodes while keeping all matching in-radius stat nodes mandatory.
        druid_rel_all = optimize_layout_and_paths(
            [ParagonBoard(e, self.meta, relax_path_nodes=True, glyph_strategy="max") for e in raw_d],
            solver="auto",
        )
        self._assert_valid_optimized_build(druid_rel_all, label="Druid relax + glyph_strategy=max")
        self.assertGreater(sum(len(b.selected_nodes) for b in druid_rel_all), sum(len(b.selected_nodes) for b in druid_rel_bon))
        for b in druid_rel_all:
            for (r, c), nt in b.grid_type.items():
                if b.in_glyph_radius((r, c)) and nt in b.glyph_stat_types:
                    self.assertIn((r, c), b.selected_nodes)
                    self.assertNotIn((r, c), b.relaxed_nodes)

        # 5. Lv.1 (R=3) board where STAT + MAGIC_<STAT> alone only give 30/40 WILL:
        #    `glyph_strategy='max'` includes all WILL/MAGIC_WILL in radius and adds a +10 WILL Rare node to reach 40/40.
        raw_lv1 = [{"id": "Paragon_Warlock_00", "nodes": {"136": 1}, "glyph": "Rare_Will_Generic", "glyphLevel": 1}]
        lv1_max = optimize_layout_and_paths(
            [ParagonBoard(e, self.meta, glyph_strategy="max") for e in raw_lv1], solver="auto"
        )
        self.assertGreaterEqual(lv1_max[0].glyph_stat_in_radius(), 40)

        # 6. Mixed per-Glyph overrides: --glyph-strategy=Ranger=bonus,Headhunter=none,Nightstalker=max
        #    and --glyph-strategy=bonus,Headhunter=max
        mixed_spec = "Ranger=bonus,Headhunter=none,Nightstalker=max"
        rogue_mixed = optimize_layout_and_paths(
            [ParagonBoard(e, self.meta, relax_path_nodes=True, glyph_strategy=mixed_spec) for e in self.sample_raw],
            solver="auto",
        )
        self._assert_valid_optimized_build(rogue_mixed, label="Rogue mixed glyph_strategy")
        by_glyph = {b.glyph_name: b for b in rogue_mixed if b.glyph_name}
        self.assertEqual(by_glyph["Ranger"].glyph_strategy, "bonus")
        self.assertEqual(by_glyph["Ranger"].glyph_stat_in_radius(), 45)
        self.assertEqual(by_glyph["Headhunter"].glyph_strategy, "none")
        self.assertFalse(by_glyph["Headhunter"].glyph_bonus)
        self.assertEqual(by_glyph["Headhunter"].glyph_stat_in_radius(), 45)  # pared down from 105 DEX
        self.assertEqual(by_glyph["Nightstalker"].glyph_strategy, "max")
        for (r, c), nt in by_glyph["Nightstalker"].grid_type.items():
            if by_glyph["Nightstalker"].in_glyph_radius((r, c)) and nt in by_glyph["Nightstalker"].glyph_stat_types:
                self.assertIn((r, c), by_glyph["Nightstalker"].selected_nodes)
        # Unspecified glyph ('Grenadier') defaults to 'preserve' and keeps its full 105 DEX
        self.assertEqual(by_glyph["Grenadier"].glyph_strategy, "preserve")
        self.assertEqual(by_glyph["Grenadier"].glyph_stat_in_radius(), 105)

        # Verify setting Ranger=none with --relax-path-nodes drops Ranger below threshold (20/40 DEX)
        # and suppresses 'Glyph Threshold Unmet' for Ranger in --hints
        rogue_ranger_none = optimize_layout_and_paths(
            [ParagonBoard(e, self.meta, relax_path_nodes=True, glyph_strategy="Ranger=none") for e in self.sample_raw],
            solver="auto",
        )
        ranger_b = next(b for b in rogue_ranger_none if b.glyph_name == "Ranger")
        self.assertLess(ranger_b.glyph_stat_in_radius(), ranger_b.glyph_threshold)
        hints_r_none = run_optimization_hints(rogue_ranger_none, solver="auto", progress_file=None)
        self.assertEqual([h for h in hints_r_none if h.category == "Glyph Threshold Unmet" and "Ranger" in h.message], [])

        # Test global default + override: --glyph-strategy=bonus,Headhunter=max
        rogue_bon_hh_max = optimize_layout_and_paths(
            [ParagonBoard(e, self.meta, relax_path_nodes=True, glyph_strategy="bonus,Headhunter=max") for e in self.sample_raw],
            solver="auto",
        )
        self._assert_valid_optimized_build(rogue_bon_hh_max, label="Rogue bonus,Headhunter=max")
        by_glyph2 = {b.glyph_name: b for b in rogue_bon_hh_max if b.glyph_name}
        self.assertEqual(by_glyph2["Headhunter"].glyph_strategy, "max")
        self.assertEqual(by_glyph2["Headhunter"].glyph_stat_in_radius(), 105)
        for name in ("Grenadier", "Nightstalker", "Ranger"):
            self.assertEqual(by_glyph2[name].glyph_strategy, "bonus")
            self.assertGreaterEqual(by_glyph2[name].glyph_stat_in_radius(), by_glyph2[name].glyph_threshold)

        # Validate error handling for unknown strategy or unknown glyph name
        with self.assertRaises(ValueError):
            parse_glyph_strategy("invalid_strat")
        with self.assertRaises(ValueError):
            parse_glyph_strategy("NotARealGlyph=bonus")

    def test_tier1_and_tier2_audit_improvements(self):
        """
        Regression tests for Tier 1 (solver tie-breaking, node_priority for in-radius MAGIC_<STAT>,
        and pure-stat threshold repairs) and Tier 2 (mode-aware over-investment, multi-node stat pockets,
        in-radius cluster periphery pruning, manual path alignment, in-radius d=2 Rares, and 0-pt
        threshold-unlocking socket swaps).
        """
        # 1. _solve_glyph_threshold_repair_impl evaluates all equal-minimum-point ties (umask=1101 on Rogue_03)
        b_rogue03 = ParagonBoard(self.sample_raw[3], self.meta, relax_path_nodes=True, glyph_strategy="bonus")
        sol_r03 = solve_board_exact(b_rogue03, is_starter=False, max_exits=4, solver="auto")[0b1101]
        b_rogue03.selected_nodes = sol_r03[2]
        rep_r03 = solve_glyph_threshold_repair(b_rogue03, solver="auto")
        self.assertIsNotNone(rep_r03)
        self.assertEqual(len(rep_r03[0]), 52)
        self.assertEqual(b_rogue03.glyph_stat_in_radius(rep_r03[0]), 45)

        # 2. _solve_glyph_threshold_repair_impl prefers pure STAT candidates over unselected RARE + zero-stat MAGIC
        with open("tests/Shred_Endgame_Druid.json", "r", encoding="utf-8") as f:
            raw_druid = json.load(f)
        b_druid04 = ParagonBoard(raw_druid[5], self.meta, relax_path_nodes=True, glyph_strategy="bonus")
        sol_d04 = solve_board_exact(b_druid04, is_starter=False, max_exits=4, solver="auto")[0b0010]
        b_druid04.selected_nodes = sol_d04[2]
        rep_d04 = solve_glyph_threshold_repair(b_druid04, solver="auto")
        self.assertIsNotNone(rep_d04)
        self.assertEqual(len(rep_d04[0]), 25)
        self.assertEqual(b_druid04.glyph_stat_in_radius(rep_d04[0]), 40)
        self.assertNotIn((8, 13), rep_d04[0])
        self.assertNotIn((8, 12), rep_d04[0])

        # 3. ParagonBoard.node_priority boosts non-governed MAGIC_<STAT> inside a magic-mode Glyph's radius
        with open("tests/builds/Paladin_Arbiter_Endgame__Eternal_Non_Seasonal.json", "r", encoding="utf-8") as f:
            raw_pal = unwrap_paragon_boards(json.load(f))
        b_pal04 = ParagonBoard(raw_pal[3], self.meta)  # Relentless with 'Resplendence' (STR, magic mode)
        self.assertEqual(b_pal04.glyph_mode, "magic")
        in_rad_magic_non_str = [
            c for c, nt in b_pal04.grid_type.items()
            if b_pal04.in_glyph_radius(c) and nt in (NODE_TYPE.MAGIC_INT, NODE_TYPE.MAGIC_WILL, NODE_TYPE.MAGIC_DEX)
        ]
        self.assertGreater(len(in_rad_magic_non_str), 0)
        for c in in_rad_magic_non_str:
            nt = b_pal04.grid_type[c]
            self.assertAlmostEqual(b_pal04.node_priority(c), b_pal04.priorities[nt] + 0.50, places=6)

        # 4. Paladin hints: Secondary Glyph Over-Invested (#1 Spirit), Glyph Stat Over-Invested (#4 Resplendence),
        #    Manual Path Targets (--relax-path-nodes), and no false-positive Resplendence ceiling swap in Pass 8
        pal_opt = optimize_layout_and_paths([ParagonBoard(e, self.meta) for e in raw_pal], solver="auto")
        pal_hints = run_optimization_hints(pal_opt, solver="auto", progress_file=None)
        sec_over = [h for h in pal_hints if h.category == "Secondary Glyph Over-Invested" and h.board_index == 1]
        self.assertEqual(len(sec_over), 1)
        self.assertGreaterEqual(sec_over[0].points_delta, 6)
        stat_over = [h for h in pal_hints if h.category == "Glyph Stat Over-Invested" and h.board_index == 4]
        self.assertEqual(len(stat_over), 1)
        self.assertGreaterEqual(stat_over[0].points_delta, 3)
        manual_hints = [h for h in pal_hints if h.category == "Manual Path Targets" and h.points_delta > 0]
        self.assertGreaterEqual(sum(h.points_delta for h in manual_hints), 9)
        for h in manual_hints:
            self.assertIn("--relax-path-nodes", h.message)
        resplendence_ceiling_swaps = [
            h for h in pal_hints if h.category == "Glyph Socket Swap" and "Resplendence" in h.message
        ]
        self.assertEqual(resplendence_ceiling_swaps, [])

        # 5. Multi-node stat target pockets behind a shared bridge on relaxed Paladin (#3 and #4)
        pal_rel = optimize_layout_and_paths(
            [ParagonBoard(e, self.meta, relax_path_nodes=True) for e in raw_pal], solver="auto"
        )
        pal_rel_hints = run_optimization_hints(pal_rel, solver="auto", progress_file=None)
        pocket_hints = [
            h for h in pal_rel_hints if h.category == "Inefficient Stat Target" and "Stat target pocket" in h.message
        ]
        self.assertGreaterEqual(len(pocket_hints), 2)

        # 6. Druid hints: In-radius Cluster Periphery Pruning (#5 Devastation), in-radius d=2 Rare (#5 Nature's Will),
        #    and 0-Pt Glyph Socket Swap unlocking unmet Keeper/Bane thresholds
        druid_opt = optimize_layout_and_paths([ParagonBoard(e, self.meta) for e in raw_druid], solver="auto")
        druid_hints = run_optimization_hints(druid_opt, solver="auto", progress_file=None)
        devastation_prune = [
            h for h in druid_hints if h.category == "Cluster Periphery Pruning" and "Devastation" in h.message
        ]
        self.assertEqual(len(devastation_prune), 1)
        self.assertEqual(devastation_prune[0].points_delta, 3)
        natures_will = [
            h for h in druid_hints if h.category == "Nearby 2-Pt Rare/Legendary" and "Nature's Will" in h.message
        ]
        self.assertEqual(len(natures_will), 1)
        unlock_swaps = [
            h for h in druid_hints if h.category == "0-Pt Glyph Socket Swap" and "unlocks the bonus threshold" in h.message
        ]
        self.assertGreaterEqual(len(unlock_swaps), 2)

    def test_random_builds_and_stat_prioritization_monotonicity(self):
        """
        Generate 10 random multi-board builds (random class, boards, glyphs, legendaries, and rares)
        and verify they all solve with invariant minimum node count and monotonic stat prioritization
        (preferring DEX/INT/STR/WILL increases or maintains that stat's total grant).
        """
        rng = random.Random(1337)
        classes = ["Barb", "Druid", "Necro", "Paladin", "Rogue", "Sorc", "Spirit", "Warlock"]
        glyph_ids = sorted(GLYPH_DATA.keys())
        core_stats = ("DEX", "INT", "STR", "WILL")
        strict_gains = Counter()

        def total_stat_grant(boards_list: list[ParagonBoard], stat: str) -> int:
            return sum(
                b.node_stat_grants(c).get(stat, 0)
                for b in boards_list
                for c in b.selected_nodes
            )

        for trial in range(10):
            cls_name = classes[trial % len(classes)]
            starter_id = f"Paragon_{cls_name}_00"
            non_starters = sorted(
                k for k in self.meta.keys()
                if k.startswith(f"Paragon_{cls_name}_") and k not in (starter_id, f"Paragon_{cls_name}_0")
            )
            num_extra = rng.randint(1, 3)
            chosen_boards = [starter_id] + rng.sample(non_starters, num_extra)
            chosen_glyphs = rng.sample(glyph_ids, len(chosen_boards))

            raw_build = []
            for b_idx, (bid, gid) in enumerate(zip(chosen_boards, chosen_glyphs)):
                cell_map = self.meta[bid]
                rares = [int(idx) for idx, nt in cell_map.items() if nt == "RARE"]
                legs = [int(idx) for idx, nt in cell_map.items() if nt == "LEGENDARY"]
                glyphs = [int(idx) for idx, nt in cell_map.items() if nt == "GLYPH"]

                nodes_dict = {}
                for g_cell in glyphs:
                    nodes_dict[str(g_cell)] = 1
                if legs and rng.random() < 0.8:
                    nodes_dict[str(legs[0])] = 1
                for r_cell in rng.sample(rares, rng.randint(1, min(3, len(rares)))):
                    nodes_dict[str(r_cell)] = 1

                raw_build.append(
                    {
                        "id": bid,
                        "nodes": nodes_dict,
                        "rotation": 0,
                        "position": {"x": 0, "y": -b_idx},
                        "glyph": gid,
                        "glyphLevel": rng.choice([15, 21, 46, 100]),
                    }
                )

            solves_by_stat: dict[str, list[ParagonBoard]] = {}
            node_counts: set[int] = set()
            for s in core_stats:
                bs = [ParagonBoard(copy.deepcopy(e), self.meta, prioritize_stats=[s]) for e in raw_build]
                opt_bs = optimize_layout_and_paths(bs, solver="auto")
                self._assert_valid_optimized_build(opt_bs, label=f"trial_{trial}_{cls_name}_prio_{s}")
                solves_by_stat[s] = opt_bs
                node_counts.add(sum(len(b.selected_nodes) for b in opt_bs))

            # Also test a random 3-stat priority ordering
            rand_order = rng.sample(list(core_stats), 3)
            bs_rand = [ParagonBoard(copy.deepcopy(e), self.meta, prioritize_stats=rand_order) for e in raw_build]
            opt_rand = optimize_layout_and_paths(bs_rand, solver="auto")
            self._assert_valid_optimized_build(opt_rand, label=f"trial_{trial}_{cls_name}_prio_{rand_order}")
            node_counts.add(sum(len(b.selected_nodes) for b in opt_rand))

            # 1. Minimum node count must be strictly invariant across all stat priority settings
            self.assertEqual(
                len(node_counts),
                1,
                f"Trial {trial} ({cls_name}): stat priority changed total node count: {node_counts}",
            )

            # 2. Preferring stat `s` must achieve >= total `s` grant compared to preferring any other single stat
            for s in core_stats:
                s_when_prioritized = total_stat_grant(solves_by_stat[s], s)
                for other_s in core_stats:
                    s_when_other = total_stat_grant(solves_by_stat[other_s], s)
                    self.assertGreaterEqual(
                        s_when_prioritized,
                        s_when_other,
                        f"Trial {trial} ({cls_name}): prioritizing {s} yielded {s_when_prioritized} {s}, "
                        f"less than {s_when_other} {s} when prioritizing {other_s}!",
                    )
                    if s_when_prioritized > s_when_other:
                        strict_gains[s] += 1

        # Verify that across the 10 random builds, every Core Stat strictly increased at least once when prioritized
        for s in core_stats:
            self.assertGreater(
                strict_gains[s],
                0,
                f"Prioritizing {s} never strictly increased {s} across 10 random builds.",
            )

    def test_budget_enforcement(self):
        """Verify --budget trims non-Glyph paths and excess in-radius Glyph stats, or reports when infeasible."""
        # 1. Default 342-pt budget on Advance_Endgame_Paladin.json (343 -> 342 pts)
        with open("tests/builds/Advance_Endgame_Paladin.json", "r", encoding="utf-8") as f:
            raw_adv = unwrap_paragon_boards(json.load(f))
        adv_opt = optimize_layout_and_paths([ParagonBoard(e, self.meta) for e in raw_adv], solver="auto")
        self.assertEqual(sum(len(b.purchased_nodes()) for b in adv_opt), 343)
        adv_opt, adv_ok, adv_notes = enforce_budget(adv_opt, budget=MAX_PARAGON_POINTS, solver="auto")
        self.assertTrue(adv_ok)
        self.assertEqual(sum(len(b.purchased_nodes()) for b in adv_opt), 342)
        self._assert_valid_optimized_build(adv_opt, label="Advance_Endgame_Paladin budget=342")
        self.assertTrue(any("How we got under budget (343 -> 342 Paragon points" in n for n in adv_notes))

        # 2. Custom --budget 225 on eternal_rogue_sample.json (241 -> 225 pts, all Glyph thresholds preserved)
        rogue_225 = optimize_layout_and_paths([ParagonBoard(e, self.meta) for e in self.sample_raw], solver="auto")
        orig_non_plain = {
            b.board_type_id: {
                c for c in b.target_nodes
                if b.grid_type[c] not in (NODE_TYPE.DEX, NODE_TYPE.INT, NODE_TYPE.STR, NODE_TYPE.WILL)
            }
            for b in rogue_225
        }
        rogue_225, ok_225, notes_225 = enforce_budget(rogue_225, budget=225, solver="auto")
        self.assertTrue(ok_225)
        self.assertEqual(sum(len(b.purchased_nodes()) for b in rogue_225), 225)
        self._assert_valid_optimized_build(rogue_225, label="Rogue budget=225")
        for b in rogue_225:
            self.assertTrue(orig_non_plain[b.board_type_id].issubset(b.selected_nodes))
            if b.glyph and b.glyph_threshold > 0:
                self.assertGreaterEqual(b.glyph_stat_in_radius(), b.glyph_threshold)
        self.assertTrue(any("How we got under budget (241 -> 225 Paragon points" in n for n in notes_225))
        self.assertTrue(any("Nightstalker" in n for n in notes_225))

        # 3. Infeasible --budget 180 on eternal_rogue_sample.json (reports No solution possible + cluster cuts)
        rogue_180 = optimize_layout_and_paths([ParagonBoard(e, self.meta) for e in self.sample_raw], solver="auto")
        rogue_180, ok_180, notes_180 = enforce_budget(rogue_180, budget=180, solver="auto")
        self.assertFalse(ok_180)
        self._assert_valid_optimized_build(rogue_180, label="Rogue budget=180")
        self.assertTrue(any("No solution possible within --budget 180" in n for n in notes_180))
        self.assertTrue(any("Suggested Rare/Magic cluster or leaf-board cuts" in n for n in notes_180))


if __name__ == "__main__":
    unittest.main()
