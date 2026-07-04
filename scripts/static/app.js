const state = {
  currentLog: "dashboard",
  pending: null,
  jobs: [],
  providers: null,
  balance: null,
};

const $ = (selector) => document.querySelector(selector);
const usdFormatter = new Intl.NumberFormat(undefined, {
  style: "currency",
  currency: "USD",
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});

async function requestJson(url, options = {}) {
  const res = await fetch(url, { cache: "no-store", ...options });
  if (res.status === 204) return null;
  const text = await res.text();
  const payload = text ? JSON.parse(text) : null;
  if (!res.ok) {
    throw new Error(payload?.detail || `${res.status} ${res.statusText}`);
  }
  return payload;
}

function postJson(url, body = {}) {
  return requestJson(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

function fmtDate(value) {
  if (!value) return "--";
  const date = typeof value === "number" ? new Date(value * 1000) : new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString();
}

function setMedia(container, url, type) {
  container.innerHTML = "";
  if (!url) {
    const empty = document.createElement("div");
    empty.className = "media-empty";
    empty.textContent = "No file";
    container.appendChild(empty);
    return;
  }
  const el = type === "video" ? document.createElement("video") : document.createElement("img");
  el.src = url;
  el.loading = "lazy";
  if (type === "video") {
    el.controls = true;
    el.muted = true;
  }
  container.appendChild(el);
}

const BASE_TITLE = document.title;
let faviconLink = null;

function updateAttentionCue(hasPending) {
  document.title = hasPending ? `(1) ${BASE_TITLE}` : BASE_TITLE;
  if (!faviconLink) {
    faviconLink = document.createElement("link");
    faviconLink.rel = "icon";
    document.head.appendChild(faviconLink);
  }
  const canvas = document.createElement("canvas");
  canvas.width = 32;
  canvas.height = 32;
  const ctx = canvas.getContext("2d");
  ctx.fillStyle = hasPending ? "#f3bf4f" : "#41d180";
  ctx.beginPath();
  ctx.arc(16, 16, 12, 0, Math.PI * 2);
  ctx.fill();
  faviconLink.href = canvas.toDataURL("image/png");
}

function renderPending(payload) {
  const pending = payload.pending;
  state.pending = pending;
  updateAttentionCue(Boolean(pending));
  const empty = $("#pendingEmpty");
  const body = $("#pendingBody");
  const stage = $("#pendingStage");
  if (!pending) {
    empty.classList.remove("hidden");
    body.classList.add("hidden");
    stage.textContent = "None";
    stage.dataset.kind = "muted";
    return;
  }
  empty.classList.add("hidden");
  body.classList.remove("hidden");
  stage.textContent = pending.stage || "pending";
  stage.dataset.kind = pending.stage || "pending";
  $("#pendingId").textContent = pending.id || "--";
  $("#pendingAttempt").textContent = pending.attempt || "--";
  $("#pendingUpdated").textContent = fmtDate(pending.updated_at);
  $("#pendingNote").textContent = pending.processing_note || "--";
  $("#approveBtn").textContent = pending.stage === "frame" ? "Generate Still" : "Animate";
  $("#rejectBtn").textContent = pending.stage === "frame" ? "Clear Frame" : "Regenerate";
  setMedia($("#framePreview"), pending.frame1_url, "image");
  setMedia($("#avatarPreview"), pending.avatar_frame_url, "image");
}

function renderStatus(payload) {
  $("#updatedAt").textContent = fmtDate(payload.updated_at);
  const counts = $("#statusCounts");
  counts.innerHTML = "";
  Object.entries(payload.counts || {}).forEach(([name, count]) => {
    const pill = document.createElement("span");
    pill.className = "pill small";
    pill.textContent = `${count} ${name}`;
    counts.appendChild(pill);
  });

  const tbody = $("#statusRows");
  tbody.innerHTML = "";
  (payload.rows || []).forEach((row) => {
    const tr = document.createElement("tr");
    const output = row.output_media_url
      ? `<a href="${row.output_media_url}" target="_blank" rel="noreferrer">Open</a>`
      : "";
    const action = String(row.status || "").startsWith("flagged")
      ? `<button class="inline-action" type="button" data-unflag="${escapeHtml(row.id || "")}">Unflag</button>`
      : "";
    tr.innerHTML = `
      <td><span class="status-text">${escapeHtml(row.status || "")}</span></td>
      <td><code>${escapeHtml(row.id || "")}</code></td>
      <td>${escapeHtml(row.note || "")}</td>
      <td>${escapeHtml(row.processing_note || "")}</td>
      <td>${output}</td>
      <td>${action}</td>
    `;
    tbody.appendChild(tr);
  });
}

function renderServices(payload) {
  const list = $("#servicesList");
  list.innerHTML = "";
  (payload.services || []).forEach((service) => {
    const row = document.createElement("div");
    row.className = "service-row";
    row.innerHTML = `
      <span class="dot" data-status="${escapeHtml(service.status || "unknown")}"></span>
      <div>
        <strong>${escapeHtml(service.name || "")}</strong>
        <small>${escapeHtml(service.detail || service.url || service.label || "")}</small>
      </div>
    `;
    list.appendChild(row);
  });
}

function renderProviders(payload) {
  state.providers = payload;
  fillSelect($("#avatarProvider"), payload.options.avatar_frame_provider, payload.effective.avatar_frame_provider);
  fillSelect($("#animationProvider"), payload.options.animation_provider, payload.effective.animation_provider);
  $("#wavespeedEnabled").checked = Boolean(payload.effective.wavespeed_enabled);

  $("#avatarProviderBase").textContent = providerBaseText(
    payload,
    "avatar_frame_provider",
    payload.base.avatar_frame_provider,
  );
  $("#animationProviderBase").textContent = providerBaseText(
    payload,
    "animation_provider",
    payload.base.animation_provider,
  );
  $("#wavespeedBase").textContent = providerBaseText(
    payload,
    "wavespeed_enabled",
    payload.base.wavespeed_enabled ? "true" : "false",
  );

  const badge = $("#providerOverlayBadge");
  badge.textContent = payload.overlay_exists ? "override" : "config.yaml";
  badge.dataset.kind = payload.overlay_exists ? "avatar" : "muted";
}

function renderBalance(payload) {
  state.balance = payload;
  const value = $("#wavespeedBalanceValue");
  const status = $("#wavespeedBalanceStatus");
  const detail = $("#wavespeedBalanceDetail");
  const checked = $("#wavespeedBalanceChecked");
  const link = $("#wavespeedDashboardLink");

  link.href = payload.dashboard_url || "https://wavespeed.ai";
  checked.textContent = payload.checked_at ? `Checked ${fmtDate(payload.checked_at)}` : "--";

  if (payload.ok) {
    value.textContent = usdFormatter.format(Number(payload.balance || 0));
    status.textContent = payload.enabled ? "enabled" : "disabled";
    status.dataset.kind = payload.enabled ? "ok" : "muted";
    detail.textContent = payload.enabled
      ? "Paid providers are enabled."
      : "API key is set, but paid providers are disabled.";
    return;
  }

  value.textContent = payload.configured ? "Unavailable" : "Not configured";
  status.textContent = payload.configured ? "error" : "setup";
  status.dataset.kind = payload.configured ? "error" : "muted";
  detail.textContent = payload.detail || `Set ${payload.api_key_env || "WAVESPEED_API_KEY"}.`;
}

function fillSelect(select, options, value) {
  const previous = select.value;
  select.innerHTML = "";
  options.forEach((optionValue) => {
    const option = document.createElement("option");
    option.value = optionValue;
    option.textContent = optionValue;
    select.appendChild(option);
  });
  select.value = value || previous || options[0];
}

function providerBaseText(payload, key, baseValue) {
  return payload.overridden[key] ? `base: ${baseValue}` : "base value";
}

function renderJobs(payload) {
  state.jobs = payload.jobs || [];
  const list = $("#jobsList");
  list.innerHTML = "";
  if (!state.jobs.length) {
    list.innerHTML = '<div class="empty compact">No jobs yet.</div>';
    return;
  }
  state.jobs.slice(0, 8).forEach((job) => {
    const row = document.createElement("div");
    row.className = "job-row";
    const detail = job.result?.status || job.error || job.state;
    row.innerHTML = `
      <span class="dot" data-status="${jobDot(job.state)}"></span>
      <div>
        <strong>${escapeHtml(job.name)}</strong>
        <small>${escapeHtml(job.state)}${job.run_id ? ` · ${escapeHtml(job.run_id)}` : ""}</small>
        <small>${escapeHtml(detail || "")}</small>
      </div>
    `;
    list.appendChild(row);
  });

  const active = state.jobs.find((job) => ["queued", "running"].includes(job.state) && job.run_id);
  if (active) ensureRunLogSelected(active.run_id);
}

function jobDot(jobState) {
  if (jobState === "done") return "ok";
  if (jobState === "error") return "error";
  return "unknown";
}

function ensureRunLogSelected(runId) {
  const value = `run:${runId}`;
  const select = $("#logSelect");
  if (![...select.options].some((option) => option.value === value)) {
    const option = document.createElement("option");
    option.value = value;
    option.textContent = value;
    select.prepend(option);
  }
  if (state.currentLog !== value) {
    state.currentLog = value;
    select.value = value;
  }
}

function renderLog(payload) {
  const lines = payload.lines || [];
  $("#logOutput").textContent = lines.length ? lines.join("\n") : "(empty)";
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

async function refreshAll() {
  const [pending, status, services, jobs, providers] = await Promise.all([
    requestJson("/api/pending"),
    requestJson("/api/status"),
    requestJson("/api/services"),
    requestJson("/api/jobs"),
    requestJson("/api/config/providers"),
  ]);
  renderPending(pending);
  renderStatus(status);
  renderServices(services);
  renderJobs(jobs);
  renderProviders(providers);
  await refreshLog();
}

async function refreshLog() {
  try {
    const payload = await requestJson(`/api/logs/${encodeURIComponent(state.currentLog)}?lines=200`);
    renderLog(payload);
  } catch (err) {
    $("#logOutput").textContent = String(err.message || err);
  }
}

async function refreshBalance(force = false) {
  const suffix = force ? "?force=true" : "";
  try {
    const payload = await requestJson(`/api/wavespeed/balance${suffix}`);
    renderBalance(payload);
  } catch (err) {
    renderBalance({
      ok: false,
      configured: false,
      enabled: false,
      balance: null,
      detail: String(err.message || err),
      dashboard_url: "https://wavespeed.ai",
    });
  }
}

function renderTailscale(payload) {
  const node = payload.node || {};
  const serve = payload.serve || {};
  const status = $("#tailscaleStatus");

  $("#tsHostname").textContent = node.hostname || "--";
  $("#tsTailnet").textContent = node.tailnet || "--";
  $("#tsServed").textContent = (serve.served_ports || []).join(", ") || "none";
  $("#tsFunneled").textContent = (serve.funneled_ports || []).join(", ") || "none";

  const detail = $("#tailscaleDetail");
  if (!node.available) {
    status.textContent = "unavailable";
    status.dataset.kind = "error";
    detail.textContent = node.detail || "tailscale CLI not reachable";
  } else if (!node.online) {
    status.textContent = "offline";
    status.dataset.kind = "error";
    detail.textContent = "Logged out or disconnected — Funnel/Serve are down.";
  } else {
    status.textContent = "online";
    status.dataset.kind = "ok";
    detail.textContent = serve.available ? "" : serve.detail || "";
  }

  $("#funnelWarning").classList.toggle("hidden", !serve.dashboard_funneled);
}

function renderRunpod(payload) {
  const status = $("#runpodStatus");
  const list = $("#runpodList");
  const detail = $("#runpodDetail");
  list.innerHTML = "";

  if (!payload.configured) {
    status.textContent = "setup";
    status.dataset.kind = "muted";
    detail.textContent = "Set RUNPOD_API_KEY in .env to see pods.";
    return;
  }
  if (!payload.ok) {
    status.textContent = "error";
    status.dataset.kind = "error";
    detail.textContent = payload.detail || "RunPod API error";
    return;
  }

  const pods = payload.pods || [];
  if (!pods.length) {
    status.textContent = "no pods";
    status.dataset.kind = "ok";
    detail.textContent = "No pods exist — nothing is billing.";
    return;
  }

  const running = pods.filter((p) => p.status === "RUNNING").length;
  status.textContent = running ? `${running} running` : "all stopped";
  status.dataset.kind = running ? "error" : "ok";
  pods.forEach((pod) => {
    const row = document.createElement("div");
    row.className = "service-row";
    const dot = pod.status === "RUNNING" ? "error" : "ok";
    const cost = pod.cost_per_hr ? ` · ${usdFormatter.format(pod.cost_per_hr)}/hr` : "";
    row.innerHTML = `
      <span class="dot" data-status="${dot}"></span>
      <div>
        <strong>${escapeHtml(pod.name || pod.id || "")}</strong>
        <small>${escapeHtml(pod.status || "")} · ${escapeHtml(pod.gpu_type || "")}${cost}</small>
      </div>
    `;
    list.appendChild(row);
  });
  detail.textContent = payload.running_cost_per_hr
    ? `Billing ${usdFormatter.format(payload.running_cost_per_hr)}/hr while running.`
    : "Stopped pods may still bill for disk.";
}

function renderCosines(payload) {
  const svg = $("#cosineChart");
  const summary = $("#cosineSummary");
  const detail = $("#cosineDetail");
  const points = payload.points || [];
  const threshold = Number(payload.threshold || 0);

  if (!points.length) {
    svg.innerHTML = "";
    summary.textContent = "no data";
    summary.dataset.kind = "muted";
    detail.textContent = "No identity-gate scores recorded yet.";
    return;
  }

  const width = 280;
  const height = 88;
  const pad = 6;
  const x = (i) =>
    points.length === 1
      ? width / 2
      : pad + (i * (width - pad * 2)) / (points.length - 1);
  const y = (v) => height - pad - Math.min(Math.max(v, 0), 1) * (height - pad * 2);

  const passes = points.filter((p) => p.cosine >= threshold).length;
  const thresholdY = y(threshold);
  let markup = `<line x1="0" x2="${width}" y1="${thresholdY}" y2="${thresholdY}" class="cosine-threshold"></line>`;
  if (points.length > 1) {
    const path = points.map((p, i) => `${x(i)},${y(p.cosine)}`).join(" ");
    markup += `<polyline points="${path}" class="cosine-line"></polyline>`;
  }
  points.forEach((p, i) => {
    const cls = p.cosine >= threshold ? "cosine-dot pass" : "cosine-dot fail";
    markup += `<circle cx="${x(i)}" cy="${y(p.cosine)}" r="3" class="${cls}"><title>${escapeHtml(p.id)} ${p.cosine.toFixed(4)} (${escapeHtml(p.date)})</title></circle>`;
  });
  svg.innerHTML = markup;

  summary.textContent = `${passes}/${points.length} ≥ ${threshold}`;
  summary.dataset.kind = passes ? "ok" : "error";
  const latest = points[points.length - 1];
  detail.textContent = `Latest: ${latest.id} at ${latest.cosine.toFixed(4)} — threshold ${threshold} (identity.cosine_min).`;
}

async function refreshInfra() {
  const results = await Promise.allSettled([
    requestJson("/api/tailscale"),
    requestJson("/api/runpod/pods"),
    requestJson("/api/cosines"),
  ]);
  if (results[0].status === "fulfilled") renderTailscale(results[0].value);
  if (results[1].status === "fulfilled") renderRunpod(results[1].value);
  if (results[2].status === "fulfilled") renderCosines(results[2].value);
}

function showMessage(text, tone = "neutral") {
  const el = $("#actionMessage");
  el.textContent = text;
  el.dataset.tone = tone;
}

async function submitLink(event) {
  event.preventDefault();
  const url = $("#linkInput").value.trim();
  if (!url) return;
  try {
    const job = await postJson("/api/links", { url });
    showMessage(`Queued ${job.name} (${job.id})`, "ok");
    $("#linkInput").value = "";
    await refreshAll();
  } catch (err) {
    showMessage(String(err.message || err), "error");
  }
}

async function runNext() {
  try {
    const job = await postJson("/api/queue/run-next");
    if (job === null) {
      showMessage("Queue is empty.", "neutral");
    } else {
      showMessage(`Queued ${job.name} (${job.id})`, "ok");
    }
    await refreshAll();
  } catch (err) {
    showMessage(String(err.message || err), "error");
  }
}

async function decide(decision) {
  if (!state.pending) return;
  const pending = state.pending;
  const label = decision === "yes" ? $("#approveBtn").textContent : $("#rejectBtn").textContent;
  if (!window.confirm(`${label} for ${pending.id}?`)) return;
  try {
    const job = await postJson(`/api/pending/${encodeURIComponent(pending.id)}/decision`, {
      stage: pending.stage,
      decision,
    });
    showMessage(`Queued ${job.name} (${job.id})`, "ok");
    await refreshAll();
  } catch (err) {
    showMessage(String(err.message || err), "error");
  }
}

async function unflag(id) {
  if (!window.confirm(`Unflag ${id}?`)) return;
  try {
    await postJson(`/api/flagged/${encodeURIComponent(id)}/unflag`);
    showMessage(`Unflagged ${id}`, "ok");
    await refreshAll();
  } catch (err) {
    showMessage(String(err.message || err), "error");
  }
}

async function saveProviders() {
  const body = {
    avatar_frame_provider: $("#avatarProvider").value,
    animation_provider: $("#animationProvider").value,
    wavespeed_enabled: $("#wavespeedEnabled").checked,
  };
  if (body.wavespeed_enabled && !window.confirm("Enable WaveSpeed-backed providers?")) return;
  try {
    const payload = await requestJson("/api/config/providers", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    renderProviders(payload);
    showMessage("Provider override saved.", "ok");
  } catch (err) {
    showMessage(String(err.message || err), "error");
  }
}

async function revertProviders() {
  if (!window.confirm("Revert providers to config.yaml?")) return;
  try {
    const payload = await requestJson("/api/config/providers", { method: "DELETE" });
    renderProviders(payload);
    showMessage("Provider override removed.", "ok");
  } catch (err) {
    showMessage(String(err.message || err), "error");
  }
}

$("#refreshBtn").addEventListener("click", () => refreshAll().catch(showMessage));
$("#logSelect").addEventListener("change", (event) => {
  state.currentLog = event.target.value;
  refreshLog().catch(showMessage);
});
$("#linkForm").addEventListener("submit", submitLink);
$("#runNextBtn").addEventListener("click", () => runNext());
$("#approveBtn").addEventListener("click", () => decide("yes"));
$("#rejectBtn").addEventListener("click", () => decide("no"));
$("#saveProvidersBtn").addEventListener("click", () => saveProviders());
$("#revertProvidersBtn").addEventListener("click", () => revertProviders());
$("#refreshBalanceBtn").addEventListener("click", () => refreshBalance(true));
$("#statusRows").addEventListener("click", (event) => {
  const id = event.target?.dataset?.unflag;
  if (id) unflag(id);
});

refreshAll().catch((err) => showMessage(String(err.message || err), "error"));
refreshBalance();
refreshInfra();
setInterval(() => refreshAll().catch((err) => showMessage(String(err.message || err), "error")), 8000);
setInterval(() => refreshBalance(), 60000);
setInterval(() => refreshInfra(), 60000);
