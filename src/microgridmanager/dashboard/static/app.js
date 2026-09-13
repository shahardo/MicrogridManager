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
    card("Battery rule (placeholder, M7 replaces this)", [
      ["SoC headroom", fmtPct(row.battery_soc_headroom)],
      ["Charge rule output", fmtW(row.charge_rule_output_w)],
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
    const py = yScale(y);
    const label = opts.yFormat ? opts.yFormat(y) : y.toFixed(2);
    ctx.fillText(label, 2, py + 3);
    ctx.strokeStyle = "#eef2f6";
    ctx.beginPath();
    ctx.moveTo(padding.left, py);
    ctx.lineTo(padding.left + plotW, py);
    ctx.stroke();
  }

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

  let lx = padding.left + 4;
  ctx.font = "11px system-ui";
  plottable.forEach((s) => {
    ctx.fillStyle = s.color;
    ctx.fillRect(lx, 4, 8, 8);
    ctx.fillStyle = "#334155";
    ctx.fillText(s.label, lx + 11, 12);
    lx += ctx.measureText(s.label).width + 30;
  });
}

function renderCharts(rows) {
  if (!rows || rows.length === 0) {
    return;
  }

  drawLineChart(document.getElementById("chart-power"), [
    seriesFromRows(rows, "pv_power_w", "PV", COLORS[0]),
    seriesFromRows(rows, "battery_power_w", "Battery", COLORS[1]),
    seriesFromRows(rows, "household_load_power_w", "Household", COLORS[2]),
    seriesFromRows(rows, "ev_power_w", "EV", COLORS[3]),
    seriesFromRows(rows, "water_heater_power_w", "Water heater", COLORS[4]),
    seriesFromRows(rows, "grid_power_w", "Grid", COLORS[5]),
  ]);

  drawLineChart(
    document.getElementById("chart-levels"),
    [
      seriesFromRows(rows, "battery_soc", "Battery SoC", COLORS[1]),
      seriesFromRows(rows, "ev_soc", "EV SoC", COLORS[3]),
      seriesFromRows(rows, "ev_target_soc", "EV target", "#c4b5fd"),
      seriesFromRows(rows, "water_heater_tank_fraction", "Water tank", COLORS[4]),
    ],
    { yMin: 0, yMax: 1, yFormat: (y) => `${Math.round(y * 100)}%` },
  );

  drawLineChart(document.getElementById("chart-price"), [
    seriesFromRows(rows, "grid_import_price_per_kwh", "Import $/kWh", COLORS[2]),
    seriesFromRows(rows, "grid_export_price_per_kwh", "Export $/kWh", COLORS[5]),
  ]);

  drawLineChart(document.getElementById("chart-forecast"), [
    seriesFromRows(rows, "pv_power_w", "PV actual", COLORS[0]),
    seriesFromRows(rows, "pv_power_forecast_w", "PV forecast", "#93c5fd"),
    seriesFromRows(rows, "household_load_power_w", "Load actual", COLORS[2]),
    seriesFromRows(rows, "household_load_power_forecast_w", "Load forecast", "#fca5a5"),
  ]);

  drawLineChart(
    document.getElementById("chart-protection"),
    [
      seriesFromRows(rows, "protection_state", "State (0=normal..4=restoration)", COLORS[6]),
      seriesFromRows(rows, "household_load_served", "Household served", COLORS[2]),
      seriesFromRows(rows, "water_heater_served", "Water heater served", COLORS[4]),
      seriesFromRows(rows, "ev_charger_served", "EV served", COLORS[3]),
    ],
    { yMin: 0, yMax: 4 },
  );
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
