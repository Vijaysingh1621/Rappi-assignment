const state = { mode: "rules", cases: [], selected: null, health: null };

const $ = (sel) => document.querySelector(sel);
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const money = (v) => "$" + Number(v || 0).toLocaleString(undefined, { maximumFractionDigits: 2 });

async function api(path, opts = {}) {
  const res = await fetch(path, { headers: { "Content-Type": "application/json" }, ...opts });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(body.detail || res.statusText);
  return body;
}

function toast(msg) {
  const t = $("#toast");
  t.textContent = msg;
  t.hidden = false;
  clearTimeout(toast._t);
  toast._t = setTimeout(() => (t.hidden = true), 4500);
}

const SCENARIOS = {
  recommendation_review: "1 · Recommendation review",
  supplier_shortfall: "2 · Supplier cannot fulfil",
  demand_change: "3 · Demand changed",
  purchasing_constraint: "4 · Purchasing constraint",
};

const STATUS_CLASS = {
  completed: "ok", completed_with_escalation: "warn", awaiting_approval: "info",
  escalated: "bad", failed: "bad", rejected_by_human: "bad", stale_approval: "bad",
};
const VERDICT_CLASS = { accept: "ok", modify: "info", reject: "bad", investigate: "warn", escalate: "warn" };

function statusPill(s) {
  return s ? `<span class="pill ${STATUS_CLASS[s] || ""}">${esc(s.replaceAll("_", " "))}</span>` : "";
}

// ---------------------------------------------------------------------------------------
async function loadCases() {
  state.cases = await api("/api/cases");
  const groups = {};
  for (const c of state.cases) (groups[c.scenario] ||= []).push(c);
  $("#caseList").innerHTML = Object.entries(SCENARIOS).map(([k, label]) => `
    <div class="group">${label}</div>
    ${(groups[k] || []).map((c) => `
      <div class="caseitem ${state.selected === c.id ? "selected" : ""}" data-id="${esc(c.id)}">
        <div><div class="t">${esc(c.title)}</div><div class="id">${esc(c.id)}</div></div>
        ${c.latest_run ? statusPill(c.latest_run.status) : ""}
      </div>`).join("")}`).join("");
  document.querySelectorAll(".caseitem").forEach((el) =>
    el.addEventListener("click", () => selectCase(el.dataset.id)));
}

function sparkline(values, forecast) {
  const w = 600, h = 70, max = Math.max(...values, forecast) * 1.1 || 1;
  const x = (i) => (i / (values.length - 1)) * w;
  const y = (v) => h - (v / max) * h;
  const pts = values.map((v, i) => `${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(" ");
  return `<svg class="spark" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none" role="img"
      aria-label="28-day sales vs forecast">
    <line x1="0" x2="${w}" y1="${y(forecast)}" y2="${y(forecast)}" stroke="var(--muted)" stroke-dasharray="4 4"/>
    <polyline points="${pts}" fill="none" stroke="var(--accent)" stroke-width="2"/></svg>`;
}

async function selectCase(id) {
  state.selected = id;
  document.querySelectorAll(".caseitem").forEach((el) => el.classList.toggle("selected", el.dataset.id === id));
  const d = await api(`/api/cases/${id}`);
  const { case: c, item, demand, open_pos, budget, projection } = d;
  const inbound = open_pos.reduce((s, p) => s + p.inbound_qty, 0);
  const rec = c.recommendation;
  const ev = c.event;
  $("#caseDetail").innerHTML = `
    <div class="row spread">
      <div><h2>${esc(c.title)}</h2><div class="muted">${esc(c.id)} · ${esc(item.name)} @ ${esc(item.node_id)}</div></div>
      <button id="runBtn" class="primary">Run ${state.mode === "llm" ? "Claude" : "rules"} agent</button>
    </div>
    <p>${esc(c.description)}</p>
    ${rec ? `<div class="callout"><b>System recommendation:</b> buy ${rec.quantity} units from ${esc(rec.supplier_id)}
      <div class="muted">${esc(rec.rationale)}</div></div>` : ""}
    ${ev && ev.message ? `<div class="callout info"><b>Event:</b> ${esc(ev.message)}</div>` : ""}
    <div class="kpis">
      <div class="kpi"><div class="v">${item.on_hand}</div><div class="l">On hand (count ${item.days_since_cycle_count}d ago)</div></div>
      <div class="kpi"><div class="v">${inbound}</div><div class="l">Inbound on ${open_pos.length} open PO(s)</div></div>
      <div class="kpi"><div class="v">${demand.forecast_daily}</div><div class="l">Forecast / day</div></div>
      <div class="kpi"><div class="v">${demand.avg_7d}</div><div class="l">Actual 7-day avg</div></div>
      <div class="kpi"><div class="v">${item.storage_capacity}</div><div class="l">Storage capacity</div></div>
      <div class="kpi"><div class="v">${money(budget.remaining)}</div><div class="l">Budget left (${esc(item.category)})</div></div>
    </div>
    <h3>Sales, last 28 days <span class="muted">(dashed = forecast) · signal: ${esc(demand.signal)}</span></h3>
    ${sparkline(demand.sales_28d, demand.forecast_daily)}
    <div class="muted">${esc(demand.note)} ${projection.first_stockout_day !== null
      ? `<b>Without action: stock-out on day ${projection.first_stockout_day}.</b>` : ""}</div>
    ${open_pos.length ? `<h3>Open purchase orders</h3>${table(open_pos,
      ["id", "supplier_id", "qty_ordered", "qty_confirmed", "status", "shipping_status", "expected_arrival"])}` : ""}
    <div id="runResult"></div>
    ${d.runs.length ? `<h3>Previous runs</h3>${d.runs.map((r) => `
      <div class="row"><a href="#" class="mono" data-run="${esc(r.id)}">${esc(r.id)}</a>${statusPill(r.status)}
      <span class="muted">${esc(r.mode)} · ${esc((r.started_at || "").slice(11, 19))}</span></div>`).join("")}` : ""}`;
  $("#runBtn").addEventListener("click", () => runAgent(id));
  document.querySelectorAll("[data-run]").forEach((a) => a.addEventListener("click", async (e) => {
    e.preventDefault();
    renderRun(await api(`/api/runs/${a.dataset.run}`));
  }));
}

function table(rows, cols) {
  return `<div class="table-wrap"><table><thead><tr>${cols.map((c) => `<th>${esc(c.replaceAll("_", " "))}</th>`).join("")}
    </tr></thead><tbody>${rows.map((r) => `<tr>${cols.map((c) => `<td>${esc(r[c])}</td>`).join("")}</tr>`).join("")}
    </tbody></table></div>`;
}

async function runAgent(id) {
  const btn = $("#runBtn");
  btn.disabled = true;
  btn.innerHTML = `<span class="spinner"></span> Agent working…`;
  try {
    const run = await api(`/api/cases/${id}/run`, { method: "POST", body: JSON.stringify({ mode: state.mode }) });
    await Promise.all([loadCases(), refreshCounts()]);
    await selectCase(id);
    renderRun(run);
  } catch (e) {
    toast(e.message);
    btn.disabled = false;
    btn.textContent = "Run agent";
  }
}

// ---------------------------------------------------------------------------------------
function renderRun(run) {
  const final = run.decision?.final;
  const executed = run.decision?.executed || [];
  const el = $("#runResult");
  el.innerHTML = `
    <div class="decision">
      <div class="row spread">
        <div class="row">
          <span class="verdict">${esc(final?.verdict || "—")}</span>
          ${final ? `<span class="pill ${VERDICT_CLASS[final.verdict] || ""}">confidence ${esc(final.confidence)}</span>` : ""}
        </div>
        <div class="row">${statusPill(run.status)} <span class="mono muted">${esc(run.id)} · ${esc(run.mode)}</span></div>
      </div>
      <p>${esc(final?.summary || "")}</p>
      ${final?.key_factors?.length ? `<b>Key factors</b><ul class="factors">${final.key_factors.map((f) => `<li>${esc(f)}</li>`).join("")}</ul>` : ""}
      ${executed.length ? `<h3>Actions executed</h3>${table(executed.map((x) => ({
        type: x.action.type, target: x.po_id || x.escalation_id || "",
        supplier: x.action.supplier_id || "", requested: x.expected?.qty ?? "",
        confirmed: x.actual?.qty ?? "", status: x.actual?.status || "", eta_day: x.actual?.delivery_day ?? "",
      })), ["type", "target", "supplier", "requested", "confirmed", "status", "eta_day"])}` : ""}
      ${run.status === "awaiting_approval" ? `<div class="callout info">Waiting for buyer approval — see the Approvals tab.</div>` : ""}
    </div>
    <h3>Agent trace (${run.trace.length} steps)</h3>
    <div class="timeline">${run.trace.map(renderStep).join("")}</div>`;
}

function renderStep(s) {
  const cls = s.ok === true ? "ok" : s.ok === false ? "fail" : s.kind === "policy" ? "pause" : "";
  let body = "";
  if (s.kind === "validation" || s.kind === "verification") {
    const checks = s.data.checks || [];
    body = `<ul class="checks">${checks.map((c) => `<li class="${c.passed ? "" : "fail"}">
      <span class="mono">${esc(c.code)}</span> ${esc(c.message)}</li>`).join("")}</ul>`;
  } else if (s.kind === "llm") {
    body = `<p>${esc(s.data.text)}</p>`;
  }
  const raw = `<pre>${esc(JSON.stringify(s.data, null, 2))}</pre>`;
  const open = ["validation", "verification", "llm", "policy"].includes(s.kind) ? "open" : "";
  return `<div class="step ${cls}"><details ${open}><summary><span class="kind">${esc(s.kind)}</span>${esc(s.title)}</summary>
    ${body}<details><summary class="muted">raw</summary>${raw}</details></details></div>`;
}

// ---------------------------------------------------------------------------------------
async function loadApprovals() {
  const list = await api("/api/approvals");
  $("#approvals").innerHTML = list.length ? list.map((a) => `
    <div class="card">
      <div class="row spread"><b>${esc(a.id)} · ${esc(a.case_id)}</b>
        <span class="pill ${a.status === "pending" ? "info" : a.status === "approved" ? "ok" : "bad"}">${esc(a.status)}</span></div>
      <ul class="factors">${a.reasons.map((r) => `<li>${esc(r)}</li>`).join("")}</ul>
      ${table(a.actions.map((x) => ({ type: x.type, supplier: x.supplier_id || x.po_id || "", quantity: x.quantity ?? "",
        delivery_day: x.delivery_day ?? "", reason: x.reason })), ["type", "supplier", "quantity", "delivery_day", "reason"])}
      ${a.status === "pending" ? `<div class="row" style="margin-top:10px">
        <button class="okbtn" data-approve="${esc(a.id)}">Approve & execute</button>
        <button class="badbtn" data-reject="${esc(a.id)}">Reject</button></div>`
      : `<div class="muted">by ${esc(a.decided_by)} ${esc(a.comment || "")}</div>`}
    </div>`).join("") : `<div class="empty">No approvals yet. Run CASE-S3-C to see a high-value order routed here.</div>`;
  document.querySelectorAll("[data-approve],[data-reject]").forEach((b) => b.addEventListener("click", async () => {
    const approve = !!b.dataset.approve;
    b.disabled = true;
    try {
      const run = await api(`/api/approvals/${b.dataset.approve || b.dataset.reject}`,
        { method: "POST", body: JSON.stringify({ approve, approver: "demo.buyer" }) });
      toast(`Run ${run.id}: ${run.status.replaceAll("_", " ")}`);
    } catch (e) { toast(e.message); }
    await Promise.all([loadApprovals(), loadCases(), refreshCounts()]);
  }));
}

async function loadEscalations() {
  const list = await api("/api/escalations");
  $("#escalations").innerHTML = list.length ? list.map((e) => `
    <div class="card"><div class="row spread"><b>${esc(e.case_id)}</b>
      <span class="pill ${e.kind === "cycle_count" ? "info" : "warn"}">${esc(e.kind.replaceAll("_", " "))}</span></div>
      <p>${esc(e.summary)}</p>
      ${e.options.length ? `<b>Options</b><ul class="factors">${e.options.map((o) => `<li>${esc(o)}</li>`).join("")}</ul>` : ""}
      <div class="muted mono">${esc(e.id)} · run ${esc(e.run_id)}</div></div>`).join("")
    : `<div class="empty">No escalations.</div>`;
}

async function loadPOs() {
  const list = await api("/api/purchase-orders");
  $("#pos").innerHTML = table(list, ["id", "sku_id", "node_id", "supplier_id", "qty_ordered", "qty_confirmed",
    "unit_cost", "status", "shipping_status", "expected_arrival", "created_by", "source_run_id"]);
}

async function refreshCounts() {
  const [ap, es] = await Promise.all([api("/api/approvals?status=pending"), api("/api/escalations")]);
  $("#apprCount").textContent = ap.length || "";
  $("#escCount").textContent = es.filter((e) => e.status === "open").length || "";
}

// ---------------------------------------------------------------------------------------
document.querySelectorAll(".tab").forEach((t) => t.addEventListener("click", () => {
  document.querySelectorAll(".tab").forEach((x) => x.classList.toggle("active", x === t));
  document.querySelectorAll(".tabpane").forEach((p) => p.classList.toggle("active", p.id === `tab-${t.dataset.tab}`));
  ({ approvals: loadApprovals, escalations: loadEscalations, pos: loadPOs }[t.dataset.tab] || (() => {}))();
}));

document.querySelectorAll(".segmented button").forEach((b) => b.addEventListener("click", () => {
  if (b.dataset.mode === "llm" && !state.health?.llm_available) {
    toast("Claude agent needs ANTHROPIC_API_KEY in .env. The rules agent works offline.");
    return;
  }
  state.mode = b.dataset.mode;
  document.querySelectorAll(".segmented button").forEach((x) => x.classList.toggle("active", x === b));
  if (state.selected) selectCase(state.selected);
}));

$("#resetBtn").addEventListener("click", async () => {
  if (!confirm("Reset all data to the seeded scenarios?")) return;
  await api("/api/reset", { method: "POST" });
  state.selected = null;
  $("#caseDetail").innerHTML = `<div class="empty">Data reset. Select a case.</div>`;
  await Promise.all([loadCases(), refreshCounts()]);
  toast("Data reset to seed state");
});

(async function init() {
  state.health = await api("/api/health");
  $("#simDate").textContent = `Sim date ${state.health.sim_today}`;
  if (!state.health.llm_available) $("#llmBtn").title = "Set ANTHROPIC_API_KEY to enable";
  await Promise.all([loadCases(), refreshCounts()]);
})();
