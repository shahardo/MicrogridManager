const COLORS = ["#2563eb", "#16a34a", "#dc2626", "#9333ea", "#ea580c", "#0891b2", "#64748b"];

// Mirrors microgridmanager.protection.state_machine.PROTECTION_STATE_CODES —
// telemetry/JSON can only carry numbers, so the state is decoded back to a
// label here rather than sent as a string.
const PROTECTION_STATE_LABELS = {
  0: "grid-connected (normal)",
  1: "islanding transition",
  2: "islanded",
  3: "black start",
  4: "restoration",
};

const state = {
  mode: "live",
  liveTimer: null,
  chartView: "line",
  lastChartRows: [],
  replay: {
    rows: [],
    index: 0,
    playing: false,
    timer: null,
    speed: 1.0,
  },
};

async function fetchJSON(url, opts) {
  const res = await fetch(url, opts);
  if (!res.ok) {
    throw new Error(`${url}: ${res.status}`);
  }
  return res.json();
}

function card(title, metrics) {
  const rows = metrics
    .map(([label, value]) => `<div class="metric"><span>${label}</span><span class="value">${value}</span></div>`)
    .join("");
  return `<div class="card"><h3>${title}</h3>${rows}</div>`;
}

function fmtW(watts) {
  if (watts === undefined || watts === null) return "n/a";
  return `${(watts / 1000).toFixed(2)} kW`;
}

function fmtPct(fraction) {
  if (fraction === undefined || fraction === null) return "n/a";
  return `${(fraction * 100).toFixed(0)}%`;
}

function fmtPrice(price) {
  if (price === undefined || price === null) return "n/a";
  return `$${price.toFixed(3)}/kWh`;
}

function renderCards(row) {
  const deviceCards = document.getElementById("device-cards");
  const decisionCards = document.getElementById("decision-cards");
  if (!row) {
    deviceCards.innerHTML = "<p>No data yet — start the simulation or pick a run to replay.</p>";
    decisionCards.innerHTML = "";
    return;
  }

  deviceCards.innerHTML = [
    card("Rooftop PV", [["Power", fmtW(row.pv_power_w)]]),
    card("Battery", [
      ["Power", fmtW(row.battery_power_w)],
      ["State of charge", fmtPct(row.battery_soc)],
    ]),
    card("Household load", [["Power", fmtW(row.household_load_power_w)]]),
    card("EV charger", [
      ["Power", fmtW(row.ev_power_w)],
      ["Plugged in", row.ev_plugged_in ? "yes" : "no"],
      ["Charging", row.ev_charging ? "yes" : "no"],
      ["State of charge", fmtPct(row.ev_soc)],
      ["Target", fmtPct(row.ev_target_soc)],
    ]),
    card("Water heater", [
      ["Power", fmtW(row.water_heater_power_w)],
      ["Tank level", fmtPct(row.water_heater_tank_fraction)],
      ["Heating", row.water_heater_heating ? "yes" : "no"],
    ]),
    card("Grid connection", [
      [
        "Power",
        row.grid_power_w === undefined
          ? "n/a"
          : `${fmtW(Math.abs(row.grid_power_w))} ${row.grid_power_w >= 0 ? "(import)" : "(export)"}`,
      ],
      ["Import price", fmtPrice(row.grid_import_price_per_kwh)],
      ["Export price", fmtPrice(row.grid_export_price_per_kwh)],
    ]),
  ].join("");

  decisionCards.innerHTML = [
    card("Tariff", [
      ["Current import price", fmtPrice(row.grid_import_price_per_kwh)],
      ["Projected import price", fmtPrice(row.grid_projected_import_price_per_kwh)],
    ]),
    card("Economic dispatch (M7)", [
      ["Active", row.dispatch_active ? "yes (grid-connected)" : "no (self-consumption rule)"],
      ["Battery SoC headroom", fmtPct(row.battery_soc_headroom)],
      ["Battery setpoint", fmtW(row.charge_rule_output_w)],
      [
        "Projected horizon cost",
        row.dispatch_projected_cost_usd === undefined
          ? "n/a"
          : `$${row.dispatch_projected_cost_usd.toFixed(3)}`,
      ],
    ]),
    card("Protection state machine (M5)", [
      [
        "State",
        row.protection_state === undefined
          ? "n/a"
          : (PROTECTION_STATE_LABELS[row.protection_state] ?? row.protection_state),
      ],
      ["Household load", row.household_load_served ? "served" : "shed"],
      ["Water heater", row.water_heater_served ? "served" : "shed"],
      ["EV charger", row.ev_charger_served ? "served" : "shed"],
    ]),
  ].join("");
}

function seriesFromRows(rows, key, label, color) {
  return {
    label,
    color,
    points: rows
      .filter((r) => r[key] !== undefined && r[key] !== null)
      .map((r) => ({ t: new Date(r.timestamp).getTime(), y: r[key] })),
  };
}

// Shared by both chart styles: left/bottom axis lines, horizontal gridlines
// and their labels. Takes pre-computed yScale bounds so it has no opinion on
// how those bounds were derived (plain min/max for line charts, stacked
// totals for area charts).
function drawAxes(ctx, { padding, plotW, plotH, yMin, yMax, yFormat }) {
  ctx.strokeStyle = "#cbd5e1";
  ctx.beginPath();
  ctx.moveTo(padding.left, padding.top);
  ctx.lineTo(padding.left, padding.top + plotH);
  ctx.lineTo(padding.left + plotW, padding.top + plotH);
  ctx.stroke();

  ctx.fillStyle = "#94a3b8";
  ctx.font = "10px system-ui";
  for (let i = 0; i <= 4; i++) {
    const y = yMin + (yMax - yMin) * (i / 4);
    const py = padding.top + plotH - ((y - yMin) / (yMax - yMin || 1)) * plotH;
    const label = yFormat ? yFormat(y) : y.toFixed(2);
    ctx.fillText(label, 2, py + 3);
    ctx.strokeStyle = "#eef2f6";
    ctx.beginPath();
    ctx.moveTo(padding.left, py);
    ctx.lineTo(padding.left + plotW, py);
    ctx.stroke();
  }
}

function drawLegend(ctx, padding, seriesList) {
  let lx = padding.left + 4;
  ctx.font = "11px system-ui";
  seriesList.forEach((s) => {
    ctx.fillStyle = s.color;
    ctx.fillRect(lx, 4, 8, 8);
    ctx.fillStyle = "#334155";
    ctx.fillText(s.label, lx + 11, 12);
    lx += ctx.measureText(s.label).width + 30;
  });
}

function drawLineChart(canvas, seriesList, opts = {}) {
  const ctx = canvas.getContext("2d");
  const width = canvas.width;
  const height = canvas.height;
  ctx.clearRect(0, 0, width, height);

  const padding = { left: 55, right: 10, top: 18, bottom: 24 };
  const plotW = width - padding.left - padding.right;
  const plotH = height - padding.top - padding.bottom;

  const plottable = seriesList.filter((s) => s.points.length >= 2);
  const allPoints = plottable.flatMap((s) => s.points);

  if (allPoints.length === 0) {
    ctx.fillStyle = "#94a3b8";
    ctx.font = "12px system-ui";
    ctx.fillText("Not enough data yet", padding.left, height / 2);
    return;
  }

  const xs = allPoints.map((p) => p.t);
  const xMin = Math.min(...xs);
  const xMax = Math.max(...xs);
  let yMin = opts.yMin ?? Math.min(...allPoints.map((p) => p.y));
  let yMax = opts.yMax ?? Math.max(...allPoints.map((p) => p.y));
  if (yMax - yMin < 1e-9) {
    yMax += 1;
    yMin -= 1;
  }
  if (opts.yMin === undefined && opts.yMax === undefined) {
    const pad = (yMax - yMin) * 0.1;
    yMin -= pad;
    yMax += pad;
  }

  const xScale = (t) => padding.left + ((t - xMin) / (xMax - xMin || 1)) * plotW;
  const yScale = (y) => padding.top + plotH - ((y - yMin) / (yMax - yMin || 1)) * plotH;

  drawAxes(ctx, { padding, plotW, plotH, yMin, yMax, yFormat: opts.yFormat });

  plottable.forEach((s) => {
    ctx.strokeStyle = s.color;
    ctx.lineWidth = 1.5;
    ctx.beginPath();
    s.points.forEach((p, i) => {
      const x = xScale(p.t);
      const y = yScale(p.y);
      if (i === 0) {
        ctx.moveTo(x, y);
      } else {
        ctx.lineTo(x, y);
      }
    });
    ctx.stroke();
  });

  drawLegend(ctx, padding, plottable);
}

// Same series/opts shape as drawLineChart, but reads straight from `rows`
// (rather than pre-filtered per-series points) since every band needs
// values aligned at the same x position to stack correctly; a missing
// reading is treated as 0 contribution for that tick.
function drawStackedAreaChart(canvas, rows, seriesDefs, opts = {}) {
  const ctx = canvas.getContext("2d");
  const width = canvas.width;
  const height = canvas.height;
  ctx.clearRect(0, 0, width, height);

  const padding = { left: 55, right: 10, top: 18, bottom: 24 };
  const plotW = width - padding.left - padding.right;
  const plotH = height - padding.top - padding.bottom;

  const points = rows
    .filter((r) => r.timestamp !== undefined && r.timestamp !== null)
    .map((r) => ({
      t: new Date(r.timestamp).getTime(),
      values: seriesDefs.map((s) => (r[s.key] === undefined || r[s.key] === null ? 0 : r[s.key])),
    }));

  if (points.length < 2) {
    ctx.fillStyle = "#94a3b8";
    ctx.font = "12px system-ui";
    ctx.fillText("Not enough data yet", padding.left, height / 2);
    return;
  }

  let yMinAll = 0;
  let yMaxAll = 0;
  const stacks = points.map((p) => {
    let base = 0;
    return p.values.map((v) => {
      const y0 = base;
      const y1 = base + v;
      base = y1;
      yMinAll = Math.min(yMinAll, y0, y1);
      yMaxAll = Math.max(yMaxAll, y0, y1);
      return [y0, y1];
    });
  });

  let yMin = opts.yMin ?? yMinAll;
  let yMax = opts.yMax ?? yMaxAll;
  if (yMax - yMin < 1e-9) {
    yMax += 1;
    yMin -= 1;
  }
  if (opts.yMin === undefined && opts.yMax === undefined) {
    const pad = (yMax - yMin) * 0.1;
    yMin -= pad;
    yMax += pad;
  }

  const ts = points.map((p) => p.t);
  const xMin = Math.min(...ts);
  const xMax = Math.max(...ts);
  const xScale = (t) => padding.left + ((t - xMin) / (xMax - xMin || 1)) * plotW;
  const yScale = (y) => padding.top + plotH - ((y - yMin) / (yMax - yMin || 1)) * plotH;

  drawAxes(ctx, { padding, plotW, plotH, yMin, yMax, yFormat: opts.yFormat });

  seriesDefs.forEach((s, idx) => {
    ctx.beginPath();
    points.forEach((p, i) => {
      const x = xScale(p.t);
      const y = yScale(stacks[i][idx][1]);
      if (i === 0) {
        ctx.moveTo(x, y);
      } else {
        ctx.lineTo(x, y);
      }
    });
    for (let i = points.length - 1; i >= 0; i--) {
      const x = xScale(points[i].t);
      const y = yScale(stacks[i][idx][0]);
      ctx.lineTo(x, y);
    }
    ctx.closePath();
    ctx.fillStyle = `${s.color}b3`;
    ctx.fill();
    ctx.strokeStyle = s.color;
    ctx.lineWidth = 1;
    ctx.beginPath();
    points.forEach((p, i) => {
      const x = xScale(p.t);
      const y = yScale(stacks[i][idx][1]);
      if (i === 0) {
        ctx.moveTo(x, y);
      } else {
        ctx.lineTo(x, y);
      }
    });
    ctx.stroke();
  });

  drawLegend(ctx, padding, seriesDefs);
}

const CHART_CONFIGS = [
  {
    id: "chart-power",
    series: [
      { key: "pv_power_w", label: "PV", color: COLORS[0] },
      { key: "battery_power_w", label: "Battery", color: COLORS[1] },
      { key: "household_load_power_w", label: "Household", color: COLORS[2] },
      { key: "ev_power_w", label: "EV", color: COLORS[3] },
      { key: "water_heater_power_w", label: "Water heater", color: COLORS[4] },
      { key: "grid_power_w", label: "Grid", color: COLORS[5] },
    ],
    opts: {},
  },
  {
    id: "chart-levels",
    series: [
      { key: "battery_soc", label: "Battery SoC", color: COLORS[1] },
      { key: "ev_soc", label: "EV SoC", color: COLORS[3] },
      { key: "ev_target_soc", label: "EV target", color: "#c4b5fd" },
      { key: "water_heater_tank_fraction", label: "Water tank", color: COLORS[4] },
    ],
    opts: { yMin: 0, yMax: 1, yFormat: (y) => `${Math.round(y * 100)}%` },
  },
  {
    id: "chart-price",
    series: [
      { key: "grid_import_price_per_kwh", label: "Import $/kWh", color: COLORS[2] },
      { key: "grid_export_price_per_kwh", label: "Export $/kWh", color: COLORS[5] },
    ],
    opts: {},
  },
  {
    id: "chart-protection",
    series: [
      { key: "protection_state", label: "State (0=normal..4=restoration)", color: COLORS[6] },
      { key: "household_load_served", label: "Household served", color: COLORS[2] },
      { key: "water_heater_served", label: "Water heater served", color: COLORS[4] },
      { key: "ev_charger_served", label: "EV served", color: COLORS[3] },
    ],
    opts: { yMin: 0, yMax: 4 },
  },
  {
    id: "chart-forecast",
    series: [
      { key: "pv_power_w", label: "PV actual", color: COLORS[0] },
      { key: "pv_power_forecast_w", label: "PV forecast", color: "#93c5fd" },
      { key: "household_load_power_w", label: "Load actual", color: COLORS[2] },
      { key: "household_load_power_forecast_w", label: "Load forecast", color: "#fca5a5" },
    ],
    opts: {},
  },
];

function renderCharts(rows) {
  if (!rows || rows.length === 0) {
    return;
  }
  state.lastChartRows = rows;

  CHART_CONFIGS.forEach((cfg) => {
    const canvas = document.getElementById(cfg.id);
    if (state.chartView === "stacked") {
      drawStackedAreaChart(canvas, rows, cfg.series, cfg.opts);
    } else {
      const seriesList = cfg.series.map((s) => seriesFromRows(rows, s.key, s.label, s.color));
      drawLineChart(canvas, seriesList, cfg.opts);
    }
  });
}

async function pollLive() {
  try {
    const [liveState, history] = await Promise.all([
      fetchJSON("/api/state"),
      fetchJSON("/api/history?limit=200"),
    ]);
    document.getElementById("btn-start").disabled = liveState.running;
    document.getElementById("btn-pause").disabled = !liveState.running;
    document.getElementById("speed-input").value = liveState.speed;
    document.getElementById("grid-toggle").checked = liveState.grid_connected;
    document.getElementById("run-id-label").textContent = liveState.run_id;
    renderCards(liveState.latest);
    renderCharts(history);
  } catch (err) {
    console.error(err);
  }
}

function reconstructRows(seriesMap) {
  const keys = Object.keys(seriesMap);
  if (keys.length === 0) return [];
  const length = seriesMap[keys[0]].length;
  const rows = [];
  for (let i = 0; i < length; i++) {
    const row = { timestamp: seriesMap[keys[0]][i].timestamp };
    for (const key of keys) {
      const point = seriesMap[key][i];
      if (point) row[key] = point.value;
    }
    rows.push(row);
  }
  return rows;
}

function stopReplayTimer() {
  if (state.replay.timer) {
    clearInterval(state.replay.timer);
    state.replay.timer = null;
  }
  state.replay.playing = false;
}

function renderReplayFrame() {
  const { rows, index } = state.replay;
  if (rows.length === 0) {
    renderCards(null);
    return;
  }
  const clamped = Math.min(index, rows.length - 1);
  state.replay.index = clamped;
  const windowRows = rows.slice(0, clamped + 1).slice(-200);
  renderCards(rows[clamped]);
  renderCharts(windowRows);
  document.getElementById("replay-position").textContent = `${clamped + 1} / ${rows.length}`;
  document.getElementById("replay-seek").value = clamped;
}

function startReplayTimer() {
  stopReplayTimer();
  state.replay.playing = true;
  const intervalMs = Math.max(1000 / state.replay.speed, 20);
  state.replay.timer = setInterval(() => {
    if (state.replay.index >= state.replay.rows.length - 1) {
      stopReplayTimer();
      return;
    }
    state.replay.index += 1;
    renderReplayFrame();
  }, intervalMs);
}

async function loadRun(runId) {
  const data = await fetchJSON(`/api/runs/${encodeURIComponent(runId)}/data`);
  state.replay.rows = reconstructRows(data);
  state.replay.index = 0;
  const seek = document.getElementById("replay-seek");
  seek.max = Math.max(state.replay.rows.length - 1, 0);
  seek.value = 0;
  renderReplayFrame();
}

async function loadRunList() {
  const runs = await fetchJSON("/api/runs");
  const select = document.getElementById("replay-run-select");
  select.innerHTML = runs.map((r) => `<option value="${r}">${r}</option>`).join("");
  if (runs.length > 0) {
    select.value = runs[runs.length - 1];
    await loadRun(select.value);
  } else {
    renderCards(null);
  }
}

function switchMode(mode) {
  state.mode = mode;
  document.getElementById("mode-live").classList.toggle("active", mode === "live");
  document.getElementById("mode-replay").classList.toggle("active", mode === "replay");
  document.getElementById("live-controls").classList.toggle("hidden", mode !== "live");
  document.getElementById("replay-controls").classList.toggle("hidden", mode !== "replay");

  if (mode === "live") {
    stopReplayTimer();
    if (!state.liveTimer) {
      pollLive();
      state.liveTimer = setInterval(pollLive, 1000);
    }
  } else {
    if (state.liveTimer) {
      clearInterval(state.liveTimer);
      state.liveTimer = null;
    }
    loadRunList();
  }
}

document.getElementById("mode-live").addEventListener("click", () => switchMode("live"));
document.getElementById("mode-replay").addEventListener("click", () => switchMode("replay"));

document.getElementById("btn-start").addEventListener("click", () =>
  fetchJSON("/api/controls/start", { method: "POST" }),
);
document.getElementById("btn-pause").addEventListener("click", () =>
  fetchJSON("/api/controls/pause", { method: "POST" }),
);
document.getElementById("btn-reset").addEventListener("click", () =>
  fetchJSON("/api/controls/reset", { method: "POST" }),
);
document.getElementById("speed-input").addEventListener("change", (e) => {
  fetchJSON("/api/controls/speed", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ speed: parseFloat(e.target.value) }),
  });
});
document.getElementById("grid-toggle").addEventListener("change", (e) => {
  fetchJSON("/api/controls/grid", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ connected: e.target.checked }),
  });
});

document.getElementById("chart-view-select").addEventListener("change", (e) => {
  state.chartView = e.target.value;
  renderCharts(state.lastChartRows);
});

document.getElementById("replay-run-select").addEventListener("change", (e) => loadRun(e.target.value));
document.getElementById("replay-seek").addEventListener("input", (e) => {
  stopReplayTimer();
  state.replay.index = parseInt(e.target.value, 10);
  renderReplayFrame();
});
document.getElementById("replay-play").addEventListener("click", startReplayTimer);
document.getElementById("replay-pause").addEventListener("click", stopReplayTimer);
document.getElementById("replay-speed").addEventListener("change", (e) => {
  state.replay.speed = parseFloat(e.target.value) || 1.0;
  if (state.replay.playing) {
    startReplayTimer();
  }
});

switchMode("live");
