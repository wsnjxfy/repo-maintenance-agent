"use strict";

const $ = (id) => document.getElementById(id);
let CATALOG = null;
let CURRENT = null;      // active run id
let SOURCE = null;       // EventSource

async function getJSON(url) {
  const r = await fetch(url);
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || r.statusText);
  return r.json();
}

async function postJSON(url, body) {
  const r = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || r.statusText);
  return r.json();
}

function taskEntry(id) {
  return CATALOG.tasks.find((t) => t.task_id === id);
}

function renderTools(entry) {
  const box = $("tools");
  box.innerHTML = "";
  const enabled = new Set(entry.default_tools);
  for (const tool of CATALOG.tools) {
    const row = document.createElement("label");
    row.className = "tool-row";
    const cb = document.createElement("input");
    cb.type = "checkbox";
    cb.value = tool.name;
    cb.checked = enabled.has(tool.name) || tool.required;
    if (tool.required) { cb.checked = true; cb.disabled = true; row.classList.add("disabled"); }
    cb.addEventListener("change", validateSelection);

    const name = document.createElement("span");
    name.className = "name";
    name.textContent = tool.name;
    row.append(cb, name);

    const tags = [];
    if (tool.is_edit) tags.push("编辑");
    if (tool.executes_code) tags.push("执行代码");
    if (tool.required) tags.push("必选");
    for (const t of tags) {
      const s = document.createElement("span");
      s.className = "tag"; s.textContent = t; row.append(s);
    }

    if (tool.doc) {
      const tip = document.createElement("span");
      tip.className = "tip";
      tip.textContent = tool.doc;         // styled hover box
      row.append(tip);
    }
    box.append(row);
  }
  validateSelection();
}

function selectedTools() {
  return [...document.querySelectorAll("#tools input:checked")].map((c) => c.value);
}

function validateSelection() {
  const tools = selectedTools();
  const hasEdit = CATALOG.edit_tools.some((t) => tools.includes(t));
  const err = $("error");
  if (!hasEdit) {
    err.textContent = "至少勾选一个编辑工具（replace_text 或 apply_patch）。";
    $("start").disabled = true;
  } else {
    err.textContent = "";
    $("start").disabled = CURRENT !== null;
  }
}

function fillPolicies(entry) {
  const sel = $("policy");
  sel.innerHTML = "";
  for (const p of entry.policies) {
    const o = document.createElement("option");
    o.value = p; o.textContent = p; sel.append(o);
  }
}

function onTaskChange() {
  const entry = taskEntry($("task").value);
  $("task-notes").textContent = entry.notes || "";
  fillPolicies(entry);
  renderTools(entry);
  $("preflight-list").innerHTML = "";
}

async function runPreflight() {
  const task = $("task").value;
  const policy = $("policy").value;
  const budget = $("budget").value;
  const q = new URLSearchParams({ policy });
  if (budget) q.set("budget_usd", budget);
  const list = $("preflight-list");
  list.innerHTML = "<li>检查中…</li>";
  try {
    const { checks } = await getJSON(`/api/tasks/${task}/preflight?${q}`);
    list.innerHTML = "";
    for (const c of checks) {
      const li = document.createElement("li");
      li.innerHTML = `<span class="dot ${c.ok ? "ok" : "bad"}">${c.ok ? "●" : "●"}</span>` +
        `<span>${c.name}</span> <span class="muted">${c.detail || ""}</span>`;
      list.append(li);
    }
  } catch (e) {
    list.innerHTML = `<li class="bad">${e.message}</li>`;
  }
}

function resetResults() {
  $("steps").innerHTML = "";
  $("log").textContent = "";
  $("status-bar").innerHTML = "";
  $("result-summary").innerHTML = "";
  const f = $("report"); f.hidden = true; f.removeAttribute("src");
}

function addStep(step) {
  const li = document.createElement("li");
  li.className = step.ok === false ? "bad" : "ok";
  li.textContent = `${step.tool} → ${step.ok === false ? "error" : "ok"}` +
    (step.phase ? `  (${step.phase})` : "");
  $("steps").append(li);
}

function renderStatus(summary) {
  if (!summary) return;
  const bar = $("status-bar");
  const t = summary.tokens ? `${summary.tokens.prompt}+${summary.tokens.completion}` : "—";
  bar.innerHTML =
    `<span>步数 <b>${summary.steps ?? "—"}</b></span>` +
    `<span>actions <b>${summary.actions ?? "—"}</b></span>` +
    `<span>tokens <b>${t}</b></span>` +
    `<span>花费 <b>$${summary.cost_usd ?? 0}</b></span>` +
    `<span>status <b>${summary.status ?? "—"}</b></span>`;
}

function renderResult(runId, summary) {
  if (!summary) return;
  renderStatus(summary);
  const div = $("result-summary");
  const resolved = summary.resolved;
  const ev = summary.evaluation || {};
  const reqs = ev.requirements, regs = ev.regressions;
  let f2p = reqs && reqs.total != null ? `F2P ${reqs.passed}/${reqs.total}` : "";
  let p2p = regs && regs.total != null ? `P2P ${regs.passed}/${regs.total}` : "";
  const art = (name) => `/api/runs/${runId}/artifacts/${name}`;
  div.innerHTML =
    `<p class="resolved-${resolved}">resolved: ${resolved}</p>` +
    (f2p || p2p ? `<p>${f2p} ${p2p}</p>` : "") +
    `<p>` +
    `<a href="${art("report.html")}" target="_blank">report.html</a> · ` +
    `<a href="${art("summary.json")}" target="_blank">summary.json</a>` +
    (summary.final_patch ? ` · <a href="${art(summary.final_patch)}" target="_blank">final.patch</a>` : "") +
    (summary.llm_log ? ` · <a href="${art(summary.llm_log)}" target="_blank">llm_calls.jsonl</a>` : "") +
    `</p>`;
  const f = $("report");
  f.src = art("report.html");
  f.hidden = false;
}

function startStream(runId) {
  SOURCE = new EventSource(`/api/runs/${runId}/stream`);
  SOURCE.addEventListener("log", (e) => {
    const log = $("log");
    log.textContent += JSON.parse(e.data).line + "\n";
    log.scrollTop = log.scrollHeight;
  });
  SOURCE.addEventListener("step", (e) => addStep(JSON.parse(e.data)));
  SOURCE.addEventListener("done", (e) => {
    const d = JSON.parse(e.data);
    renderResult(runId, d.summary);
    finishRun();
    loadHistory();
  });
  SOURCE.onerror = () => { /* server closes the stream after done */ };
}

function finishRun() {
  if (SOURCE) { SOURCE.close(); SOURCE = null; }
  CURRENT = null;
  $("cancel").disabled = true;
  validateSelection();
}

async function start() {
  resetResults();
  const entry = taskEntry($("task").value);
  const body = {
    task: $("task").value,
    policy: $("policy").value,
    tools: selectedTools(),
    model: $("model").value || null,
    max_steps: parseInt($("max-steps").value, 10) || null,
    budget_usd: parseFloat($("budget").value) || null,
  };
  $("error").textContent = "";
  try {
    const { id } = await postJSON("/api/runs", body);
    CURRENT = id;
    $("start").disabled = true;
    $("cancel").disabled = false;
    startStream(id);
  } catch (e) {
    $("error").textContent = e.message;
  }
}

async function cancel() {
  if (!CURRENT) return;
  await postJSON(`/api/runs/${CURRENT}/cancel`, {}).catch(() => {});
}

async function loadHistory() {
  try {
    const { runs } = await getJSON("/api/runs");
    const tb = $("history").querySelector("tbody");
    tb.innerHTML = "";
    for (const r of runs) {
      const s = r.summary || {};
      const tr = document.createElement("tr");
      const time = new Date(r.created * 1000).toLocaleTimeString();
      const report = r.run_dir
        ? `<a href="/api/runs/${r.id}/artifacts/report.html" target="_blank">report</a>` : "";
      tr.innerHTML = `<td>${time}</td><td>${r.config.task}</td><td>${r.config.policy}</td>` +
        `<td class="muted">${r.config.tools.length} 个</td><td>${r.status}</td>` +
        `<td class="resolved-${s.resolved}">${s.resolved ?? ""}</td><td>${report}</td>`;
      tb.append(tr);
    }
  } catch (e) { /* ignore */ }
}

async function init() {
  CATALOG = await getJSON("/api/catalog");
  const sel = $("task");
  for (const t of CATALOG.tasks) {
    const o = document.createElement("option");
    o.value = t.task_id; o.textContent = t.title; sel.append(o);
  }
  sel.addEventListener("change", onTaskChange);
  $("policy").addEventListener("change", () => { $("preflight-list").innerHTML = ""; });
  $("preflight-btn").addEventListener("click", runPreflight);
  $("start").addEventListener("click", start);
  $("cancel").addEventListener("click", cancel);
  onTaskChange();
  loadHistory();
}

init().catch((e) => { $("error").textContent = e.message; });
