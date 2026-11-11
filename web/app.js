// Main UI Controller for Diablo 4 Paragon Board Optimizer Web Application

(() => {
  // Node visual palette matching terminal Unicode visualizer & D4 aesthetics
  const NODE_PALETTE = {
    START:     { fill: "#f0c674", text: "#111", symbol: "S", name: "Start Node" },
    EXIT:      { fill: "#56c8d8", text: "#111", symbol: "E", name: "Exit Gate" },
    LEGENDARY: { fill: "#ff9933", text: "#111", symbol: "L", name: "Legendary" },
    GLYPH:     { fill: "#b47ede", text: "#111", symbol: "G", name: "Glyph Socket" },
    RARE:      { fill: "#ffd700", text: "#111", symbol: "R", name: "Rare Node" },
    MAGIC:     { fill: "#5294e2", text: "#fff", symbol: "M", name: "Magic Node" },
    MAGIC_DEX: { fill: "#48b685", text: "#111", symbol: "M", name: "Magic Dex" },
    MAGIC_INT: { fill: "#6ba4e8", text: "#111", symbol: "M", name: "Magic Int" },
    MAGIC_STR: { fill: "#e06c75", text: "#111", symbol: "M", name: "Magic Str" },
    MAGIC_WILL:{ fill: "#56b6c2", text: "#111", symbol: "M", name: "Magic Will" },
    DEX:       { fill: "#3ca365", text: "#111", symbol: "d", name: "Dexterity" },
    INT:       { fill: "#d9b23f", text: "#111", symbol: "i", name: "Intelligence" },
    STR:       { fill: "#d15454", text: "#fff", symbol: "s", name: "Strength" },
    WILL:      { fill: "#47a8b5", text: "#111", symbol: "w", name: "Willpower" },
  };

  const CLASS_DEFAULT_STACK = {
    BARB:    ["RARE", "MAGIC", "STR", "DEX", "WILL", "INT"],
    DRUID:   ["RARE", "MAGIC", "WILL", "DEX", "INT", "STR"],
    NECRO:   ["RARE", "MAGIC", "INT", "DEX", "WILL", "STR"],
    ROGUE:   ["RARE", "MAGIC", "DEX", "INT", "STR", "WILL"],
    SORC:    ["RARE", "MAGIC", "INT", "DEX", "WILL", "STR"],
    SPIRIT:  ["RARE", "MAGIC", "DEX", "STR", "INT", "WILL"],
    PALADIN: ["RARE", "MAGIC", "STR", "INT", "WILL", "DEX"],
    WARLOCK: ["RARE", "MAGIC", "WILL", "STR", "INT", "DEX"],
  };

  const STAT_ITEM_META = {
    RARE:  { label: "RARE (Rare Nodes)", color: "#ffd700" },
    MAGIC: { label: "MAGIC (Magic Nodes)", color: "#5294e2" },
    DEX:   { label: "DEX (Dexterity)", color: "#3ca365" },
    INT:   { label: "INT (Intelligence)", color: "#d9b23f" },
    STR:   { label: "STR (Strength)", color: "#d15454" },
    WILL:  { label: "WILL (Willpower)", color: "#47a8b5" },
  };

  const GLYPH_STRATEGY_DESCRIPTIONS = {
    default: "Uses the global Glyph Strategy selected in the left sidebar.",
    preserve:
      "Leaves your selected stat nodes around each Glyph untouched (shown with a white border) and only optimizes the connecting paths between them.",
    bonus:
      "Selects only the minimum number of nodes in the Glyph radius needed to activate the Glyph's secondary bonus (e.g., 25 or 40 stat), freeing excess radius points.",
    max: "Selects every matching stat node inside the Glyph radius to maximize the Glyph's additive/multiplicative scaling.",
    none: "Treats Glyph radius stat nodes like normal path nodes, routing only the shortest path to the Glyph socket without targeting radius bonuses.",
  };

  const GLYPH_STRATEGY_OPTIONS = [
    ["default", "Use Global Strategy"],
    ["preserve", "Keep Existing Glyph Nodes"],
    ["bonus", "Reach Bonus Threshold"],
    ["max", "Maximize Bonus Stat"],
    ["none", "Ignore Glyph Radius"],
  ];

  // Application State
  const state = {
    rawJsonStr: "",
    activeSampleKey: "eternal_rogue",
    profileIndex: 0,
    globalGlyphStrategy: "preserve",
    perGlyphOverrides: {}, // glyphName -> strategy
    relaxPathNodes: true,
    currentClass: "ROGUE",
    prioritizeStats: [...CLASS_DEFAULT_STACK.ROGUE],
    statOrderCustomized: false,
    budget: 342,
    enableHints: true,
    viewMode: "optimized", // "optimized" | "diff" | "original"
    isOptimized: false,
    needsOptimize: true,
    lastResult: null,
    workerBusy: false,
    workerReady: false,
  };

  // DOM References
  const statusDot = document.getElementById("status-dot");
  const statusText = document.getElementById("status-text");
  const sampleSelect = document.getElementById("sample-select");
  const profileGroup = document.getElementById("profile-group");
  const profileSelect = document.getElementById("profile-select");
  const btnPasteJson = document.getElementById("btn-paste-json");
  const btnOptimize = document.getElementById("btn-optimize");
  const glyphStrategySelect = document.getElementById("glyph-strategy-select");
  const glyphStrategyHelp = document.getElementById("glyph-strategy-help");
  const relaxCheckbox = document.getElementById("relax-checkbox");
  const hintsCheckbox = document.getElementById("hints-checkbox");
  const budgetInput = document.getElementById("budget-input");
  const viewModeSelect = document.getElementById("view-mode-select");
  const statChipsContainer = document.getElementById("stat-chips");
  const btnClearStats = document.getElementById("btn-clear-stats");
  const summaryBanner = document.getElementById("summary-banner");
  const budgetAlertContainer = document.getElementById("budget-alert-container");
  const layoutMinimap = document.getElementById("layout-minimap");
  const boardsContainer = document.getElementById("boards-container");

  // Modal Dialog References
  const jsonDialog = document.getElementById("json-dialog");
  const jsonDialogForm = document.getElementById("json-dialog-form");
  const btnCloseDialog = document.getElementById("btn-close-dialog");
  const jsonTextarea = document.getElementById("json-textarea");
  const dialogError = document.getElementById("dialog-error");
  let dialogInitialText = "";

  // Create inline Blob Web Worker
  let worker = null;
  let nextMsgId = 1;
  const pendingCalls = new Map();

  function setStatus(kind, text) {
    statusDot.className = "status-dot " + kind;
    statusText.textContent = text;
  }

  function initWorker() {
    const zipB64 = window.__PARAGON_ZIP_B64__ || "";
    const workerCode = `self.PARAGON_ZIP_B64 = ${JSON.stringify(zipB64)};\n` + (window.__WORKER_SRC__ || "");
    const blob = new Blob([workerCode], { type: "application/javascript" });
    worker = new Worker(URL.createObjectURL(blob));

    worker.addEventListener("message", (ev) => {
      const data = ev.data || {};
      if (data.type === "status") {
        if (data.status === "ready") {
          state.workerReady = true;
          setStatus("ready", data.detail);
        } else {
          setStatus("busy", data.detail);
        }
        return;
      }
      const { id, ok, result, error } = data;
      if (pendingCalls.has(id)) {
        const { resolve, reject } = pendingCalls.get(id);
        pendingCalls.delete(id);
        if (ok) resolve(result);
        else reject(new Error(error || "Worker error"));
      }
    });

    worker.addEventListener("error", (err) => {
      setStatus("error", "Worker error: " + (err.message || "failed"));
    });
  }

  function callWorker(cmd, payload) {
    return new Promise((resolve, reject) => {
      const id = nextMsgId++;
      pendingCalls.set(id, { resolve, reject });
      worker.postMessage({ id, cmd, payload });
    });
  }

  function composeGlyphStrategySpec() {
    const parts = [state.globalGlyphStrategy];
    for (const [gName, strat] of Object.entries(state.perGlyphOverrides)) {
      if (strat && strat !== "default" && strat !== state.globalGlyphStrategy) {
        parts.push(`${gName}=${strat}`);
      }
    }
    return parts.join(",");
  }

  function markDirty() {
    state.needsOptimize = true;
    state.isOptimized = false;
    if (!state.workerBusy) {
      const wasDisabled = btnOptimize.disabled;
      btnOptimize.disabled = false;
      if (!wasDisabled && typeof btnOptimize.getAnimations === "function") {
        for (const anim of btnOptimize.getAnimations({ subtree: true })) {
          anim.cancel();
          anim.play();
        }
      }
    }
  }

  async function runOptimization(previewOnly = false) {
    if (!state.rawJsonStr || state.workerBusy) return;
    state.workerBusy = true;
    btnOptimize.disabled = true;
    if (!previewOnly) {
      btnOptimize.textContent = "Optimizing...";
    }
    boardsContainer.classList.add("is-optimizing");
    boardsContainer.setAttribute("aria-busy", "true");
    layoutMinimap.classList.add("is-optimizing");
    setStatus("busy", previewOnly ? "Parsing build..." : "Optimizing Steiner trees & 2D layout...");

    try {
      const res = await callWorker("run_pipeline", {
        rawJson: state.rawJsonStr,
        options: {
          profile_index: state.profileIndex,
          glyph_strategy: composeGlyphStrategySpec(),
          relax_path_nodes: state.relaxPathNodes,
          prioritize_stats: state.statOrderCustomized ? state.prioritizeStats : [],
          budget: Number(state.budget) || 0,
          enable_hints: state.enableHints,
          preview_only: previewOnly,
        },
      });
      state.lastResult = res;
      if (res.class_name && CLASS_DEFAULT_STACK[res.class_name.toUpperCase()]) {
        const clsKey = res.class_name.toUpperCase();
        if (clsKey !== state.currentClass && !state.statOrderCustomized) {
          state.currentClass = clsKey;
          state.prioritizeStats = [...CLASS_DEFAULT_STACK[clsKey]];
          renderStatChips();
        } else {
          state.currentClass = clsKey;
        }
      }
      state.isOptimized = !previewOnly;
      state.needsOptimize = previewOnly;
      updateProfileSelector(res.profiles || []);
      renderAll(res);
      setStatus("ready", previewOnly ? "Build loaded (unoptimized preview)" : `Optimized in ${res.solve_ms} ms`);
    } catch (err) {
      setStatus("error", err.message || String(err));
      alert("Optimization Error:\n" + (err.message || String(err)));
    } finally {
      state.workerBusy = false;
      btnOptimize.textContent = "Optimize Build";
      btnOptimize.disabled = !state.needsOptimize;
      boardsContainer.classList.remove("is-optimizing");
      boardsContainer.removeAttribute("aria-busy");
      layoutMinimap.classList.remove("is-optimizing");
    }
  }

  function updateProfileSelector(profiles) {
    if (!profiles || profiles.length <= 1) {
      profileGroup.hidden = true;
      return;
    }
    profileGroup.hidden = false;
    profileSelect.innerHTML = "";
    for (const p of profiles) {
      const opt = document.createElement("option");
      opt.value = String(p.index);
      opt.textContent = `${p.label} (${p.board_count} boards)`;
      if (p.index === state.profileIndex) opt.selected = true;
      profileSelect.appendChild(opt);
    }
  }

  function renderAll(res) {
    renderSummaryBanner(res);
    renderBudgetAlert(res.budget_info);
    renderLayoutMinimap(res.boards);
    renderBoards(res);
  }

  function renderSummaryBanner(res) {
    const isPreview = Boolean(res.preview_only);
    const saved = res.points_saved;
    const savedSign = isPreview
      ? `Unoptimized Input (${res.total_targets} targets)`
      : saved > 0
      ? `-${saved} pts saved`
      : saved < 0
      ? `+${Math.abs(saved)} pts added`
      : "Already optimal";
    const savedClass = !isPreview && saved > 0 ? "saved-positive" : "";

    const statsOrder = ["DEX", "INT", "STR", "WILL"];
    const statParts = statsOrder.map((k) => {
      const val = res.optimized_stats[k] || 0;
      const d = isPreview ? 0 : (res.stat_deltas[k] || 0);
      const dStr = d !== 0 ? ` (${d > 0 ? "+" : ""}${d})` : "";
      return `${k}: ${val}${dStr}`;
    });

    const totalHints = (res.boards || []).reduce((acc, b) => acc + (b.hints ? b.hints.length : 0), 0);
    const ptsMain = isPreview
      ? `${res.original_points} <small style="font-size:0.8rem;color:var(--text-muted)">input pts</small>`
      : `${res.optimized_points} <small style="font-size:0.8rem;color:var(--text-muted)">/ ${res.original_points} orig</small>`;
    const solverMain = isPreview ? "Unoptimized" : `${res.solve_ms} ms`;
    const solverSub = isPreview
      ? "Click 'Optimize Build' to solve"
      : `${totalHints} actionable hints (${res.hints_ms} ms)`;

    summaryBanner.innerHTML = `
      <div class="metric-card">
        <span class="metric-title">Paragon Points</span>
        <span class="metric-value">${ptsMain}</span>
        <span class="metric-sub ${savedClass}">${savedSign}</span>
      </div>
      <div class="metric-card">
        <span class="metric-title">Class & Boards</span>
        <span class="metric-value">${res.class_name}</span>
        <span class="metric-sub">${res.boards.length} Boards • ${res.profile_label}</span>
      </div>
      <div class="metric-card">
        <span class="metric-title">Core Stat Totals</span>
        <span class="metric-value" style="font-size:0.95rem">${statParts.slice(0, 2).join(" • ")}</span>
        <span class="metric-sub" style="font-family:var(--font-mono)">${statParts.slice(2).join(" • ")}</span>
      </div>
      <div class="metric-card">
        <span class="metric-title">Solver & Hints</span>
        <span class="metric-value">${solverMain}</span>
        <span class="metric-sub">${solverSub}</span>
      </div>
    `;
  }

  function renderBudgetAlert(bInfo) {
    if (!bInfo) {
      budgetAlertContainer.innerHTML = "";
      return;
    }
    const cls = bInfo.feasible ? "budget-alert" : "budget-alert infeasible";
    const statusTitle = bInfo.feasible
      ? `Budget Enforced (${bInfo.final_points} / ${bInfo.budget} pts, trimmed from ${bInfo.initial_points} pts)`
      : `Budget Exceeded (${bInfo.final_points} pts required vs. Budget ${bInfo.budget} pts)`;

    const notesHtml = (bInfo.notes || [])
      .map((s) => `<li>${escapeHtml(s)}</li>`)
      .join("");

    budgetAlertContainer.innerHTML = `
      <div class="${cls}">
        <strong>${escapeHtml(statusTitle)}</strong>
        ${notesHtml ? `<ul style="margin:0.4rem 0 0;padding-left:1.2rem">${notesHtml}</ul>` : ""}
      </div>
    `;
  }

  function renderLayoutMinimap(boards) {
    if (!boards || boards.length === 0) {
      layoutMinimap.innerHTML = "";
      return;
    }
    let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
    const byCoord = new Map();
    for (const b of boards) {
      if (b.x < minX) minX = b.x;
      if (b.x > maxX) maxX = b.x;
      if (b.y < minY) minY = b.y;
      if (b.y > maxY) maxY = b.y;
      byCoord.set(`${b.x},${b.y}`, b);
    }
    const cols = maxX - minX + 1;
    layoutMinimap.style.gridTemplateColumns = `repeat(${cols}, minmax(0, 78px))`;
    layoutMinimap.innerHTML = "";

    // Render top-to-bottom (higher y at top, matching in-game screen layout)
    for (let y = maxY; y >= minY; y--) {
      for (let x = minX; x <= maxX; x++) {
        const b = byCoord.get(`${x},${y}`);
        const cell = document.createElement("div");
        if (b) {
          cell.className = "minimap-cell occupied" + (b.is_starter ? " starter" : "");
          cell.innerHTML = `
            <strong style="color:var(--accent-gold)">#${b.index} (${x},${y})</strong>
            <span style="white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:74px">${escapeHtml(b.display_name)}</span>
            <span style="color:var(--text-muted)">${b.purchased_count} pts • ${b.rotation_deg}°</span>
          `;
          cell.addEventListener("click", () => {
            const targetEl = document.getElementById(`board-card-${b.index}`);
            if (targetEl) targetEl.scrollIntoView({ behavior: "smooth", block: "start" });
          });
        } else {
          cell.className = "minimap-cell";
        }
        layoutMinimap.appendChild(cell);
      }
    }
  }

  function renderBoards(res) {
    boardsContainer.innerHTML = "";
    for (const b of res.boards) {
      const card = document.createElement("section");
      card.className = "board-card";
      card.id = `board-card-${b.index}`;

      const diffPts = b.original_purchased_count - b.purchased_count;
      const diffLabel = diffPts > 0 ? ` (-${diffPts} pts)` : diffPts < 0 ? ` (+${Math.abs(diffPts)} pts)` : "";

      let glyphBadge = "";
      let glyphOverrideSelect = "";
      if (b.glyph_name) {
        const gStatusCls = b.glyph_met ? "glyph-ok" : "glyph-unmet";
        const gThreshStr = b.glyph_threshold > 0 ? `${b.glyph_stat_val}/${b.glyph_threshold} ${b.glyph_stat_name || ""}` : `${b.glyph_stat_val} ${b.glyph_stat_name || ""}`;
        glyphBadge = `<span class="badge ${gStatusCls}" title="Socketed Glyph (Lv.${b.glyph_level}, Radius ${b.glyph_radius})">Glyph: ${escapeHtml(b.glyph_name)} (${gThreshStr.trim()})</span>`;

        const curOverride = state.perGlyphOverrides[b.glyph_name] || "default";
        const curTip = GLYPH_STRATEGY_DESCRIPTIONS[curOverride] || "";
        const optionsHtml = GLYPH_STRATEGY_OPTIONS.map(
          ([val, label]) =>
            `<option value="${val}" title="${escapeHtml(GLYPH_STRATEGY_DESCRIPTIONS[val] || "")}" ${curOverride === val ? "selected" : ""}>${label}</option>`
        ).join("");
        glyphOverrideSelect = `
          <select class="glyph-override-select" data-glyph="${escapeHtml(b.glyph_name)}" title="${escapeHtml(curTip)}" style="padding:0.15rem 0.4rem;font-size:0.74rem">
            ${optionsHtml}
          </select>
        `;
      }

      card.innerHTML = `
        <div class="board-header">
          <div>
            <div class="board-title">#${b.index} ${escapeHtml(b.display_name)} <small style="color:var(--text-muted);font-weight:400">(${escapeHtml(b.id)})</small></div>
            <div class="board-subtitle">Layout (${b.x}, ${b.y}) • Rot ${b.rotation_deg}° • Targets: ${b.target_count}</div>
          </div>
          <div class="board-badges">
            <span class="badge">${b.purchased_count} pts${diffLabel}</span>
            ${glyphBadge}
            ${glyphOverrideSelect}
          </div>
        </div>
        <div class="board-svg-wrap"></div>
        <div class="board-inspector" id="inspector-${b.index}">
          <span>Hover any node to inspect (x, y) coordinates & affixes; click a node to toggle target.</span>
        </div>
      `;

      const overrideSel = card.querySelector(".glyph-override-select");
      if (overrideSel) {
        overrideSel.addEventListener("change", (ev) => {
          const gName = ev.target.getAttribute("data-glyph");
          const val = ev.target.value;
          ev.target.title = GLYPH_STRATEGY_DESCRIPTIONS[val] || "";
          if (val === "default") delete state.perGlyphOverrides[gName];
          else state.perGlyphOverrides[gName] = val;
          markDirty();
        });
      }

      const svgWrap = card.querySelector(".board-svg-wrap");
      const inspectorEl = card.querySelector(`#inspector-${b.index}`);
      const svgEl = buildBoardSvg(b, inspectorEl);
      svgWrap.appendChild(svgEl);

      if (b.hints && b.hints.length > 0) {
        const hintsDiv = document.createElement("div");
        hintsDiv.className = "board-hints";
        for (const h of b.hints) {
          const item = document.createElement("div");
          const deltaCls = h.points_delta > 0 ? "saving" : h.points_delta < 0 ? "warning" : "";
          item.className = `hint-item ${deltaCls}`;
          const deltaTag = h.points_delta > 0 ? `[-${h.points_delta} pts]` : h.points_delta < 0 ? `[+${Math.abs(h.points_delta)} pts]` : `[0 pts]`;
          item.innerHTML = `<span class="hint-delta">${deltaTag}</span> <strong>${escapeHtml(h.category)}:</strong> ${escapeHtml(h.message)}`;

          item.addEventListener("pointerenter", () => highlightBoardCoords(svgEl, h.screen_coords || []));
          item.addEventListener("pointerleave", () => highlightBoardCoords(svgEl, []));
          hintsDiv.appendChild(item);
        }
        card.appendChild(hintsDiv);
      }

      boardsContainer.appendChild(card);
    }
  }

  function applyCellVisuals(c, rect, txt, pal) {
    const activeInView = state.viewMode === "original" ? c.was_selected : c.is_selected;
    const addedInDiff = state.viewMode === "diff" && c.is_selected && !c.was_selected;
    const removedInDiff = state.viewMode === "diff" && !c.is_selected && c.was_selected;

    rect.setAttribute("rx", c.is_target ? "3.5" : "2.5");
    rect.setAttribute("fill", pal.fill);
    rect.setAttribute("fill-opacity", activeInView ? "0.95" : removedInDiff ? "0.35" : "0.18");
    rect.removeAttribute("stroke-dasharray");

    if (addedInDiff) {
      rect.setAttribute("stroke", "#4ec978");
      rect.setAttribute("stroke-width", "2.0");
    } else if (removedInDiff) {
      rect.setAttribute("stroke", "#e05252");
      rect.setAttribute("stroke-width", "1.6");
      rect.setAttribute("stroke-dasharray", "2,2");
    } else if (c.is_target && activeInView) {
      rect.setAttribute("stroke", "#ffffff");
      rect.setAttribute("stroke-width", "1.4");
    } else if (activeInView) {
      rect.setAttribute("stroke", "rgba(0,0,0,0.65)");
      rect.setAttribute("stroke-width", "0.8");
    } else {
      rect.removeAttribute("stroke");
      rect.removeAttribute("stroke-width");
    }

    txt.setAttribute("font-size", activeInView ? "10.5" : "8.5");
    txt.setAttribute("font-weight", activeInView || c.is_target ? "700" : "400");
    txt.setAttribute("fill", activeInView ? pal.text : "#9aa3b5");
    txt.setAttribute("fill-opacity", activeInView ? "1" : "0.55");
    txt.textContent = activeInView ? pal.symbol.toUpperCase() : pal.symbol.toLowerCase();
  }

  function updateInspectorText(c, inspectorEl) {
    const statusTags = [];
    if (c.is_target) statusTags.push("Target");
    else if (c.is_selected) statusTags.push("Selected Path");
    else statusTags.push("Unselected");
    if (c.was_selected !== c.is_selected) {
      statusTags.push(c.is_selected ? "+Added by Optimizer" : "-Saved/Removed by Optimizer");
    }
    if (c.in_glyph_radius) {
      statusTags.push(c.feeds_glyph ? "In Glyph Radius (Feeds Bonus)" : "In Glyph Radius");
    }
    inspectorEl.innerHTML = `
      <span><strong>(${c.sx}, ${c.sy})</strong> — ${escapeHtml(c.label)}</span>
      <span style="color:var(--accent-gold)">${escapeHtml(statusTags.join(" • "))}</span>
    `;
  }

  function buildBoardSvg(board, inspectorEl) {
    const ns = "http://www.w3.org/2000/svg";
    const pad = 22;
    const cellSize = 20;
    const totalSize = pad + 21 * cellSize + 8;

    const svg = document.createElementNS(ns, "svg");
    svg.setAttribute("viewBox", `0 0 ${totalSize} ${totalSize}`);
    svg.setAttribute("class", "board-svg");

    // Coordinate Axis Labels (x: 0..20 across top, y: 0..20 down left)
    const axisGroup = document.createElementNS(ns, "g");
    for (let i = 0; i < 21; i++) {
      if (i % 2 === 0) {
        const tx = document.createElementNS(ns, "text");
        tx.setAttribute("x", String(pad + i * cellSize + cellSize / 2));
        tx.setAttribute("y", "14");
        tx.setAttribute("text-anchor", "middle");
        tx.setAttribute("fill", "#6c768a");
        tx.setAttribute("font-size", "8.5");
        tx.setAttribute("font-family", "monospace");
        tx.textContent = String(i);
        axisGroup.appendChild(tx);

        const ty = document.createElementNS(ns, "text");
        ty.setAttribute("x", "11");
        ty.setAttribute("y", String(pad + i * cellSize + cellSize / 2 + 3));
        ty.setAttribute("text-anchor", "middle");
        ty.setAttribute("fill", "#6c768a");
        ty.setAttribute("font-size", "8.5");
        ty.setAttribute("font-family", "monospace");
        ty.textContent = String(i);
        axisGroup.appendChild(ty);
      }
    }
    svg.appendChild(axisGroup);

    // Grid background lines
    const gridRect = document.createElementNS(ns, "rect");
    gridRect.setAttribute("x", String(pad));
    gridRect.setAttribute("y", String(pad));
    gridRect.setAttribute("width", String(21 * cellSize));
    gridRect.setAttribute("height", String(21 * cellSize));
    gridRect.setAttribute("fill", "#0b0d12");
    gridRect.setAttribute("stroke", "#232834");
    gridRect.setAttribute("rx", "4");
    svg.appendChild(gridRect);

    const cellsGroup = document.createElementNS(ns, "g");
    const highlightGroup = document.createElementNS(ns, "g");
    highlightGroup.setAttribute("class", "highlight-layer");

    for (const c of board.cells) {
      const px = pad + c.sx * cellSize;
      const py = pad + c.sy * cellSize;
      const palKey = c.is_start ? "START" : c.type;
      const pal = NODE_PALETTE[palKey] || NODE_PALETTE.DEX;

      const g = document.createElementNS(ns, "g");
      g.setAttribute("class", "board-cell");
      g.setAttribute("data-sx", String(c.sx));
      g.setAttribute("data-sy", String(c.sy));

      // Radius diamond background tint
      if (c.in_glyph_radius) {
        const rBg = document.createElementNS(ns, "rect");
        rBg.setAttribute("x", String(px));
        rBg.setAttribute("y", String(py));
        rBg.setAttribute("width", String(cellSize));
        rBg.setAttribute("height", String(cellSize));
        rBg.setAttribute("fill", c.feeds_glyph ? "rgba(180, 126, 222, 0.20)" : "rgba(180, 126, 222, 0.09)");
        g.appendChild(rBg);
      }

      const rect = document.createElementNS(ns, "rect");
      rect.setAttribute("class", "cell-bg");
      rect.setAttribute("x", String(px + 1.4));
      rect.setAttribute("y", String(py + 1.4));
      rect.setAttribute("width", String(cellSize - 2.8));
      rect.setAttribute("height", String(cellSize - 2.8));
      g.appendChild(rect);

      const txt = document.createElementNS(ns, "text");
      txt.setAttribute("x", String(px + cellSize / 2));
      txt.setAttribute("y", String(py + cellSize / 2 + 3.4));
      txt.setAttribute("text-anchor", "middle");
      txt.setAttribute("font-family", "monospace");
      g.appendChild(txt);

      applyCellVisuals(c, rect, txt, pal);

      g.addEventListener("pointerenter", () => updateInspectorText(c, inspectorEl));
      g.addEventListener("click", () => toggleCellTarget(board, c, rect, txt, pal, inspectorEl));
      cellsGroup.appendChild(g);
    }

    svg.appendChild(cellsGroup);
    svg.appendChild(highlightGroup);
    svg._cellSize = cellSize;
    svg._pad = pad;
    return svg;
  }

  function highlightBoardCoords(svgEl, coords) {
    const ns = "http://www.w3.org/2000/svg";
    const layer = svgEl.querySelector(".highlight-layer");
    if (!layer) return;
    layer.innerHTML = "";
    const pad = svgEl._pad || 22;
    const cs = svgEl._cellSize || 20;
    for (const [sx, sy] of coords) {
      const r = document.createElementNS(ns, "rect");
      r.setAttribute("class", "cell-highlight-ring");
      r.setAttribute("x", String(pad + sx * cs - 1));
      r.setAttribute("y", String(pad + sy * cs - 1));
      r.setAttribute("width", String(cs + 2));
      r.setAttribute("height", String(cs + 2));
      r.setAttribute("rx", "4");
      layer.appendChild(r);
    }
  }

  function toggleCellTarget(board, cell, rect, txt, pal, inspectorEl) {
    if (!state.lastResult || !state.lastResult.raw_boards) return;
    if (cell.is_start || cell.type === "EXIT") return;

    const bEntry =
      state.lastResult.raw_boards.find(
        (b) => b.id === board.id || (b.id === "Paragon_Spirit_0" && board.id === "Paragon_Spirit_00")
      ) || state.lastResult.raw_boards[board.index - 1];
    if (!bEntry) return;
    bEntry.nodes = bEntry.nodes || {};
    bEntry.user_targets = bEntry.user_targets || [];
    const key = String(cell.raw_idx);

    const currentlyActive = state.viewMode === "original" ? cell.was_selected : (cell.is_selected || cell.is_target);
    if (currentlyActive) {
      delete bEntry.nodes[key];
      bEntry.user_targets = bEntry.user_targets.filter((idx) => idx !== cell.raw_idx);
      cell.is_target = false;
      cell.is_selected = false;
      cell.was_selected = false;
      board.target_count = Math.max(0, board.target_count - 1);
    } else {
      bEntry.nodes[key] = 1;
      if (!bEntry.user_targets.includes(cell.raw_idx)) {
        bEntry.user_targets.push(cell.raw_idx);
      }
      cell.is_target = true;
      cell.is_selected = true;
      cell.was_selected = true;
      board.target_count += 1;
    }

    state.rawJsonStr = JSON.stringify(state.lastResult.raw_boards, null, 2);
    applyCellVisuals(cell, rect, txt, pal);
    updateInspectorText(cell, inspectorEl);
    markDirty();
  }

  const dragLiveRegion = document.getElementById("drag-live-region");

  function announceMove(itemKey, newRank) {
    if (!dragLiveRegion) return;
    dragLiveRegion.textContent = `${itemKey} moved to priority ${newRank}`;
    setTimeout(() => {
      if (dragLiveRegion) dragLiveRegion.textContent = "";
    }, 1000);
  }

  function moveStatItem(fromIdx, toIdx, focusKey = null) {
    if (toIdx < 0 || toIdx >= state.prioritizeStats.length || fromIdx === toIdx) return;
    const [moved] = state.prioritizeStats.splice(fromIdx, 1);
    state.prioritizeStats.splice(toIdx, 0, moved);
    state.statOrderCustomized = true;
    renderStatChips(focusKey);
    announceMove(moved, toIdx + 1);
    markDirty();
  }

  function renderStatChips(focusKey = null) {
    statChipsContainer.innerHTML = "";
    state.prioritizeStats.forEach((st, idx) => {
      const meta = STAT_ITEM_META[st] || { label: st, color: "#9aa3b5" };
      const row = document.createElement("div");
      row.className = "stat-stack-item";
      row.setAttribute("role", "listitem");
      row.setAttribute("data-stat", st);

      const left = document.createElement("div");
      left.className = "stat-stack-left";
      left.innerHTML = `
        <span style="color:var(--text-muted);width:1.1rem">${idx + 1}.</span>
        <span class="stat-swatch" style="background:${meta.color}"></span>
        <strong>${escapeHtml(st)}</strong>
      `;

      const controls = document.createElement("div");
      controls.className = "stat-stack-controls";

      const upBtn = document.createElement("button");
      upBtn.type = "button";
      upBtn.className = "nudge-btn";
      upBtn.textContent = "↑";
      upBtn.disabled = idx === 0;
      upBtn.setAttribute("aria-label", `Move ${st} up`);
      upBtn.addEventListener("click", () => moveStatItem(idx, idx - 1, st));

      const downBtn = document.createElement("button");
      downBtn.type = "button";
      downBtn.className = "nudge-btn";
      downBtn.textContent = "↓";
      downBtn.disabled = idx === state.prioritizeStats.length - 1;
      downBtn.setAttribute("aria-label", `Move ${st} down`);
      downBtn.addEventListener("click", () => moveStatItem(idx, idx + 1, st));

      const handle = document.createElement("button");
      handle.type = "button";
      handle.className = "drag-handle";
      handle.textContent = "⋮⋮";
      handle.setAttribute("aria-label", `Reorder ${meta.label}. Use Up or Down arrow keys to move.`);
      handle.setAttribute("data-handle-key", st);

      handle.addEventListener("keydown", (ev) => {
        if (ev.key === "ArrowUp") {
          ev.preventDefault();
          moveStatItem(idx, idx - 1, st);
        } else if (ev.key === "ArrowDown") {
          ev.preventDefault();
          moveStatItem(idx, idx + 1, st);
        }
      });

      handle.addEventListener("pointerdown", (ev) => {
        if (ev.button !== 0) return;
        ev.preventDefault();
        let anchorY = ev.clientY;
        handle.classList.add("dragging");
        row.classList.add("dragging-row");
        row.style.transition = "none";

        const onMove = (moveEv) => {
          const rows = Array.from(statChipsContainer.querySelectorAll(".stat-stack-item"));
          const curIdx = rows.indexOf(row);
          for (let i = 0; i < rows.length; i++) {
            if (i === curIdx) continue;
            const other = rows[i];
            const r = other.getBoundingClientRect();
            const midY = r.top + r.height / 2;
            if ((i < curIdx && moveEv.clientY < midY) || (i > curIdx && moveEv.clientY > midY)) {
              const prevOtherTop = r.top;
              const prevRowTop = row.getBoundingClientRect().top - (moveEv.clientY - anchorY);
              statChipsContainer.insertBefore(row, i < curIdx ? other : other.nextSibling);
              const newRowTop = row.getBoundingClientRect().top - (moveEv.clientY - anchorY);
              anchorY += newRowTop - prevRowTop;

              // FLIP animate the displaced neighbor row smoothly
              const otherDelta = prevOtherTop - other.getBoundingClientRect().top;
              other.style.transition = "none";
              other.style.transform = `translateY(${otherDelta}px)`;
              void other.offsetHeight;
              other.style.transition = "transform 150ms cubic-bezier(0.2, 0, 0, 1)";
              other.style.transform = "";

              state.prioritizeStats = Array.from(
                statChipsContainer.querySelectorAll(".stat-stack-item")
              ).map((el) => el.getAttribute("data-stat"));
              state.statOrderCustomized = true;
              Array.from(statChipsContainer.querySelectorAll(".stat-stack-item")).forEach((el, rIdx) => {
                const numSpan = el.querySelector(".stat-stack-left span");
                if (numSpan) numSpan.textContent = `${rIdx + 1}.`;
              });
              markDirty();
              break;
            }
          }
          row.style.transform = `translateY(${moveEv.clientY - anchorY}px)`;
        };

        const onEnd = () => {
          document.removeEventListener("pointermove", onMove);
          document.removeEventListener("pointerup", onEnd);
          document.removeEventListener("pointercancel", onEnd);
          handle.classList.remove("dragging");
          row.classList.remove("dragging-row");
          row.style.transition = "";
          row.style.transform = "";
          const newRank = state.prioritizeStats.indexOf(st) + 1;
          announceMove(st, newRank);
          renderStatChips(st);
        };

        document.addEventListener("pointermove", onMove);
        document.addEventListener("pointerup", onEnd);
        document.addEventListener("pointercancel", onEnd);
      });

      controls.append(upBtn, downBtn, handle);
      row.append(left, controls);
      statChipsContainer.appendChild(row);
    });

    if (focusKey) {
      const targetHandle = statChipsContainer.querySelector(`[data-handle-key="${focusKey}"]`);
      if (targetHandle) targetHandle.focus();
    }
  }

  function populateSampleDropdown() {
    const catalog = window.__SAMPLE_CATALOG__ || [];
    if (catalog.length === 0) return;
    sampleSelect.innerHTML = "";
    for (const group of catalog) {
      const optgroup = document.createElement("optgroup");
      optgroup.label = group.class_label;
      for (const item of group.items) {
        const opt = document.createElement("option");
        opt.value = item.key;
        opt.textContent = item.label;
        if (item.key === state.activeSampleKey) {
          opt.selected = true;
        }
        optgroup.appendChild(opt);
      }
      sampleSelect.appendChild(optgroup);
    }
    const customOpt = document.createElement("option");
    customOpt.value = "custom";
    customOpt.disabled = true;
    customOpt.textContent = "Custom Imported JSON";
    sampleSelect.appendChild(customOpt);
  }

  let sampleBuildsCache = null;

  async function ensureSampleBuilds() {
    if (sampleBuildsCache) return sampleBuildsCache;
    if (window.__SAMPLE_BUILDS__) {
      sampleBuildsCache = window.__SAMPLE_BUILDS__;
      return sampleBuildsCache;
    }
    const b64 = window.__SAMPLE_BUILDS_B64__ || "";
    if (!b64) return {};
    const bin = atob(b64);
    const bytes = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++) {
      bytes[i] = bin.charCodeAt(i);
    }
    const stream = new Blob([bytes]).stream().pipeThrough(new DecompressionStream("deflate"));
    const text = await new Response(stream).text();
    sampleBuildsCache = JSON.parse(text);
    window.__SAMPLE_BUILDS__ = sampleBuildsCache;
    return sampleBuildsCache;
  }

  function resolveSampleKey(query) {
    if (!query) return null;
    const stripped = query.replace(/^build_/, "").replace(/\.json$/i, "");
    const norm = (s) => String(s).toLowerCase().replace(/[^a-z0-9]+/g, "");
    const targetNorm = norm(stripped);

    const catalog = window.__SAMPLE_CATALOG__ || [];
    for (const group of catalog) {
      for (const item of group.items) {
        if (item.key === query || item.key === stripped) return item.key;
      }
    }
    for (const group of catalog) {
      for (const item of group.items) {
        if (norm(item.key) === targetNorm || norm(item.label) === targetNorm) return item.key;
      }
    }
    return null;
  }

  function getBuildKeyFromHash() {
    const rawHash = window.location.hash.replace(/^#/, "");
    if (!rawHash) return null;
    const params = new URLSearchParams(rawHash);
    const buildParam = params.get("build") || (!rawHash.includes("=") ? decodeURIComponent(rawHash) : null);
    return resolveSampleKey(buildParam);
  }

  function loadBuildJson(rawJsonStr, sampleKey = "custom", updateHash = false) {
    state.activeSampleKey = sampleKey;
    sampleSelect.value = sampleKey;
    if (updateHash && sampleKey !== "custom") {
      history.replaceState(null, "", `#build=${encodeURIComponent(sampleKey)}`);
    }
    state.rawJsonStr = rawJsonStr;
    state.profileIndex = 0;
    state.perGlyphOverrides = {};
    state.statOrderCustomized = false;
    state.isOptimized = false;
    runOptimization(true);
  }

  async function loadSample(sampleKey, updateHash = true) {
    const samples = await ensureSampleBuilds();
    if (!samples[sampleKey]) return;
    loadBuildJson(JSON.stringify(samples[sampleKey], null, 2), sampleKey, updateHash);
  }

  function escapeHtml(str) {
    return String(str)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  // Wire up Event Listeners
  sampleSelect.addEventListener("change", (ev) => {
    if (ev.target.value !== "custom") {
      loadSample(ev.target.value);
    }
  });

  profileSelect.addEventListener("change", (ev) => {
    state.profileIndex = Number(ev.target.value) || 0;
    state.perGlyphOverrides = {};
    state.isOptimized = false;
    runOptimization(true);
  });

  function setDialogInvalid(errMessage) {
    if (errMessage) {
      jsonTextarea.setAttribute("aria-invalid", "true");
      btnCloseDialog.classList.add("is-invalid");
      dialogError.textContent = errMessage;
      dialogError.classList.add("visible");
    } else {
      jsonTextarea.removeAttribute("aria-invalid");
      btnCloseDialog.classList.remove("is-invalid");
      dialogError.textContent = "";
      dialogError.classList.remove("visible");
    }
  }

  function validateDialogJson() {
    const raw = jsonTextarea.value.trim();
    if (!raw) {
      setDialogInvalid("JSON cannot be empty.");
      return false;
    }
    try {
      const parsed = JSON.parse(raw);
      if (!parsed || typeof parsed !== "object") {
        setDialogInvalid("Build JSON must be a JSON object or array.");
        return false;
      }
      setDialogInvalid("");
      return true;
    } catch (err) {
      setDialogInvalid("Invalid JSON: " + err.message);
      return false;
    }
  }

  btnPasteJson.addEventListener("click", () => {
    if (state.isOptimized && state.lastResult && state.lastResult.output_json) {
      jsonTextarea.value = JSON.stringify(state.lastResult.output_json, null, 2);
    } else {
      jsonTextarea.value = state.rawJsonStr;
    }
    dialogInitialText = jsonTextarea.value;
    setDialogInvalid("");
    jsonDialog.showModal();
    jsonTextarea.focus();
    jsonTextarea.setSelectionRange(0, 0);
    jsonTextarea.scrollTop = 0;
  });

  jsonTextarea.addEventListener("input", () => {
    if (jsonTextarea.getAttribute("aria-invalid") === "true") {
      setDialogInvalid("");
    }
  });

  jsonTextarea.addEventListener("blur", () => {
    if (jsonTextarea.value.trim() !== dialogInitialText.trim()) {
      validateDialogJson();
    }
  });

  jsonDialogForm.addEventListener("submit", (ev) => {
    const submitter = ev.submitter;
    const action = submitter ? submitter.value : "close";
    if (action === "cancel") {
      setDialogInvalid("");
      return;
    }
    if (!validateDialogJson()) {
      ev.preventDefault();
      jsonTextarea.focus();
    }
  });

  jsonDialog.addEventListener("close", () => {
    const action = jsonDialog.returnValue;
    jsonDialog.returnValue = "";
    if (action !== "close") {
      return;
    }
    const trimmed = jsonTextarea.value.trim();
    if (trimmed !== dialogInitialText.trim() && validateDialogJson()) {
      loadBuildJson(jsonTextarea.value, "custom", false);
    }
  });

  btnOptimize.addEventListener("click", () => {
    runOptimization(false);
  });

  glyphStrategySelect.addEventListener("change", (ev) => {
    state.globalGlyphStrategy = ev.target.value;
    const desc = GLYPH_STRATEGY_DESCRIPTIONS[state.globalGlyphStrategy] || "";
    glyphStrategySelect.title = desc;
    if (glyphStrategyHelp) glyphStrategyHelp.textContent = desc;
    markDirty();
  });

  relaxCheckbox.addEventListener("change", (ev) => {
    state.relaxPathNodes = ev.target.checked;
    markDirty();
  });

  hintsCheckbox.addEventListener("change", (ev) => {
    state.enableHints = ev.target.checked;
    markDirty();
  });

  budgetInput.addEventListener("change", (ev) => {
    state.budget = Number(ev.target.value) || 0;
    markDirty();
  });

  viewModeSelect.addEventListener("change", (ev) => {
    state.viewMode = ev.target.value;
    if (state.lastResult) renderBoards(state.lastResult);
  });

  btnClearStats.addEventListener("click", () => {
    state.prioritizeStats = [...(CLASS_DEFAULT_STACK[state.currentClass] || CLASS_DEFAULT_STACK.ROGUE)];
    state.statOrderCustomized = false;
    renderStatChips();
    markDirty();
  });

  // Boot
  populateSampleDropdown();
  renderStatChips();
  initWorker();

  window.addEventListener("hashchange", () => {
    const hashKey = getBuildKeyFromHash();
    if (hashKey && hashKey !== state.activeSampleKey) {
      loadSample(hashKey, false);
    }
  });

  const initialKey =
    getBuildKeyFromHash() ||
    (window.__SAMPLE_CATALOG__ &&
      window.__SAMPLE_CATALOG__[0] &&
      window.__SAMPLE_CATALOG__[0].items[0] &&
      window.__SAMPLE_CATALOG__[0].items[0].key);
  if (initialKey) {
    loadSample(initialKey, Boolean(getBuildKeyFromHash()));
  }
})();
