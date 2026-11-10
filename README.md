# Diablo 4 Paragon Board Optimizer

The game Diablo 4 has a complex stat system called Paragon. You choose a
selection of up to 5 "Paragon Boards" from your class's set of 9–10 boards,
which can be rotated and connected in a mostly arbitrary arrangement.

The board is effectively a set of 150 or so nodes arranged in a non-uniform
grid. Each node provides some benefits, but you need to find a path through
the connected boards from the origin point to enable them.

The main challenge is connecting the most desirable nodes with the shortest
path. It's more complicated than that, but that's the gist of it.

Instead of just playing the game, I was nerdsniped into thinking about
optimization strategies. It turns out I'm not the first, other players have
tried to solve this problem. There is even an existing online solver [here](https://www.d4-paragon.com/).

However, I tried it out and could see just with my puny human brain that it
missed a lower cost path. Thinking about it, I recalled **Steiner Trees** and
wondered if that applied here and could improve the results.

I asked an AI to test this theory, and after some iterations it did produce
a working solution using Dreyfus-Wagner, and the results do seem far better,
often finding improved solutions.

## Features

- **Minimum Paragon Points**: Solves each board's Node-Weighted Steiner Tree.
- **Fast**: Native C++ Dreyfus–Wagner core.
- **Attribute Prioritization**: Flexible prioritization system for breaking ties between equal-length minimum paths.
- **Glyph**: Automatically calculates the Manhattan diamond radius around each board's Glyph sockets.
- **Hint System**: A suite of post-optimization ablation and neighborhood passes to give build tips.
- **Board Visualizer**: Displays board layout right in your terminal.

## Usage

This script reads the JSON format used by the
[maxroll.gg](https://maxroll.gg/d4/planner/) online Paragon planner tool.

Create a new build, then:

- Select all the boards, nodes and glyphs you want to use.
    - No need to worry about normal nodes, rotation or arrangement - this script
      will figure out how to connect them.
    - If you already have a complete build with normal nodes selected, that's
      fine too - this script can optimize it.
- Click the "Variant" menu, then the export icon. That will copy some JSON to the clipboard.
- Save that JSON to a file, and pass the resulting file to this tool and examine the result.
    - If you like it, use `-o optimized.json`, that output can be imported back
      into the planner tool.

### Quick Start

```bash
$ ./paragon_optimize.py tests/eternal_rogue_sample.json
```

If you like the result, and want to import the result back to the Maxroll planner:

```bash
$ ./paragon_optimize.py --output optimized.json tests/eternal_rogue_sample.json
```

If you already have a complete build and want to optimize it, try this:

```bash
$ ./paragon_optimize.py --relax-path-nodes tests/builds/Paladin_Arbiter_Endgame__Eternal_Non_Seasonal.json
```

The `--relax-path-nodes` option tells the script that you don't mind changing
normal nodes if it can find a shorter path.

This might find you an identical selection of special nodes with better class
stats, or enough spare paragon points to reach a new rare node.

In addition, the `--hints` option might be able to suggest minor tweaks that
could improve your build.

#### Examples

I tested this script across all public community builds from Maxroll, this
script found strictly cheaper paths on **57 out of 73 builds**, often freeing
enough paragon points to reach multiple rare/magic cluters.

Here is a closer look at a couple of examples:

1. **Rerouting a complete Maxroll build "Paladin Arbiter Endgame Eternal Non Seasonal"**:
   - Exported from a complete Maxroll guide with **323 Paragon points** allocated (taking 26 of the 28 Rare clusters across its 5 boards, and skipping the last 2, presumably because it ran out of points).
   - Running `./paragon_optimize.py --relax-path-nodes tests/builds/Paladin_Arbiter_Endgame__Eternal_Non_Seasonal.json` reroutes the connecting corridors without reducing any Glyph's in-radius stat, saving **11 Paragon points**:

```text
Relaxed 144 plain stat path node(s) not feeding a Glyph: 323 -> 312 Paragon points (-11 vs. without --relax-path-nodes); Core Stat change: -30 DEX, -11 INT, +30 STR, -38 WILL.
```

   - Those **11 freed points** are enough to grab one of the two skipped Rare nodes **and its entire 6-node Magic cluster**.

#### Advanced

If you're already thinking this is a ridiculous amount of optimization for a
video game stat system, get ready, we can go deeper!

##### Attribute Prioritization

If there are multiple paths that solve your board with equal cost, a tie
breaker strategy has to be used.

The default strategy is class-stat priority, whichever path maximizes the
stat your class prefers, i.e. `DEX`, `INT`, `STR`, `WILL`, wins.

The default will probably work, but you can tweak this if you need to.

```bash
$ ./paragon_optimize.py --prioritize-stat DEX your_d4_build.json
```

In addition to class-stat priority, the tool is also aware of secondary and
tertiary stats, and will weight those nodes accordingly.

For example, a Rogue might prefer `DEX`, but `STR` and `INT` are also useful,
but `WILL` might not be.

```bash
$ ./paragon_optimize.py --prioritize-stat DEX,STR,INT your_d4_build.json
```

There are even more prioritization options available, see the `--help` for more.

##### Glyph Optimization

Each board contains a "glyph" socket node which you can customize.

When a glyph is socketed, it can increase the value of surrounding nodes.

You can customize how those surrounding nodes are considered when pathing.

###### Maximize Glyph Stat

Some glyphs can take advantage of a specific stat, for example all `STR` nodes
in the glyph's manhattan circle radius become valuable.

In that case you want the script to find the minimum path to enable all the
applicable nodes, you can do that automatically like this:

```bash
$ ./paragon_optimize.py --glyph-strategy=max your_d4_build.json
```

###### Glyph Bonus Stat

Some glyphs have a valuable bonus that is enabled if a specific stat meets
some threshold in its radius. For example, at least +25 `INT`.

In that case, you might want the script to find the optimal way to activate the
bonus, but beyond that not to waste points on it.

```bash
$ ./paragon_optimize.py --glyph-strategy=bonus your_d4_build.json
```

###### Ignore Glyph Radius

If you do not care about the glyph radius, just tell the script to ignore it.

```bash
$ ./paragon_optimize.py --glyph-strategy=none your_d4_build.json
```

There are additional options and strategies, see the `--help` for more.

##### Per-Glyph Strategy

You might want different strategies for each socketed glyph.

Just specify the Glyph name, followed by the strategy to use.

```bash
./paragon_optimize.py --glyph-strategy=Headhunter=max,Nightstalker=bonus your_d4_build.json
```

You can also set a default strategy, and just change it for one glyph

```bash
./paragon_optimize.py --glyph-strategy=max,Nightstalker=bonus your_d4_build.json
```

#### Recalculate Path Nodes

When you export a finished build from Maxroll, every normal `+5` stat node along
your path is marked as selected in the JSON. By default, the solver preserves all
of them.

Passing `--relax-path-nodes` tells the script to treat plain `+5` stat nodes outside
your Glyph radii as optional path nodes so it can search for cheaper connecting
routes (and prints the net point and Core Stat difference):

```bash
./paragon_optimize.py --relax-path-nodes your_d4_build.json
```

You can also combine `--relax-path-nodes` with `--glyph-strategy` (for example,
`--relax-path-nodes --glyph-strategy=bonus,Headhunter=max`) to re-optimize the
normal stat nodes inside your Glyph radii at the same time.

#### Point Budgets

By default, the optimizer enforces the *Diablo 4* maximum cap of **342 Paragon
points**. You can pass `--budget N` if your character has fewer points available.

```bash
$ ./paragon_optimize.py --budget 225 tests/eternal_rogue_sample.json
```

When the optimal solution exceeds the budget, the script attempts to trim less
valuable nodes until the solution fits. This might not always be possible.

#### Hint System

Once solved, it's possible to perform a number of checks on the final board
that might help improve your build.

For example, a marginal cost analysis might reveal that a significant stat
boost is available for just 1 additional paragon point.

Enable these hints using `--hints`:

```bash
./paragon_optimize.py --hints your_d4_build.json
```

The checks currently available include (see [HINTS.md](HINTS.md) for full details):

- **Glyph Threshold Unmet**: Minimum points needed to activate an unmet glyph bonus.
- **Glyph Radius Breakpoint**: Extra stats your path gets for free when a glyph reaches Lv. 15 or Lv. 46.
- **Secondary Glyph Over-Invested**: Points wasted stacking a secondary-stat glyph past its `+25` threshold.
- **In-Radius 1-Pt Upgrade**: Unselected stat or `+7` Magic nodes in a glyph radius directly adjacent to your path.
- **Inefficient Stat Target**: Stat nodes requiring 2+ point detours, with cheaper adjacent swap suggestions.
- **High-Cost Spurs & Clusters**: Point cost of isolated Rare/Legendary spurs and trimmable Magic cluster leaves.
- **Board Cost**: True net point savings if an entire board is dropped.
- **Nearby Rare & Legendary Nodes**: Unselected Rare or Legendary nodes reachable for 0–2 points.
- **Glyph Socket Swap**: Cross-board glyph swaps that give higher scaling for 0 points.

## JSON Format

The tool uses the maxroll planner JSON format, where each element represents a board:

```json
[
  {
    "id": "Paragon_Rogue_00",
    "nodes": {
      "72": 1,
      "74": 1,
      "136": 1
    },
    "rotation": 0,
    "position": {"x": 0, "y": 0},
    "glyph": "Rare_056_Dexterity_Main",
    "glyphLevel": 150
  }
]
```
