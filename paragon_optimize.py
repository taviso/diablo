#!/usr/bin/env python3
"""
Optimal Diablo 4 Paragon Board Optimizer CLI.

Solves the exact Node-Weighted Steiner Tree problem for each board across all
exit gate combinations (using exact graph reduction rules + Dreyfus-Wagner
dynamic programming) and finds the globally optimal 2D board arrangement,
rotation, and pathing.

Accepts and produces the exact JSON format used by https://www.d4-paragon.com/.
"""

import argparse
import json
import sys

from paragon import (
    DEFAULT_META_DATA_PATH,
    MAX_PARAGON_POINTS,
    Hint,
    ParagonBoard,
    enforce_budget,
    format_board_ascii,
    format_board_hints,
    format_board_unicode,
    format_layout_map_unicode,
    format_legend_unicode,
    load_meta_data,
    optimize_layout_and_paths,
    parse_glyph_strategy,
    print_budget_report,
    print_hints,
    print_summary,
    run_optimization_hints,
    unwrap_paragon_boards,
)


def print_relax_report(
    boards: list[ParagonBoard], baseline: list[ParagonBoard], file=sys.stdout
) -> None:
    """Report point and Core Stat changes of a `--relax-path-nodes` solution vs. the unrelaxed optimum."""

    def totals(bs: list[ParagonBoard]) -> tuple[int, dict[str, int]]:
        stats = {"DEX": 0, "INT": 0, "STR": 0, "WILL": 0}
        pts = 0
        for b in bs:
            sel = b.purchased_nodes()
            pts += len(sel)
            for c in sel:
                for k, v in b.node_stat_grants(c).items():
                    stats[k] += v
        return pts, stats

    relaxed = sum(len(b.relaxed_nodes) for b in boards)
    base_pts, base_stats = totals(baseline)
    new_pts, new_stats = totals(boards)
    delta_str = ", ".join(
        f"{new_stats[k] - base_stats[k]:+d} {k}" for k in base_stats if new_stats[k] != base_stats[k]
    ) or "no core stat change"
    scope = (
        "plain stat path node(s) (with minimum-point Glyph bonus thresholds)"
        if any(b.glyph_strategy == "bonus" for b in boards)
        else (
            "plain stat path node(s) (including ignored Glyph radii)"
            if any(b.glyph_strategy == "none" for b in boards)
            else "plain stat path node(s) not feeding a Glyph"
        )
    )
    print(
        f"\nRelaxed {relaxed} {scope}: {base_pts} -> {new_pts} Paragon points "
        f"({new_pts - base_pts:+d} vs. without --relax-path-nodes); Core Stat change: {delta_str}.\n"
        "  Check that Rare node attribute requirements are still met before applying.",
        file=file,
    )


def main():
    parser = argparse.ArgumentParser(
        description="Optimal Diablo 4 Paragon Board Optimizer (compatible with d4-paragon.com JSON)."
    )
    parser.add_argument(
        "input",
        nargs="?",
        help="Input JSON file path (or '-' for stdin).",
    )
    parser.add_argument(
        "-i", "--input-file",
        dest="input_opt",
        help="Input JSON file path (alternative to positional argument).",
    )
    parser.add_argument(
        "-o", "--output",
        help="Output JSON file path (if omitted, JSON is not written and boards/summary are displayed).",
    )
    parser.add_argument(
        "--prioritize-stat",
        action="append",
        default=[],
        metavar="STAT",
        help=(
            "Stat type(s) to prioritize on equal-length connecting paths "
            "(e.g. DEX, INT, STR, WILL, MAGIC, RARE). Can be specified multiple times or comma-separated."
        ),
    )
    parser.add_argument(
        "--glyph-strategy",
        action="append",
        default=[],
        metavar="SPEC",
        help=(
            "Glyph radius optimization strategy: 'bonus' (minimum-point nodes to reach threshold), "
            "'max' (all matching stat nodes in radius), 'none' (no auto-stat nodes or tie-breaking), "
            "or 'preserve' (default: keep input targets and tie-break in radius). "
            "Accepts a global strategy and/or per-Glyph overrides by name, e.g. "
            "--glyph-strategy=bonus,Headhunter=max or --glyph-strategy=Ranger=bonus,Headhunter=none,Nightstalker=max."
        ),
    )
    parser.add_argument(
        "--relax-path-nodes",
        action="store_true",
        help=(
            "Treat plain +5 stat nodes (DEX/INT/STR/WILL) as optional path nodes instead of mandatory targets "
            "so exported full-path builds can be rerouted (preserving in-radius Glyph stat nodes for 'preserve'/'max', "
            "and paring 'bonus'/'none' Glyphs down to their threshold/minimum path). Prints the resulting point and "
            "Core Stat change vs. without --relax-path-nodes."
        ),
    )
    parser.add_argument(
        "--budget",
        type=int,
        default=MAX_PARAGON_POINTS,
        metavar="POINTS",
        help=(
            f"Maximum Paragon point budget (default: {MAX_PARAGON_POINTS}, the Diablo 4 maximum; "
            "pass 0 for unlimited). If the initial optimal solution exceeds the budget, the optimizer "
            "relaxes plain path nodes and trims excess in-radius Glyph stat nodes down toward each Glyph's "
            "bonus threshold, printing a report of how it got under budget (or reporting if no solution is "
            "possible without dropping Rare/Magic/Legendary nodes)."
        ),
    )
    parser.add_argument(
        "--meta-data",
        default=DEFAULT_META_DATA_PATH,
        help="Path to paragon_meta_data.json.",
    )
    parser.add_argument(
        "--pretty",
        action="store_true",
        help="Pretty-print the JSON output with indentation (when using --output).",
    )
    parser.add_argument(
        "--no-summary",
        action="store_true",
        help="Do not print the optimization summary table.",
    )
    parser.add_argument(
        "--hints",
        action="store_true",
        help="Analyze the optimized boards and print actionable point-saving & stat-swap hints.",
    )
    parser.add_argument(
        "--no-visualize",
        action="store_true",
        help="Do not print the grid visualization of each optimized board.",
    )
    parser.add_argument(
        "--ascii",
        action="store_true",
        help="Render boards using plain ASCII characters instead of Unicode blocks.",
    )
    parser.add_argument(
        "--no-color",
        action="store_true",
        help="Disable ANSI color codes in terminal output.",
    )
    parser.add_argument(
        "--solver",
        choices=["auto", "dw", "z3", "milp"],
        default="auto",
        help="Steiner solver backend: 'auto' (default: C++ Dreyfus-Wagner for <=15 bits, MILP/Z3 fallback for >15 bits), 'dw' (Dreyfus-Wagner), 'z3' (Z3 MaxSMT via /usr/bin/z3), or 'milp' (SciPy/HiGHS MILP).",
    )

    args = parser.parse_args()
    input_source = args.input_opt or args.input
    if not input_source:
        parser.error("Please provide an input JSON file (e.g., `paragon_optimize.py rogue.json`).")

    if input_source == "-":
        raw_input = json.load(sys.stdin)
    else:
        with open(input_source, "r", encoding="utf-8") as f:
            raw_input = json.load(f)

    try:
        raw_input = unwrap_paragon_boards(raw_input)
    except ValueError as exc:
        parser.error(str(exc))

    meta_data = load_meta_data(args.meta_data)

    prioritize_list = []
    for item in args.prioritize_stat:
        for part in item.split(","):
            if part.strip():
                prioritize_list.append(part.strip())

    input_glyphs = {e.get("glyph", "") for e in raw_input if isinstance(e, dict) and e.get("glyph")}
    parsed_strategy = parse_glyph_strategy(args.glyph_strategy, extra_glyph_names=input_glyphs)

    def build_boards(relax: bool) -> list[ParagonBoard]:
        bs = [
            ParagonBoard(
                entry,
                meta_data,
                prioritize_stats=prioritize_list,
                glyph_strategy=parsed_strategy,
                relax_path_nodes=relax,
            )
            for entry in raw_input
        ]
        return [b for b in bs if b.target_nodes or b.relaxed_nodes or b.is_starter_board()]

    optimized_boards = optimize_layout_and_paths(build_boards(args.relax_path_nodes), solver=args.solver)
    optimized_boards, budget_feasible, budget_notes = enforce_budget(
        optimized_boards, budget=args.budget, solver=args.solver
    )

    do_visualize = not args.no_visualize and not args.output
    do_summary = not args.no_summary and not args.output
    use_color = not args.no_color
    hints = (
        run_optimization_hints(optimized_boards, solver=args.solver, progress_file=sys.stderr)
        if args.hints
        else []
    )
    hints_by_board: dict[int, list[Hint]] = {}
    for h in hints:
        hints_by_board.setdefault(h.board_index, []).append(h)

    if do_visualize:
        if not args.ascii:
            print(format_layout_map_unicode(optimized_boards, color=use_color))
            print(format_legend_unicode(color=use_color))
        for idx, b in enumerate(optimized_boards, 1):
            print(format_board_ascii(b) if args.ascii else format_board_unicode(b, color=use_color))
            if args.hints and idx in hints_by_board:
                print(format_board_hints(hints_by_board[idx], color=use_color))
    if do_summary:
        print_summary(optimized_boards, file=sys.stdout)

    if budget_notes:
        print_budget_report(
            args.budget,
            budget_feasible,
            budget_notes,
            color=use_color,
            file=sys.stderr if args.output else sys.stdout,
        )
    if args.relax_path_nodes:
        baseline = optimize_layout_and_paths(build_boards(False), solver=args.solver)
        print_relax_report(optimized_boards, baseline, file=sys.stdout)
    if args.hints and not do_visualize:
        print_hints(hints, color=use_color, file=sys.stdout)

    if args.output:
        output_data = [b.export_data() for b in optimized_boards]
        json_kwargs = {"indent": 2} if args.pretty else {"separators": (",", ": ")}
        json_str = json.dumps(output_data, **json_kwargs)
        if args.output == "-":
            print(json_str)
        else:
            with open(args.output, "w", encoding="utf-8") as f:
                f.write(json_str + "\n")


if __name__ == "__main__":
    main()
