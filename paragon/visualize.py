"""
Terminal visualization and summary formatting for optimized Diablo 4 Paragon Boards.
"""

import sys
from collections import Counter

from .board import ParagonBoard
from .game_data import (
    EXIT_COORDS,
    NODE_TYPE,
    PARAGON_EXIT,
    STARTER_START_COORD,
)


def _ansi_styles(color: bool):
    reset = "\033[0m" if color else ""
    bold = "\033[1m" if color else ""

    def fg(r: int, g: int, b: int) -> str:
        return f"\033[38;2;{r};{g};{b}m" if color else ""

    def bg(r: int, g: int, b: int) -> str:
        return f"\033[48;2;{r};{g};{b}m" if color else ""

    return reset, bold, fg, bg


def format_layout_map_unicode(boards: list[ParagonBoard], color: bool = True) -> str:
    """Render a 2D overview map of all placed Paragon boards and their active gate connections."""
    RESET, BOLD, fg, _ = _ansi_styles(color)
    C_BORDER = fg(120, 135, 165)
    C_GATE = f"{BOLD}{fg(235, 110, 255)}"
    C_TITLE = f"{BOLD}{fg(255, 215, 100)}"
    C_TEXT = fg(210, 220, 235)

    pos_map = {(b.position["x"], b.position["y"]): (idx + 1, b) for idx, b in enumerate(boards)}
    xs = [p[0] for p in pos_map]
    ys = [p[1] for p in pos_map]
    min_x, max_x = min(xs), max(xs)
    min_y, max_y = min(ys), max(ys)

    def has_screen_exit(x: int, y: int, s_dir: PARAGON_EXIT) -> bool:
        if (x, y) not in pos_map:
            return False
        _, b = pos_map[(x, y)]
        if b.is_starter_board():
            return s_dir == PARAGON_EXIT.TOP and EXIT_COORDS[PARAGON_EXIT.TOP] in b.selected_nodes
        u_dir = PARAGON_EXIT((int(s_dir) - b.rotation) % 4)
        return EXIT_COORDS[u_dir] in b.selected_nodes

    lines = [f"\n{C_TITLE}╭─ 2D Paragon Board Layout & Gate Connections ─╮{RESET}"]
    for y in range(min_y, max_y + 1):
        row_top, row_mid1, row_mid2, row_mid3, row_bot, row_vconn = [], [], [], [], [], []
        for x in range(min_x, max_x + 1):
            if (x, y) in pos_map:
                order_num, b = pos_map[(x, y)]
                up = has_screen_exit(x, y, PARAGON_EXIT.TOP) and has_screen_exit(x, y - 1, PARAGON_EXIT.BOTTOM)
                dn = has_screen_exit(x, y, PARAGON_EXIT.BOTTOM) and has_screen_exit(x, y + 1, PARAGON_EXIT.TOP)
                rt = has_screen_exit(x, y, PARAGON_EXIT.RIGHT) and has_screen_exit(x + 1, y, PARAGON_EXIT.LEFT)
                lf = has_screen_exit(x, y, PARAGON_EXIT.LEFT) and has_screen_exit(x - 1, y, PARAGON_EXIT.RIGHT)

                top_gate = f"{C_GATE}══▲══{C_BORDER}" if up else "─────"
                bot_gate = f"{C_GATE}══▼══{C_BORDER}" if dn else "─────"
                l_gate = f"{C_GATE}◄{RESET}" if lf else f"{C_BORDER}│{RESET}"
                r_gate = f"{C_GATE}►{RESET}" if rt else f"{C_BORDER}│{RESET}"
                h_conn = f"{C_GATE}═══{RESET}" if rt else "   "

                label = f"#{order_num} {b.board_name}"
                if len(label) > 24:
                    label = label[:24]
                pos_rot = f"({x},{y}) rot={b.rotation} ({b.rotation * 90}°)"
                purchased_cnt = len(b.purchased_nodes())
                pts_str = f"{purchased_cnt} pts ({len(b.target_nodes)}T+{purchased_cnt - len(b.target_nodes)}P)"

                row_top.append(f"{C_BORDER}╭──────────{top_gate}──────────╮{RESET}   ")
                row_mid1.append(f"{C_BORDER}│{RESET} {BOLD}{label:<24}{RESET}{C_BORDER}│{RESET}   ")
                row_mid2.append(f"{l_gate} {C_TEXT}{pts_str:<24}{RESET}{r_gate}{h_conn}")
                row_mid3.append(f"{C_BORDER}│{RESET} {C_TEXT}{pos_rot:<24}{RESET}{C_BORDER}│{RESET}   ")
                row_bot.append(f"{C_BORDER}╰──────────{bot_gate}──────────╯{RESET}   ")
                row_vconn.append(f"             {C_GATE}║{RESET}                " if dn else "                              ")
            else:
                blank = " " * 30
                row_top.append(blank)
                row_mid1.append(blank)
                row_mid2.append(blank)
                row_mid3.append(blank)
                row_bot.append(blank)
                row_vconn.append(blank)
        lines.append("".join(row_top).rstrip())
        lines.append("".join(row_mid1).rstrip())
        lines.append("".join(row_mid2).rstrip())
        lines.append("".join(row_mid3).rstrip())
        lines.append("".join(row_bot).rstrip())
        if y < max_y:
            lines.append("".join(row_vconn).rstrip())
    return "\n".join(lines)


def format_board_unicode(
    board: ParagonBoard,
    color: bool = True,
) -> str:
    """
    Render a ParagonBoard using 2-char compact Unicode block characters (██, ▓▓, ·x, ░░) + stat letters and 24-bit ANSI colors.
    """
    RESET, BOLD, fg, bg = _ansi_styles(color)

    exits_active = ",".join(board.screen_active_exits()) or "None"
    rot_deg = board.rotation * 90
    purchased_cnt = len(board.purchased_nodes())
    path_cnt = purchased_cnt - len(board.target_nodes)
    title_c = f"{BOLD}{fg(255, 215, 100)}"
    sub_c = fg(185, 195, 215)
    border_c = fg(110, 125, 155)
    if board.glyph:
        g_cur = board.glyph_stat_in_radius()
        g_stat_str = f"{g_cur}/{board.glyph_threshold} {board.glyph_stat}, " if board.glyph_stat else ""
        glyph_str = f"{board.glyph_name} ({g_stat_str}R={board.glyph_radius})"
    else:
        glyph_str = "None"

    lines = [
        f"\n{title_c}╭─ {board.board_name} ({board.board_type_id}) ─ pos=({board.position['x']},{board.position['y']}) "
        f"─ rot={board.rotation} ({rot_deg}°){RESET}",
        f"{title_c}╰─{RESET} {sub_c}points={purchased_cnt} "
        f"({len(board.target_nodes)}T+{path_cnt}P) ─ exits={exits_active} ─ glyph={glyph_str}{RESET}",
    ]

    exit_arrow = {
        EXIT_COORDS[PARAGON_EXIT.TOP]: "▲",
        EXIT_COORDS[PARAGON_EXIT.RIGHT]: "►",
        EXIT_COORDS[PARAGON_EXIT.BOTTOM]: "▼",
        EXIT_COORDS[PARAGON_EXIT.LEFT]: "◄",
    }

    short_char = {
        NODE_TYPE.LEGENDARY: "L",
        NODE_TYPE.GLYPH: "G",
        NODE_TYPE.RARE: "R",
        NODE_TYPE.EXIT: "E",
        NODE_TYPE.MAGIC: "M",
        NODE_TYPE.MAGIC_DEX: "M",
        NODE_TYPE.MAGIC_INT: "M",
        NODE_TYPE.MAGIC_STR: "M",
        NODE_TYPE.MAGIC_WILL: "M",
        NODE_TYPE.DEX: "D",
        NODE_TYPE.INT: "I",
        NODE_TYPE.STR: "S",
        NODE_TYPE.WILL: "W",
    }

    lines.append("    " + "".join(f"{c:2d}" for c in range(board.size)))
    lines.append(f"   {border_c}┌" + "──" * board.size + f"┐{RESET}")
    for r in range(board.size):
        row_cells = []
        for c in range(board.size):
            coord = board.screen_to_unrotated(r, c)
            nt = board.grid_type[coord]
            in_rad = board.in_glyph_radius(coord)
            is_targ = coord in board.target_nodes
            is_sel = coord in board.selected_nodes
            is_start = board.is_starter_board() and coord == STARTER_START_COORD

            if nt == NODE_TYPE.DEAD:
                if in_rad:
                    row_cells.append(f"{bg(45, 18, 25)}{fg(115, 55, 65)}░░{RESET}" if color else "░░")
                else:
                    row_cells.append("  ")
                continue

            ch = short_char.get(nt, "?")
            if is_targ:
                if nt == NODE_TYPE.GLYPH:
                    st, sym = f"{BOLD}{bg(195, 25, 45)}{fg(255, 240, 180)}", "❖G"
                elif nt == NODE_TYPE.LEGENDARY:
                    st, sym = f"{BOLD}{bg(215, 95, 15)}{fg(255, 250, 220)}", "★L"
                elif nt == NODE_TYPE.RARE:
                    st, sym = f"{BOLD}{bg(190, 140, 15)}{fg(255, 255, 225)}", "◆R"
                elif nt.name.startswith("MAGIC"):
                    st, sym = f"{BOLD}{bg(35, 90, 185)}{fg(90, 165, 255)}", "◈M"
                elif nt == NODE_TYPE.DEX:
                    st, sym = f"{BOLD}{bg(25, 125, 70)}{fg(60, 215, 120)}", "█D"
                elif nt == NODE_TYPE.INT:
                    st, sym = f"{BOLD}{bg(20, 120, 155)}{fg(65, 210, 245)}", "█I"
                elif nt == NODE_TYPE.STR:
                    st, sym = f"{BOLD}{bg(155, 45, 45)}{fg(235, 95, 95)}", "█S"
                else:
                    st, sym = f"{BOLD}{bg(130, 85, 160)}{fg(200, 140, 240)}", f"█{ch}"
                row_cells.append(f"{st}{sym}{RESET}")
            elif is_sel:
                if is_start or nt == NODE_TYPE.EXIT:
                    arrow = "◎" if is_start else exit_arrow.get((r, c), "E")
                    st, sym = f"{BOLD}{bg(145, 40, 155)}{fg(255, 230, 255)}", f"⊞{arrow}"
                elif nt == NODE_TYPE.DEX:
                    st, sym = f"{BOLD}{bg(45, 85, 65)}{fg(130, 245, 175)}", "▓d"
                elif nt == NODE_TYPE.INT:
                    st, sym = f"{BOLD}{bg(40, 80, 105)}{fg(130, 225, 255)}", "▓i"
                elif nt == NODE_TYPE.STR:
                    st, sym = f"{BOLD}{bg(95, 50, 55)}{fg(255, 155, 155)}", "▓s"
                elif nt == NODE_TYPE.WILL:
                    st, sym = f"{BOLD}{bg(90, 75, 45)}{fg(255, 220, 130)}", "▓w"
                else:
                    st, sym = f"{BOLD}{bg(55, 75, 135)}{fg(170, 215, 255)}", f"▓{ch.lower()}"
                row_cells.append(f"{st}{sym}{RESET}")
            else:
                base_bg = bg(50, 20, 28) if in_rad else bg(24, 28, 36)
                st = f"{base_bg}{fg(80, 90, 108)}"
                sym = f"·{ch.lower()}"
                row_cells.append(f"{st}{sym}{RESET}" if color else sym)
        lines.append(f"{r:2d} {border_c}│{RESET}" + "".join(row_cells) + f"{border_c}│{RESET}")
    lines.append(f"   {border_c}└" + "──" * board.size + f"┘{RESET}")
    return "\n".join(lines)


def format_legend_unicode(color: bool = True) -> str:
    RESET, BOLD, fg, bg = _ansi_styles(color)
    return (
        f"\n   Legend: {BOLD}{bg(195,25,45)}{fg(255,240,180)}❖G{RESET} Glyph  "
        f"{BOLD}{bg(215,95,15)}{fg(255,250,220)}★L{RESET} Legendary  "
        f"{BOLD}{bg(190,140,15)}{fg(255,255,225)}◆R{RESET} Rare  "
        f"{BOLD}{bg(35,90,185)}{fg(225,240,255)}◈M{RESET} Magic\n"
        f"           {BOLD}{bg(25,125,70)}{fg(215,255,225)}█D{RESET} Target Stat  "
        f"{BOLD}{bg(45,85,65)}{fg(160,255,195)}▓d{RESET} Path Node  "
        f"{BOLD}{bg(145,40,155)}{fg(255,230,255)}⊞▲{RESET} Gate/Start  "
        f"{bg(45,18,25)}{fg(115,55,65)}░░{RESET} Glyph Radius"
    )


def format_board_ascii(board: ParagonBoard) -> str:
    lines = []
    exits_active = board.screen_active_exits()
    glyph_label = f"{board.glyph_name} ({board.glyph})" if (board.glyph and board.glyph_name != board.glyph) else (board.glyph or "None")
    lines.append(
        f"=== {board.board_name} ({board.board_type_id}) | pos=({board.position['x']},{board.position['y']}) "
        f"| rot={board.rotation} ({board.rotation * 90}°) | glyph={glyph_label} "
        f"| nodes={len(board.selected_nodes)} | exits={exits_active} ==="
    )
    type_char = {
        NODE_TYPE.DEAD: ".",
        NODE_TYPE.GLYPH: "G",
        NODE_TYPE.LEGENDARY: "L",
        NODE_TYPE.RARE: "R",
        NODE_TYPE.EXIT: "E",
        NODE_TYPE.MAGIC: "m",
        NODE_TYPE.MAGIC_DEX: "m",
        NODE_TYPE.MAGIC_INT: "m",
        NODE_TYPE.MAGIC_STR: "m",
        NODE_TYPE.MAGIC_WILL: "m",
        NODE_TYPE.DEX: "d",
        NODE_TYPE.INT: "i",
        NODE_TYPE.STR: "s",
        NODE_TYPE.WILL: "w",
    }
    for r in range(board.size):
        row = []
        for c in range(board.size):
            coord = board.screen_to_unrotated(r, c)
            nt = board.grid_type[coord]
            ch = type_char.get(nt, "n")
            if coord in board.target_nodes:
                row.append(f"[{ch.upper()}]")
            elif coord in board.selected_nodes:
                row.append(f"({ch.upper()})")
            else:
                row.append(f" {ch} ")
        lines.append("".join(row))
    return "\n".join(lines)


def print_summary(boards: list[ParagonBoard], file=sys.stderr):
    print("\nParagon Optimization Summary:", file=file)
    print("-" * 86, file=file)
    print(
        f"{'Order':<5} {'Board Name':<22} {'Pos':<8} {'Rot':<5} {'Targets':<8} {'Selected':<9} {'Glyph (Radius Stat)'}",
        file=file,
    )
    print("-" * 86, file=file)
    total_targets = 0
    total_selected = 0
    stat_counts = Counter()
    core_stat_totals = Counter({"DEX": 0, "INT": 0, "STR": 0, "WILL": 0})
    for idx, b in enumerate(boards):
        pos_str = f"({b.position['x']},{b.position['y']})"
        rot_str = f"{b.rotation * 90}°"
        if b.glyph:
            g_cur = b.glyph_stat_in_radius()
            g_str = f"{b.glyph_name} ({g_cur}/{b.glyph_threshold} {b.glyph_stat})" if b.glyph_stat else b.glyph_name
        else:
            g_str = "-"
        purchased_nodes = sorted(b.purchased_nodes())
        print(
            f"{idx + 1:<5} {b.board_name:<22} {pos_str:<8} {rot_str:<5} "
            f"{len(b.target_nodes):<8} {len(purchased_nodes):<9} {g_str}",
            file=file,
        )
        total_targets += len(b.target_nodes)
        total_selected += len(purchased_nodes)
        for coord in purchased_nodes:
            stat_counts[b.grid_type[coord].name] += 1
            for s_name, s_val in b.node_stat_grants(coord).items():
                core_stat_totals[s_name] += s_val
    print("-" * 86, file=file)
    print(
        f"{'TOTAL':<43} {total_targets:<8} {total_selected:<9} "
        f"(+{total_selected - total_targets} path/exit nodes)",
        file=file,
    )
    stats_str = ", ".join(f"{k}: {v}" for k, v in sorted(stat_counts.items()))
    core_str = ", ".join(f"+{core_stat_totals[k]} {k}" for k in ("DEX", "INT", "STR", "WILL") if core_stat_totals[k] > 0)
    print(f"Selected Node Breakdown: {stats_str}", file=file)
    print(f"Core Stat Grants (Normal +5 / Magic +7 / Rare +10 / Gate +5 All): {core_str}\n", file=file)
