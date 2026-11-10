"""
Diablo 4 ParagonBoard model, coordinate rotation, stat grants, and node priority weighting.
"""

from collections import deque

from .game_data import (
    BOARD_NAMES,
    BOARD_SPECIAL_NODES,
    D4_CLASS,
    DEFAULT_CLASS_NODE_PRIORITY,
    EXIT_COORDS,
    GLYPH_DATA,
    MAGIC_STAT_GRANT,
    NODE_TYPE,
    NORMAL_STAT_GRANT,
    PARAGON_EXIT,
    RARE_STAT_GRANT,
    STARTER_START_COORD,
    STAT_ALIASES,
    get_glyph_radius,
    get_glyph_stat_types,
)

PLAIN_STAT_TYPES = (NODE_TYPE.DEX, NODE_TYPE.INT, NODE_TYPE.STR, NODE_TYPE.WILL)
MAGIC_STAT_TYPES = (NODE_TYPE.MAGIC_DEX, NODE_TYPE.MAGIC_INT, NODE_TYPE.MAGIC_STR, NODE_TYPE.MAGIC_WILL)


def neighbors4(coord: tuple[int, int]) -> tuple[tuple[int, int], tuple[int, int], tuple[int, int], tuple[int, int]]:
    """Return the 4 orthogonal grid neighbors of `(r, c)`."""
    r, c = coord
    return ((r + 1, c), (r - 1, c), (r, c + 1), (r, c - 1))


def grid_components(coords: set[tuple[int, int]]) -> list[set[tuple[int, int]]]:
    """Return connected components of `coords` under 4-neighbor grid adjacency in canonical sorted order."""
    vis: set[tuple[int, int]] = set()
    comps: list[set[tuple[int, int]]] = []
    for start in sorted(coords):
        if start in vis:
            continue
        comp: set[tuple[int, int]] = set()
        q = deque([start])
        vis.add(start)
        while q:
            u = q.popleft()
            comp.add(u)
            for nb in neighbors4(u):
                if nb in coords and nb not in vis:
                    vis.add(nb)
                    q.append(nb)
        comps.append(comp)
    return comps


def is_grid_connected(coords: set[tuple[int, int]]) -> bool:
    """Return True if `coords` is empty or forms a single 4-connected grid component."""
    return len(grid_components(coords)) <= 1

# Scaling factor ensuring total priority across all <= 441 board nodes is strictly < 1.0,
# so minimum node count is mathematically guaranteed to be the primary objective.
PRIORITY_SCALE = 100000.0

GLYPH_STRATEGY_ALIASES: dict[str, str] = {
    "preserve": "preserve",
    "default": "preserve",
    "tiebreak": "preserve",
    "none": "none",
    "off": "none",
    "bonus": "bonus",
    "max": "max",
    "all": "max",
}


def _norm_glyph_key(name: str) -> str:
    return " ".join(name.strip().lower().replace("_", " ").replace("-", " ").split())


KNOWN_GLYPH_KEYS: set[str] = (
    {_norm_glyph_key(gid) for gid in GLYPH_DATA}
    | {gid.strip().lower() for gid in GLYPH_DATA}
    | {_norm_glyph_key(info[0]) for info in GLYPH_DATA.values()}
)


def parse_glyph_strategy(
    specs: str | list[str] | tuple[str, dict[str, str]] | dict[str, str] | None,
    extra_glyph_names: set[str] | None = None,
) -> tuple[str, dict[str, str]]:
    """
    Parse `--glyph-strategy` specifications into `(default_strategy, per_glyph_overrides)`.
    Examples:
      - `None` -> `("preserve", {})`
      - `"bonus"` -> `("bonus", {})`
      - `"bonus,Headhunter=max"` -> `("bonus", {"headhunter": "max"})`
      - `"Ranger=bonus,Headhunter=none,Nightstalker=max"` -> `("preserve", {...})`
    """
    if specs is None:
        return ("preserve", {})
    if (
        isinstance(specs, tuple)
        and len(specs) == 2
        and isinstance(specs[0], str)
        and isinstance(specs[1], dict)
    ):
        return specs
    if isinstance(specs, dict):
        specs_list = [f"{k}={v}" for k, v in specs.items()]
    elif isinstance(specs, str):
        specs_list = [specs]
    else:
        specs_list = list(specs)

    valid_glyphs = KNOWN_GLYPH_KEYS
    if extra_glyph_names:
        valid_glyphs = valid_glyphs | {_norm_glyph_key(g) for g in extra_glyph_names if g} | {
            g.strip().lower() for g in extra_glyph_names if g
        }

    default_strategy = "preserve"
    overrides: dict[str, str] = {}

    for item in specs_list:
        for raw_token in item.split(","):
            token = raw_token.strip()
            if not token:
                continue
            if "=" not in token:
                key_low = token.lower()
                if key_low not in GLYPH_STRATEGY_ALIASES:
                    raise ValueError(
                        f"Unknown glyph strategy '{token}'. Valid strategies: bonus, max, none, preserve."
                    )
                default_strategy = GLYPH_STRATEGY_ALIASES[key_low]
            else:
                g_raw, s_raw = token.split("=", 1)
                g_clean = g_raw.strip()
                s_low = s_raw.strip().lower()
                if not g_clean:
                    raise ValueError(f"Empty glyph name in strategy token '{token}'.")
                if s_low not in GLYPH_STRATEGY_ALIASES:
                    raise ValueError(
                        f"Unknown glyph strategy '{s_raw.strip()}' for '{g_clean}'. "
                        f"Valid strategies: bonus, max, none, preserve."
                    )
                strat = GLYPH_STRATEGY_ALIASES[s_low]
                if g_clean.lower() in ("*", "default"):
                    default_strategy = strat
                else:
                    norm_g = _norm_glyph_key(g_clean)
                    raw_g = g_clean.lower()
                    if norm_g not in valid_glyphs and raw_g not in valid_glyphs:
                        raise ValueError(
                            f"Unknown glyph '{g_clean}' in --glyph-strategy. "
                            f"Use an in-game Glyph name (e.g. 'Ranger', 'Headhunter', 'Nightstalker') or Glyph ID."
                        )
                    overrides[norm_g] = strat
                    overrides[raw_g] = strat

    return (default_strategy, overrides)


def resolve_glyph_strategy(
    glyph_id: str,
    glyph_name: str,
    specs: str | list[str] | tuple[str, dict[str, str]] | dict[str, str] | None,
) -> str:
    """Resolve the active strategy (`'preserve'`, `'none'`, `'bonus'`, or `'max'`) for a board's socketed Glyph."""
    default_strategy, overrides = parse_glyph_strategy(
        specs, extra_glyph_names={glyph_id, glyph_name} if (glyph_id or glyph_name) else None
    )
    if not overrides or (not glyph_id and not glyph_name):
        return default_strategy
    for candidate in (glyph_id.strip().lower(), _norm_glyph_key(glyph_id), _norm_glyph_key(glyph_name)):
        if candidate and candidate in overrides:
            return overrides[candidate]
    return default_strategy


class ParagonBoard:
    def __init__(
        self,
        data: dict,
        meta_data: dict,
        prioritize_stats: list[str] | None = None,
        glyph_strategy: str | list[str] | tuple[str, dict[str, str]] | dict[str, str] | None = None,
        relax_path_nodes: bool = False,
    ):
        if "id" not in data or "nodes" not in data:
            raise ValueError("Invalid paragon board JSON: missing 'id' or 'nodes'.")
        self.size = 21
        self.raw_id = data["id"]
        self.board_type_id = "Paragon_Spirit_00" if self.raw_id == "Paragon_Spirit_0" else self.raw_id
        self.board_name = BOARD_NAMES.get(self.board_type_id, self.board_type_id)
        self.d4_class = D4_CLASS(self.board_type_id.split("_")[1])
        self.rotation = data.get("rotation", 0)
        self.position = dict(data.get("position", {"x": 0, "y": 0}))
        self.glyph = data.get("glyph", "")
        self.glyph_level = int(data.get("glyphLevel", 1))
        self.has_glyph_key = "glyph" in data
        self.has_glyph_level_key = "glyphLevel" in data

        if self.board_type_id not in meta_data:
            raise ValueError(f"Unknown board ID '{self.board_type_id}' in paragon metadata.")
        board_meta = meta_data[self.board_type_id]

        self.grid_type: dict[tuple[int, int], NODE_TYPE] = {}
        self.glyph_coord: tuple[int, int] | None = None
        for r in range(self.size):
            for c in range(self.size):
                idx_str = str(r * self.size + c)
                nt = NODE_TYPE[board_meta.get(idx_str, "DEAD")]
                self.grid_type[(r, c)] = nt
                if nt == NODE_TYPE.GLYPH:
                    self.glyph_coord = (r, c)

        # Build node priority table for this board (deduplicating normalized stat aliases)
        self.priorities = dict(DEFAULT_CLASS_NODE_PRIORITY[self.d4_class])
        if prioritize_stats:
            deduped_groups: list[tuple[NODE_TYPE, ...]] = []
            seen_groups: set[tuple[NODE_TYPE, ...]] = set()
            for stat_str in prioritize_stats:
                key = stat_str.strip().upper()
                if key not in STAT_ALIASES:
                    raise ValueError(
                        f"Unknown stat '{stat_str}'. Valid choices: {', '.join(STAT_ALIASES.keys())}"
                    )
                grp = STAT_ALIASES[key]
                if grp not in seen_groups:
                    seen_groups.add(grp)
                    deduped_groups.append(grp)
            for rank, grp in enumerate(reversed(deduped_groups)):
                boost_base = 1.2 * (3.5 ** rank)
                for nt in grp:
                    if nt.name.startswith("MAGIC_"):
                        # Magic stat nodes grant +7 stat vs +5 for Normal stat nodes (exact 1.4x ratio)
                        self.priorities[nt] = boost_base * (MAGIC_STAT_GRANT / NORMAL_STAT_GRANT)
                    else:
                        self.priorities[nt] = boost_base
            # Normalize tiered weights if many stat groups are passed so board-wide discount sum stays << 1.0
            max_p = max(self.priorities.values(), default=0.0)
            if max_p > 50.0:
                scale = 50.0 / max_p
                for nt in self.priorities:
                    self.priorities[nt] *= scale

        self.special_nodes = BOARD_SPECIAL_NODES.get(self.board_type_id, {})
        self.glyph_radius = get_glyph_radius(self.glyph, self.glyph_level)
        self.glyph_stat_types = get_glyph_stat_types(self.glyph)
        if self.glyph in GLYPH_DATA:
            self.glyph_name, self.glyph_stat, self.glyph_threshold, self.glyph_mode = GLYPH_DATA[self.glyph]
        else:
            self.glyph_name = self.glyph
            self.glyph_stat = self.glyph_stat_types[0].name if self.glyph_stat_types else None
            self.glyph_threshold = 25 if "_side" in self.glyph.lower() else (40 if self.glyph else 0)
            self.glyph_mode = "stat"
        self.glyph_strategy = resolve_glyph_strategy(self.glyph, self.glyph_name, glyph_strategy)
        self.glyph_bonus = self.glyph_strategy != "none"

        # Determine target nodes from input JSON (ignoring perimeter exit gates and starter root gate)
        exit_coords_set = set(EXIT_COORDS.values())
        if self.is_starter_board():
            exit_coords_set.add(STARTER_START_COORD)
        self.target_nodes: set[tuple[int, int]] = set()
        # Every node marked in the input (including gates), used to report changes vs. the input build
        self.input_nodes: set[tuple[int, int]] = set()
        for idx_str, val in data["nodes"].items():
            if val != 1:
                continue
            idx = int(idx_str)
            if idx < 0 or idx >= self.size * self.size:
                raise ValueError(f"Node index {idx} out of range on board {self.board_type_id}.")
            coord = (idx // self.size, idx % self.size)
            if self.grid_type[coord] == NODE_TYPE.DEAD:
                raise ValueError(f"Required target node {idx} {coord} is DEAD on board {self.board_type_id}.")
            self.input_nodes.add(coord)
            if coord not in exit_coords_set:
                self.target_nodes.add(coord)

        # If a glyph is socketed on this board, ensure the GLYPH socket itself is a target
        if self.glyph and self.glyph_coord is not None:
            self.target_nodes.add(self.glyph_coord)

        self.auto_glyph_nodes = self.glyph_strategy == "max"
        self.auto_glyph_bonus = self.glyph_strategy in ("bonus", "max")
        self.auto_glyph_bonus_nodes: set[tuple[int, int]] = set()

        # Optionally treat plain +5 stat nodes as optional path nodes rather than mandatory targets
        # (d4-paragon.com exports mark every purchased path node).
        # - For `preserve` (default) or `max`, nodes that feed the socketed Glyph stay mandatory so the
        #   Glyph's in-radius stat can never decrease.
        # - For `bonus` or `none`, in-radius plain +5 stat nodes are also relaxed so the solver pares
        #   `bonus` Glyphs down to their minimum-point threshold selection and `none` Glyphs down to
        #   their minimum connecting path.
        self.relaxed_nodes: set[tuple[int, int]] = set()
        if relax_path_nodes:
            keep_glyph_stat = self.glyph_strategy in ("preserve", "max")
            for coord in list(self.target_nodes):
                if self.grid_type[coord] in PLAIN_STAT_TYPES and not (keep_glyph_stat and self.feeds_glyph(coord)):
                    self.target_nodes.discard(coord)
                    self.relaxed_nodes.add(coord)

        # For `max` strategy, auto-select all matching stat nodes inside the glyph's Manhattan radius
        if self.auto_glyph_nodes and self.glyph and self.glyph_coord is not None and self.glyph_stat_types:
            for coord, nt in self.grid_type.items():
                if self.in_glyph_radius(coord) and nt in self.glyph_stat_types:
                    self.target_nodes.add(coord)
                    self.relaxed_nodes.discard(coord)

        self.selected_nodes: set[tuple[int, int]] = set()
        self.entry_gate_coord: tuple[int, int] | None = (
            STARTER_START_COORD if self.is_starter_board() else None
        )
        # Exact per-exit-mask Steiner solutions cached by `solver.optimize_layout_and_paths`
        self.exit_solutions: dict[tuple, object] = {}

    def is_starter_board(self) -> bool:
        return self.board_type_id.endswith("00")

    def class_primary_stat(self) -> str:
        """Return this board's intrinsic class primary stat (`DEX`, `INT`, `STR`, or `WILL`)."""
        base_prios = DEFAULT_CLASS_NODE_PRIORITY[self.d4_class]
        return max(("DEX", "INT", "STR", "WILL"), key=lambda s: base_prios.get(NODE_TYPE[s], 0.0))

    def feeds_glyph(self, coord: tuple[int, int]) -> bool:
        """Return True if `coord` is inside the active Glyph radius and contributes to the Glyph's stat/normal mode."""
        return self.in_glyph_radius(coord) and (
            self.grid_type[coord] in self.glyph_stat_types or self.glyph_mode == "normal"
        )

    def active_exit_coords(self, nodes_set: set[tuple[int, int]] | None = None) -> set[tuple[int, int]]:
        """Return the set of perimeter exit gate coordinates present in `nodes_set` (or `self.selected_nodes`)."""
        nodes = self.selected_nodes if nodes_set is None else nodes_set
        return {c for c in EXIT_COORDS.values() if c in nodes}

    def required_terminals(
        self,
        targets: set[tuple[int, int]] | None = None,
        nodes_set: set[tuple[int, int]] | None = None,
    ) -> set[tuple[int, int]]:
        """Return all mandatory terminal coordinates (`targets` + active exit gates + starter start node)."""
        base_t = self.target_nodes if targets is None else targets
        req = set(base_t) | self.active_exit_coords(nodes_set)
        if self.is_starter_board():
            req.add(STARTER_START_COORD)
        return req

    def free_coords(self, nodes_set: set[tuple[int, int]] | None = None) -> set[tuple[int, int]]:
        """
        Return the free (0-point) root/entry coordinate(s) in `nodes_set`:
          - Starter Board: the free Start Node at `(14, 10)` (`STARTER_START_COORD`).
          - Non-Starter Board: the incoming attachment gate (`EXIT`) unlocked for free when
            the parent board's outgoing Board Attachment Gate is purchased.
        """
        nodes = self.selected_nodes if nodes_set is None else nodes_set
        if self.is_starter_board():
            return {STARTER_START_COORD} & nodes
        if self.entry_gate_coord is not None and self.entry_gate_coord in nodes:
            return {self.entry_gate_coord}
        exits_in = self.active_exit_coords(nodes)
        return {min(exits_in)} if exits_in else set()

    def purchased_nodes(self, nodes_set: set[tuple[int, int]] | None = None) -> set[tuple[int, int]]:
        """Return the subset of `nodes_set` that cost Paragon points (excluding free start/entry gates)."""
        nodes = self.selected_nodes if nodes_set is None else nodes_set
        return nodes - self.free_coords(nodes)

    def _rotate_coord(self, r: int, c: int, rot_steps: int) -> tuple[int, int]:
        rot = rot_steps % 4
        n = self.size - 1
        if rot == 1:  # 90° clockwise
            return (c, n - r)
        if rot == 2:  # 180° clockwise
            return (n - r, n - c)
        if rot == 3:  # 270° clockwise
            return (n - c, r)
        return (r, c)

    def screen_to_unrotated(self, sr: int, sc: int) -> tuple[int, int]:
        """Map a rotated screen-space coordinate (sr, sc) back to unrotated board coordinate (ur, uc)."""
        return self._rotate_coord(sr, sc, -self.rotation)

    def unrotated_to_screen(self, ur: int, uc: int) -> tuple[int, int]:
        """Map an unrotated board coordinate (ur, uc) to rotated screen-space coordinate (sr, sc)."""
        return self._rotate_coord(ur, uc, self.rotation)

    def screen_active_exits(self) -> list[str]:
        """Return active exit gate names in rotated in-game screen orientation."""
        return [
            s_dir.name
            for s_dir, (sr, sc) in EXIT_COORDS.items()
            if self.screen_to_unrotated(sr, sc) in self.selected_nodes
        ]

    def in_glyph_radius(self, coord: tuple[int, int]) -> bool:
        if not self.glyph or self.glyph_coord is None or self.glyph_radius <= 0:
            return False
        return abs(coord[0] - self.glyph_coord[0]) + abs(coord[1] - self.glyph_coord[1]) <= self.glyph_radius

    def node_stat_grants(self, coord: tuple[int, int]) -> dict[str, int]:
        """Return exact Core Stat points granted by purchasing `coord` (from d4data)."""
        nt = self.grid_type[coord]
        if nt in PLAIN_STAT_TYPES:
            return {nt.name: NORMAL_STAT_GRANT}
        if nt in MAGIC_STAT_TYPES:
            return {nt.name.split("_")[1]: MAGIC_STAT_GRANT}
        if nt == NODE_TYPE.RARE:
            info = self.special_nodes.get(coord)
            return {info[1]: RARE_STAT_GRANT} if (info and info[1]) else {}
        if nt == NODE_TYPE.EXIT and not (self.is_starter_board() and coord == STARTER_START_COORD):
            return {s.name: NORMAL_STAT_GRANT for s in PLAIN_STAT_TYPES}
        return {}

    def node_stat_grant_for(self, coord: tuple[int, int], stat: str) -> int:
        """Return Core Stat points granted by `coord` toward `stat` (or sum of all Core Stats if `stat == 'ALL'`)."""
        grants = self.node_stat_grants(coord)
        if stat == "ALL":
            return sum(grants.values())
        return grants.get(stat, 0)

    def node_label(self, coord: tuple[int, int]) -> str:
        """Return a descriptive human-readable label for `coord` including name and stat grant."""
        nt = self.grid_type[coord]
        info = self.special_nodes.get(coord)
        if nt == NODE_TYPE.LEGENDARY:
            name = info[0] if info else self.board_name
            return f"Legendary '{name}'"
        if nt == NODE_TYPE.RARE:
            name = info[0] if info else "Rare"
            if info and info[1]:
                return f"Rare '{name}' (+{RARE_STAT_GRANT} {info[1]})"
            return f"Rare '{name}'"
        if nt in MAGIC_STAT_TYPES:
            stat = nt.name.split("_")[1]
            return f"{nt.name} (+{MAGIC_STAT_GRANT} {stat})"
        if nt in PLAIN_STAT_TYPES:
            return f"{nt.name} (+{NORMAL_STAT_GRANT} {nt.name})"
        if nt == NODE_TYPE.EXIT:
            return f"Gate (+{NORMAL_STAT_GRANT} All Stats)"
        return nt.name

    def glyph_stat_in_radius(
        self,
        nodes_set: set[tuple[int, int]] | None = None,
        radius: int | None = None,
    ) -> int:
        """Calculate total governed core stat points purchased inside the socketed Glyph's radius."""
        if not self.glyph or not self.glyph_stat or self.glyph_coord is None:
            return 0
        nodes = self.selected_nodes if nodes_set is None else nodes_set
        rad = self.glyph_radius if radius is None else radius
        if rad <= 0:
            return 0
        gr, gc = self.glyph_coord
        return sum(
            self.node_stat_grant_for(c, self.glyph_stat)
            for c in nodes
            if abs(c[0] - gr) + abs(c[1] - gc) <= rad
        )

    def node_priority(self, coord: tuple[int, int]) -> float:
        nt = self.grid_type[coord]
        prio = self.priorities.get(nt, 0.0)
        # If this is a Rare node granting +10 Core Stat, add a small tie-breaker proportional to that stat's priority
        if nt == NODE_TYPE.RARE:
            info = self.special_nodes.get(coord)
            if info and info[1]:
                prio += 0.25 * self.priorities.get(NODE_TYPE[info[1]], 0.0)

        # Boost matching nodes inside the active glyph radius using exact d4data stat ratios and glyph mode
        if self.glyph_bonus and self.in_glyph_radius(coord):
            if nt in self.glyph_stat_types:
                is_magic = nt.name.startswith("MAGIC_")
                if self.glyph_mode == "normal":
                    # Normal-node-boosting glyphs (e.g. Headhunter, Eliminator, Challenger) give +200%+ to normal nodes
                    prio += 0.35 if is_magic else 0.50
                else:
                    # MAGIC_<STAT> grants +7 vs +5 for normal <STAT> (1.4x scaling & threshold contribution)
                    prio += 0.49 if is_magic else 0.35
                    if is_magic and self.glyph_mode == "magic":
                        prio += 0.15
            elif self.glyph_mode == "normal" and nt in PLAIN_STAT_TYPES:
                prio += 0.20
            elif nt == NODE_TYPE.MAGIC or nt in MAGIC_STAT_TYPES:
                prio += 0.50 if self.glyph_mode == "magic" else 0.15
            elif nt == NODE_TYPE.RARE:
                prio += 0.55 if self.glyph_mode == "rare" else 0.15
                info = self.special_nodes.get(coord)
                if info and info[1] and (info[1] == self.glyph_stat or self.glyph_stat == "ALL"):
                    prio += 0.70
        return prio

    def node_weight(self, coord: tuple[int, int], base_terms: set[tuple[int, int]]) -> float:
        """
        Weight of adding `coord` to the Steiner tree:
          0.0 if already a required target node,
          else 1.0 - priority / PRIORITY_SCALE (strictly > 0, with sum of all discounts < 1.0).
        """
        if coord in base_terms:
            return 0.0
        return 1.0 - (self.node_priority(coord) / PRIORITY_SCALE)

    def export_data(self) -> dict:
        out_id = "Paragon_Spirit_0" if self.board_type_id == "Paragon_Spirit_00" else self.board_type_id
        output = {
            "id": out_id,
            "nodes": {},
            "rotation": self.rotation,
            "position": self.position,
        }
        if self.has_glyph_key or self.glyph:
            output["glyph"] = self.glyph
        if self.has_glyph_level_key or self.glyph:
            output["glyphLevel"] = self.glyph_level

        for r in range(self.size):
            for c in range(self.size):
                if (r, c) in self.selected_nodes and not (
                    self.is_starter_board() and (r, c) == STARTER_START_COORD
                ):
                    output["nodes"][str(r * self.size + c)] = 1
        return output


def unwrap_paragon_boards(raw_input) -> list[dict]:
    """
    Extract the list of Paragon board dicts from either a direct board list or a full
    Maxroll build JSON export (selecting `.data.profiles[activeProfile].paragon.steps[position].data`
    by default, and falling back to the first non-empty profile/step if the active profile has 0 nodes).
    """
    def extract_step_data(par):
        if isinstance(par, dict) and "steps" in par and par["steps"]:
            steps = par["steps"]
            pos = par.get("position", 0)
            if not isinstance(pos, int) or not (0 <= pos < len(steps)):
                pos = 0
            return steps[pos].get("data", par)
        return par

    def has_nodes(boards_list) -> bool:
        return isinstance(boards_list, list) and any(
            isinstance(b, dict) and (b.get("glyph") or any(v == 1 for v in b.get("nodes", {}).values()))
            for b in boards_list
        )

    obj = raw_input.get("data", raw_input) if isinstance(raw_input, dict) else raw_input
    if isinstance(obj, dict) and "profiles" in obj and obj["profiles"]:
        profs = obj["profiles"]
        ap = obj.get("activeProfile", 0)
        if not isinstance(ap, int) or not (0 <= ap < len(profs)):
            ap = 0
        cand = extract_step_data(profs[ap].get("paragon", profs[ap])) if isinstance(profs[ap], dict) else profs[ap]
        if not has_nodes(cand):
            for p in profs:
                if not isinstance(p, dict):
                    continue
                c2 = extract_step_data(p.get("paragon", p))
                if has_nodes(c2):
                    cand = c2
                    break
                par = p.get("paragon")
                if isinstance(par, dict):
                    for st in par.get("steps", []):
                        if isinstance(st, dict) and has_nodes(st.get("data")):
                            cand = st["data"]
                            break
                    if has_nodes(cand):
                        break
        obj = cand
    elif isinstance(obj, dict):
        obj = extract_step_data(obj)
    if not isinstance(obj, list):
        raise ValueError("Input JSON must be a list of Paragon boards or a Maxroll build JSON.")
    return obj


def unwrap_all_paragon_configs(raw_input) -> list[tuple[str, list[dict]]]:
    """
    Extract all unique non-empty `(label, board_list)` Paragon configurations across all
    profiles (`.data.profiles[*]`) and steps (`.paragon.steps[*]`) in a Maxroll build JSON
    (or `[("default", raw_input)]` if given a direct list of boards).
    """
    if isinstance(raw_input, list):
        return [("default", raw_input)]
    if not isinstance(raw_input, dict):
        raise ValueError("Input JSON must be a list of Paragon boards or a Maxroll build JSON.")
    data_obj = raw_input.get("data", raw_input)
    if not isinstance(data_obj, dict) or "profiles" not in data_obj:
        return [("default", unwrap_paragon_boards(raw_input))]

    configs: list[tuple[str, list[dict]]] = []
    seen: list[list[dict]] = []
    for pi, prof in enumerate(data_obj.get("profiles", [])):
        if not isinstance(prof, dict):
            continue
        p_name = prof.get("name", f"profile[{pi}]")
        par = prof.get("paragon")
        if not par:
            continue
        if isinstance(par, list) and par:
            if par not in seen:
                seen.append(par)
                configs.append((f"profiles[{pi}] ({p_name})", par))
        elif isinstance(par, dict):
            for si, st in enumerate(par.get("steps", [])):
                if not isinstance(st, dict):
                    continue
                s_name = st.get("name", f"steps[{si}]")
                step_data = st.get("data")
                if isinstance(step_data, list) and step_data:
                    if step_data not in seen:
                        seen.append(step_data)
                        configs.append((f"profiles[{pi}].steps[{si}] ({p_name} / {s_name})", step_data))
    return configs
