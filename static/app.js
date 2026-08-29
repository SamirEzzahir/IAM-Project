"use strict";

let currentJobId = null;
let currentJob = null;
let pollTimer = null;
let currentAssignmentJobId = null;
let assignmentPollTimer = null;
let currentBulkJobId = null;
let bulkPollTimer = null;

const $ = (id) => document.getElementById(id);

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (character) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#039;",
  })[character]);
}

function toast(message) {
  const element = $("toast");
  element.textContent = message;
  element.classList.add("show");
  clearTimeout(window.__toastTimer);
  window.__toastTimer = setTimeout(() => element.classList.remove("show"), 3500);
}

function formatTime(value) {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "—" : date.toLocaleTimeString("fr-FR", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

function formatDate(value) {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "—" : date.toLocaleString("fr-FR");
}

async function api(url, options = {}) {
  const response = await fetch(url, options);
  const data = await response.json().catch(() => ({}));
  if (!response.ok || data.ok === false) {
    const error = new Error(data.error || "Erreur de communication avec l’application.");
    error.data = data;
    throw error;
  }
  return data;
}

function switchTab(name) {
  document.querySelectorAll(".tab").forEach((element) => element.classList.remove("active"));
  document.querySelectorAll(".nav button").forEach((element) => element.classList.toggle("active", element.dataset.tab === name));
  $(`tab-${name}`).classList.add("active");
  const headings = {
    check: ["FB EMM · Contrôle PCO", "Génération SPL et vérification des ports libres"],
    assign: ["FB EMM · Affectation automatique", "Mutation du Login vers le premier port utilisable"],
    bulk: ["FB EMM · Bulk Mutation CMD&Login", "Mutation Excel avec PCO et brin exacts"],
    available: ["FB EMM · PCO disponibles", "Collection des ports disponibles pour les prochaines fonctions"],
    config: ["FB EMM · Configuration", "Paramètres locaux de WimTech et Selenium"],
  };
  $("pageTitle").textContent = headings[name][0];
  $("pageSubtitle").textContent = headings[name][1];
  if (name === "available") loadLatestAvailable();
}

async function previewSpl() {
  const spl = $("splInput").value.trim();
  if (!spl) {
    $("odfPreview").textContent = "—";
    $("zrPreview").textContent = "—";
    return;
  }
  try {
    const data = await api("/api/generate-pcos", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ spl }),
    });
    $("odfPreview").textContent = data.odf;
    $("zrPreview").textContent = data.zr;
  } catch (_) {
    $("odfPreview").textContent = "—";
    $("zrPreview").textContent = "—";
  }
}

async function previewAssignmentSpl() {
  const spl = $("assignSplInput").value.trim();
  if (!spl) {
    $("assignOdfPreview").textContent = "—";
    $("assignZrPreview").textContent = "—";
    return;
  }
  try {
    const data = await api("/api/generate-pcos", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ spl }),
    });
    $("assignOdfPreview").textContent = data.odf;
    $("assignZrPreview").textContent = data.zr;
  } catch (_) {
    $("assignOdfPreview").textContent = "—";
    $("assignZrPreview").textContent = "—";
  }
}

function statusClass(status) {
  return ({ AVAILABLE: "ok", ASSIGNED: "ok", MUTATED: "ok", SATURATED: "saturated", NOT_FOUND: "missing", BRIN_NOT_FOUND: "missing", SEARCH_FAILED: "missing", INVALID: "error", NO_MUTATION_ACTION: "warning", ERROR: "error", MUTATION_UNKNOWN: "error", UNKNOWN: "warning", SKIPPED: "skipped", PENDING: "wait" })[status] || "wait";
}

function renderRows(rows) {
  if (!rows?.length) {
    $("resultsBody").innerHTML = '<tr><td colspan="7" class="empty"><strong>Aucun contrôle lancé</strong><span>Saisissez un SPL puis lancez la vérification.</span></td></tr>';
    return;
  }
  $("resultsBody").innerHTML = rows.map((row, index) => `
    <tr>
      <td>${String(index + 1).padStart(2, "0")}</td>
      <td class="pco-code">${escapeHtml(row.pco)}</td>
      <td><span class="row-status ${statusClass(row.status)}">${escapeHtml(row.status_label)}</span></td>
      <td>${escapeHtml((row.free_ports || []).join(" · ") || "—")}</td>
      <td><strong>${Number(row.free_count || 0)}</strong></td>
      <td>${row.duration_seconds == null ? "—" : `${Number(row.duration_seconds).toFixed(1)} s`}</td>
      <td class="message-cell">${escapeHtml(row.message || "—")}</td>
    </tr>
  `).join("");
}

function renderLogs(logs) {
  if (!logs?.length) {
    $("liveLog").innerHTML = '<div class="log-line"><time>—</time><span>En attente du lancement de Selenium…</span></div>';
    return;
  }
  $("liveLog").innerHTML = logs.map((line) => `
    <div class="log-line ${escapeHtml(line.level.toLowerCase())}">
      <time>${escapeHtml(formatTime(line.time))}</time><span>${escapeHtml(line.message)}</span>
    </div>
  `).join("");
  $("liveLog").scrollTop = $("liveLog").scrollHeight;
}

function renderAssignmentRows(rows) {
  if (!rows?.length) {
    $("assignResultsBody").innerHTML = '<tr><td colspan="6" class="empty"><strong>Aucune affectation lancée</strong><span>Saisissez le Login client et son SPL.</span></td></tr>';
    return;
  }
  $("assignResultsBody").innerHTML = rows.map((row, index) => `
    <tr>
      <td>${String(index + 1).padStart(2, "0")}</td>
      <td class="pco-code">${escapeHtml(row.pco)}</td>
      <td><span class="row-status ${statusClass(row.status)}">${escapeHtml(row.status_label)}</span></td>
      <td><strong>${escapeHtml(row.selected_port || "—")}</strong></td>
      <td>${row.duration_seconds == null ? "—" : `${Number(row.duration_seconds).toFixed(1)} s`}</td>
      <td class="message-cell">${escapeHtml(row.message || "—")}</td>
    </tr>
  `).join("");
}

function renderAssignmentLogs(logs) {
  if (!logs?.length) {
    $("assignLiveLog").innerHTML = '<div class="log-line"><time>—</time><span>En attente du lancement de Selenium…</span></div>';
    return;
  }
  $("assignLiveLog").innerHTML = logs.map((line) => `
    <div class="log-line ${escapeHtml(line.level.toLowerCase())}">
      <time>${escapeHtml(formatTime(line.time))}</time><span>${escapeHtml(line.message)}</span>
    </div>
  `).join("");
  $("assignLiveLog").scrollTop = $("assignLiveLog").scrollHeight;
}

function renderAssignmentJob(job) {
  if (job.kind === "BATCH_ASSIGNMENT") {
    $("assignTotalStat").textContent = job.total;
    $("assignOutcomeStat").textContent = `${job.results.filter((row) => row.status === "ASSIGNED").length} affecté(s)`;
    $("assignMissingStat").textContent = job.results.filter((row) => row.status === "INVALID").length;
    $("assignSaturatedStat").textContent = job.results.filter((row) => row.status === "NO_PORT").length;
    $("assignProgressBar").style.width = `${job.progress_percent}%`;
    $("assignStatusBadge").textContent = ({ RUNNING: "En cours", STOPPING: "Arrêt…", STOPPED: "Arrêté", COMPLETED: "Terminé", REVIEW_REQUIRED: "À confirmer", ERROR: "Erreur", QUEUED: "Préparation" })[job.status] || job.status;
    $("assignResultsBody").innerHTML = job.results.map((row) => `<tr><td>${escapeHtml(row.excel_row)}</td><td class="pco-code">${escapeHtml(row.login)} · ${escapeHtml(row.spl)}</td><td><span class="row-status ${statusClass(row.status)}">${escapeHtml(row.status_label)}</span></td><td><strong>${escapeHtml(row.pco ? `${row.pco} / ${row.selected_port || "—"}` : "—")}</strong></td><td>${row.duration_seconds == null ? "—" : `${Number(row.duration_seconds).toFixed(1)} s`}</td><td class="message-cell">${escapeHtml(row.message || "—")}</td></tr>`).join("");
    renderAssignmentLogs(job.logs);
    const active = ["QUEUED", "RUNNING", "STOPPING"].includes(job.status);
    $("assignStartBtn").disabled = active; $("assignStopBtn").disabled = !active; $("assignClearBtn").disabled = active;
    if (!active) $("assignMessage").innerHTML = `<div class="${job.status === "ERROR" ? "error" : "success"}"><strong>Affectation en lot ${job.status === "COMPLETED" ? "terminée" : job.status.toLowerCase()}.</strong> ${job.completed_count} / ${job.total} ligne(s) traitée(s).</div>`;
    return;
  }
  const assigned = job.assigned_result;
  $("assignTotalStat").textContent = job.total;
  $("assignOutcomeStat").textContent = assigned ? `Port ${assigned.selected_port}` : (job.status === "COMPLETED" ? "Aucun" : "—");
  $("assignMissingStat").textContent = job.not_found_count;
  $("assignSaturatedStat").textContent = job.saturated_count;
  $("assignProgressBar").style.width = `${job.progress_percent}%`;
  $("assignStatusBadge").textContent = ({ RUNNING: "En cours", STOPPING: "Arrêt…", STOPPED: "Arrêté", COMPLETED: "Terminé", REVIEW_REQUIRED: "À confirmer", ERROR: "Erreur", QUEUED: "Préparation" })[job.status] || job.status;
  $("assignStatusBadge").className = `badge ${assigned ? "ok" : job.status === "REVIEW_REQUIRED" ? "error" : job.status === "ERROR" ? "error" : "neutral"}`;
  renderAssignmentRows(job.results);
  renderAssignmentLogs(job.logs);

  const active = ["QUEUED", "RUNNING", "STOPPING"].includes(job.status);
  $("assignStartBtn").disabled = active;
  $("assignStopBtn").disabled = !["QUEUED", "RUNNING"].includes(job.status);
  $("assignClearBtn").disabled = active;

  if (job.status === "COMPLETED" && assigned) {
    $("assignMessage").innerHTML = `<div class="success"><strong>Affectation terminée.</strong> Login ${escapeHtml(job.login)} affecté à <span class="pco-code">${escapeHtml(assigned.pco)}</span>, port <strong>${escapeHtml(assigned.selected_port)}</strong>.</div>`;
  } else if (job.status === "COMPLETED") {
    $("assignMessage").innerHTML = '<div class="warning"><strong>Aucun port utilisable.</strong> Consultez la liste complète ci-dessous pour voir les PCO saturés, inexistants ou ignorés.</div>';
  } else if (job.status === "REVIEW_REQUIRED") {
    $("assignMessage").innerHTML = '<div class="error"><strong>Contrôle manuel obligatoire.</strong> La mutation a commencé mais sa confirmation finale est incertaine. Vérifiez WimTech avant de relancer.</div>';
  } else if (job.status === "ERROR") {
    $("assignMessage").innerHTML = `<div class="error"><strong>Erreur :</strong> ${escapeHtml(job.error || "Affectation interrompue.")}</div>`;
  } else if (job.status === "STOPPED") {
    $("assignMessage").innerHTML = '<div class="warning"><strong>Affectation arrêtée.</strong> Les PCO non parcourus restent en attente.</div>';
  }
}

async function pollAssignmentJob() {
  if (!currentAssignmentJobId) return;
  try {
    const data = await api(`/api/assign/${currentAssignmentJobId}`);
    renderAssignmentJob(data.job);
    if (["COMPLETED", "STOPPED", "REVIEW_REQUIRED", "ERROR"].includes(data.job.status)) {
      clearInterval(assignmentPollTimer);
      assignmentPollTimer = null;
    }
  } catch (error) {
    clearInterval(assignmentPollTimer);
    assignmentPollTimer = null;
    toast(error.message);
  }
}

async function startAssignment() {
  const batchFile = $("assignBatchFile").files[0];
  if (batchFile) {
    const formData = new FormData(); formData.append("file", batchFile);
    $("assignMessage").innerHTML = '<div class="notice">Lecture du fichier Login/SPL…</div>';
    $("assignStartBtn").disabled = true;
    try {
      const data = await api("/api/assign/batch/start", { method: "POST", body: formData });
      currentAssignmentJobId = data.job_id; renderAssignmentJob(data.job);
      clearInterval(assignmentPollTimer); assignmentPollTimer = setInterval(pollAssignmentJob, 800); pollAssignmentJob();
    } catch (error) { $("assignStartBtn").disabled = false; $("assignMessage").innerHTML = `<div class="error"><strong>Erreur :</strong> ${escapeHtml(error.message)}</div>`; }
    return;
  }
  const loginList = $("assignLoginsText").value.trim();
  if (loginList) {
    $("assignMessage").innerHTML = '<div class="notice">Recherche des ports MSAN et des SPL…</div>';
    $("assignStartBtn").disabled = true;
    try {
      const data = await api("/api/assign/logins/start", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ logins: loginList }),
      });
      currentAssignmentJobId = data.job_id; renderAssignmentJob(data.job);
      clearInterval(assignmentPollTimer);
      assignmentPollTimer = setInterval(pollAssignmentJob, 800); pollAssignmentJob();
    } catch (error) {
      $("assignStartBtn").disabled = false;
      $("assignMessage").innerHTML = `<div class="error"><strong>Erreur :</strong> ${escapeHtml(error.message)}</div>`;
    }
    return;
  }
  const login = $("assignLoginInput").value.trim();
  const spl = $("assignSplInput").value.trim();
  if (!login) return toast("Saisissez le Login client.");
  if (!spl) return toast("Saisissez le SPL du client.");
  $("assignMessage").innerHTML = '<div class="notice">Préparation de l’affectation Selenium…</div>';
  $("assignStartBtn").disabled = true;
  try {
    const data = await api("/api/assign/start", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ login, spl }),
    });
    currentAssignmentJobId = data.job_id;
    renderAssignmentJob(data.job);
    clearInterval(assignmentPollTimer);
    assignmentPollTimer = setInterval(pollAssignmentJob, 800);
    pollAssignmentJob();
  } catch (error) {
    $("assignStartBtn").disabled = false;
    $("assignMessage").innerHTML = `<div class="error"><strong>Erreur :</strong> ${escapeHtml(error.message)}</div>`;
  }
}

async function stopAssignment() {
  if (!currentAssignmentJobId) return;
  try {
    await api(`/api/assign/${currentAssignmentJobId}/stop`, { method: "POST" });
    await pollAssignmentJob();
  } catch (error) {
    toast(error.message);
  }
}

function clearAssignment() {
  currentAssignmentJobId = null;
  clearInterval(assignmentPollTimer);
  assignmentPollTimer = null;
  $("assignTotalStat").textContent = "0";
  $("assignOutcomeStat").textContent = "—";
  $("assignMissingStat").textContent = "0";
  $("assignSaturatedStat").textContent = "0";
  $("assignProgressBar").style.width = "0%";
  $("assignStatusBadge").textContent = "En attente";
  $("assignStatusBadge").className = "badge neutral";
  $("assignMessage").innerHTML = "";
  $("assignBatchFile").value = "";
  $("assignLoginsText").value = "";
  renderAssignmentRows([]);
  renderAssignmentLogs([]);
  $("assignStartBtn").disabled = false;
  $("assignStopBtn").disabled = true;
}

async function resolveMsanPort() {
  const port = $("assignMsanPort").value.trim();
  if (!port) return toast("Saisissez le port MSAN.");
  try { const data = await api(`/api/config/msan-mapping/resolve?port=${encodeURIComponent(port)}`); $("assignSplInput").value = data.spl; previewAssignmentSpl(); toast(`SPL trouvé : ${data.spl}`); }
  catch (error) { toast(error.message); }
}

async function uploadMsanMapping() {
  const file = $("msanMappingFile").files[0]; if (!file) return toast("Sélectionnez le fichier Carte/SPL.");
  const formData = new FormData(); formData.append("file", file);
  try { const data = await api("/api/config/msan-mapping", { method: "POST", body: formData }); toast(`${data.count} correspondance(s) importée(s).`); }
  catch (error) { toast(error.message); }
}

function renderBulkRows(rows) {
  if (!rows?.length) {
    $("bulkResultsBody").innerHTML = '<tr><td colspan="11" class="empty"><strong>Aucun fichier traité</strong><span>Sélectionnez un fichier Excel puis lancez Bulk Mutation.</span></td></tr>';
    return;
  }
  $("bulkResultsBody").innerHTML = rows.map((row) => `
    <tr>
      <td>${escapeHtml(row.excel_row || "—")}</td>
      <td class="pco-code">${escapeHtml(row.command || "—")}</td>
      <td>${escapeHtml(row.login || "—")}</td>
      <td class="pco-code">${escapeHtml(row.pco || "—")}</td>
      <td><strong>${escapeHtml(row.brin || "—")}</strong></td>
      <td>${escapeHtml(row.search_mode || "—")}</td>
      <td>${escapeHtml(row.previous_login || "—")}</td>
      <td class="pco-code">${escapeHtml(row.spl || "—")}</td>
      <td><span class="row-status ${statusClass(row.status)}">${escapeHtml(row.status_label)}</span></td>
      <td>${row.duration_seconds == null ? "—" : `${Number(row.duration_seconds).toFixed(1)} s`}</td>
      <td class="message-cell">${escapeHtml(row.message || "—")}</td>
    </tr>
  `).join("");
}

function renderBulkLogs(logs) {
  if (!logs?.length) {
    $("bulkLiveLog").innerHTML = '<div class="log-line"><time>—</time><span>En attente du fichier Excel…</span></div>';
    return;
  }
  $("bulkLiveLog").innerHTML = logs.map((line) => `
    <div class="log-line ${escapeHtml(line.level.toLowerCase())}">
      <time>${escapeHtml(formatTime(line.time))}</time><span>${escapeHtml(line.message)}</span>
    </div>
  `).join("");
  $("bulkLiveLog").scrollTop = $("bulkLiveLog").scrollHeight;
}

function renderBulkJob(job) {
  $("bulkTotalStat").textContent = job.total;
  $("bulkSuccessStat").textContent = job.bulk_success_count;
  $("bulkFailedStat").textContent = job.bulk_failed_count;
  $("bulkProgressStat").textContent = `${job.completed_count} / ${job.total}`;
  $("bulkProgressBar").style.width = `${job.progress_percent}%`;
  $("bulkStatusBadge").textContent = ({ RUNNING: "En cours", STOPPING: "Arrêt…", STOPPED: "Arrêté", COMPLETED: "Terminé", REVIEW_REQUIRED: "À confirmer", ERROR: "Erreur", QUEUED: "Préparation" })[job.status] || job.status;
  $("bulkStatusBadge").className = `badge ${job.status === "COMPLETED" ? "ok" : ["REVIEW_REQUIRED", "ERROR"].includes(job.status) ? "error" : "neutral"}`;
  renderBulkRows(job.results);
  renderBulkLogs(job.logs);

  const active = ["QUEUED", "RUNNING", "STOPPING"].includes(job.status);
  $("bulkStartBtn").disabled = active;
  $("bulkStopBtn").disabled = !["QUEUED", "RUNNING"].includes(job.status);
  $("bulkClearBtn").disabled = active;
  $("bulkFileInput").disabled = active;

  const download = $("bulkDownloadBtn");
  download.href = job.output_available ? `/api/bulk/${job.job_id}/result.xlsx` : "#";
  download.classList.toggle("disabled", !job.output_available);

  if (job.status === "COMPLETED") {
    $("bulkMessage").innerHTML = `<div class="success"><strong>Bulk terminé.</strong> ${job.bulk_success_count} mutation(s) réussie(s), ${job.bulk_failed_count} ligne(s) non mutée(s).${job.output_available ? " Le fichier résultat est prêt." : ""}</div>`;
  } else if (job.status === "REVIEW_REQUIRED") {
    $("bulkMessage").innerHTML = '<div class="error"><strong>Contrôle manuel obligatoire.</strong> Une mutation a commencé sans confirmation finale. Le Bulk a été arrêté pour éviter une double mutation.</div>';
  } else if (job.status === "STOPPED") {
    $("bulkMessage").innerHTML = '<div class="warning"><strong>Bulk arrêté.</strong> Le fichier résultat contient les lignes déjà traitées.</div>';
  } else if (job.status === "ERROR") {
    $("bulkMessage").innerHTML = `<div class="error"><strong>Erreur :</strong> ${escapeHtml(job.error || "Traitement Bulk interrompu.")}</div>`;
  }
}

async function pollBulkJob() {
  if (!currentBulkJobId) return;
  try {
    const data = await api(`/api/bulk/${currentBulkJobId}`);
    renderBulkJob(data.job);
    if (["COMPLETED", "STOPPED", "REVIEW_REQUIRED", "ERROR"].includes(data.job.status)) {
      clearInterval(bulkPollTimer);
      bulkPollTimer = null;
    }
  } catch (error) {
    clearInterval(bulkPollTimer);
    bulkPollTimer = null;
    toast(error.message);
  }
}

async function startBulkMutation() {
  const file = $("bulkFileInput").files[0];
  if (!file) return toast("Sélectionnez un fichier Excel .xlsx ou .xlsm.");
  const formData = new FormData();
  formData.append("file", file);
  $("bulkMessage").innerHTML = '<div class="notice">Lecture du fichier puis préparation de Bulk Mutation…</div>';
  $("bulkStartBtn").disabled = true;
  try {
    const data = await api("/api/bulk/start", { method: "POST", body: formData });
    currentBulkJobId = data.job_id;
    renderBulkJob(data.job);
    clearInterval(bulkPollTimer);
    bulkPollTimer = setInterval(pollBulkJob, 900);
    pollBulkJob();
  } catch (error) {
    $("bulkStartBtn").disabled = false;
    $("bulkMessage").innerHTML = `<div class="error"><strong>Erreur :</strong> ${escapeHtml(error.message)}</div>`;
  }
}

async function stopBulkMutation() {
  if (!currentBulkJobId) return;
  try {
    await api(`/api/bulk/${currentBulkJobId}/stop`, { method: "POST" });
    await pollBulkJob();
  } catch (error) {
    toast(error.message);
  }
}

function clearBulkMutation() {
  currentBulkJobId = null;
  clearInterval(bulkPollTimer);
  bulkPollTimer = null;
  $("bulkFileInput").value = "";
  $("bulkFileInput").disabled = false;
  $("bulkFileMeta").textContent = "Aucun fichier sélectionné.";
  $("bulkTotalStat").textContent = "0";
  $("bulkSuccessStat").textContent = "0";
  $("bulkFailedStat").textContent = "0";
  $("bulkProgressStat").textContent = "0 / 0";
  $("bulkProgressBar").style.width = "0%";
  $("bulkStatusBadge").textContent = "En attente";
  $("bulkStatusBadge").className = "badge neutral";
  $("bulkMessage").innerHTML = "";
  renderBulkRows([]);
  renderBulkLogs([]);
  $("bulkStartBtn").disabled = false;
  $("bulkStopBtn").disabled = true;
  $("bulkDownloadBtn").href = "#";
  $("bulkDownloadBtn").classList.add("disabled");
}

function updateBulkFileMeta() {
  const file = $("bulkFileInput").files[0];
  $("bulkFileMeta").innerHTML = file
    ? `<strong>${escapeHtml(file.name)}</strong> · ${(file.size / 1024).toFixed(1)} Ko · colonnes requises : Commande GPON, Login, ODF, PCO, brin`
    : "Aucun fichier sélectionné.";
}

function renderJob(job) {
  currentJob = job;
  $("totalStat").textContent = job.total;
  $("availableStat").textContent = job.available_count;
  $("saturatedStat").textContent = job.saturated_count;
  $("progressStat").textContent = `${job.completed_count} / ${job.total}`;
  $("progressBar").style.width = `${job.progress_percent}%`;
  $("jobStatusBadge").textContent = ({ RUNNING: "En cours", PAUSED: "En pause", STOPPING: "Arrêt…", STOPPED: "Arrêté", COMPLETED: "Terminé", ERROR: "Erreur", QUEUED: "Préparation" })[job.status] || job.status;
  $("jobStatusBadge").className = `badge ${job.status === "COMPLETED" ? "ok" : job.status === "ERROR" ? "error" : job.status === "PAUSED" ? "warning" : "neutral"}`;
  renderRows(job.results);
  renderLogs(job.logs);

  const active = ["QUEUED", "RUNNING", "PAUSED", "STOPPING"].includes(job.status);
  $("startBtn").disabled = active;
  $("pauseBtn").disabled = job.status !== "RUNNING";
  $("resumeBtn").disabled = job.status !== "PAUSED";
  $("stopBtn").disabled = !["RUNNING", "PAUSED"].includes(job.status);
  $("clearBtn").disabled = active;
  if (job.status === "COMPLETED") {
    $("actionMessage").innerHTML = `<div class="success"><strong>Contrôle terminé.</strong> ${job.available_count} PCO disponible(s) conservé(s) pour les prochaines fonctions.</div>`;
  } else if (job.status === "ERROR") {
    $("actionMessage").innerHTML = `<div class="error"><strong>Erreur :</strong> ${escapeHtml(job.error || "Contrôle interrompu.")}</div>`;
  }
}

async function pollJob() {
  if (!currentJobId) return;
  try {
    const data = await api(`/api/check/${currentJobId}`);
    renderJob(data.job);
    if (["COMPLETED", "STOPPED", "ERROR"].includes(data.job.status)) {
      clearInterval(pollTimer);
      pollTimer = null;
      loadLatestAvailable();
    }
  } catch (error) {
    clearInterval(pollTimer);
    pollTimer = null;
    toast(error.message);
  }
}

async function startCheck() {
  const spl = $("splInput").value.trim();
  if (!spl) return toast("Saisissez un numéro SPL.");
  $("actionMessage").innerHTML = '<div class="notice">Préparation du contrôle Selenium…</div>';
  $("startBtn").disabled = true;
  try {
    const data = await api("/api/check/start", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ spl }),
    });
    currentJobId = data.job_id;
    renderJob(data.job);
    clearInterval(pollTimer);
    pollTimer = setInterval(pollJob, 800);
    pollJob();
  } catch (error) {
    $("startBtn").disabled = false;
    if (error.data?.job_id) currentJobId = error.data.job_id;
    $("actionMessage").innerHTML = `<div class="error"><strong>Erreur :</strong> ${escapeHtml(error.message)}</div>`;
  }
}

async function control(action) {
  if (!currentJobId) return;
  try {
    await api(`/api/check/${currentJobId}/${action}`, { method: "POST" });
    await pollJob();
  } catch (error) {
    toast(error.message);
  }
}

function clearResults() {
  currentJobId = null;
  currentJob = null;
  clearInterval(pollTimer);
  pollTimer = null;
  $("totalStat").textContent = "0";
  $("availableStat").textContent = "0";
  $("saturatedStat").textContent = "0";
  $("progressStat").textContent = "0 / 0";
  $("progressBar").style.width = "0%";
  $("jobStatusBadge").textContent = "En attente";
  $("jobStatusBadge").className = "badge neutral";
  $("actionMessage").innerHTML = "";
  renderRows([]);
  renderLogs([]);
  $("startBtn").disabled = false;
  $("pauseBtn").disabled = true;
  $("resumeBtn").disabled = true;
  $("stopBtn").disabled = true;
}

async function loadLatestAvailable() {
  try {
    const data = await api("/api/available/latest");
    const rows = data.available_pcos || [];
    $("availableMeta").innerHTML = data.spl
      ? `<strong>SPL ${escapeHtml(data.spl)}</strong> · ${rows.length} PCO disponible(s) · sauvegarde ${escapeHtml(formatDate(data.saved_at))}`
      : "Aucun résultat disponible pour le moment.";
    $("availableBody").innerHTML = rows.length ? rows.map((row, index) => `
      <tr><td>${String(index + 1).padStart(2, "0")}</td><td class="pco-code">${escapeHtml(row.pco)}</td><td>${escapeHtml((row.free_ports || []).join(" · ") || "—")}</td><td><strong>${Number(row.free_count || 0)}</strong></td><td>${escapeHtml(formatDate(row.checked_at))}</td></tr>
    `).join("") : '<tr><td colspan="5" class="empty"><strong>Aucun PCO disponible</strong></td></tr>';
    const link = $("downloadCsv");
    const exportJobId = data.job_id || currentJobId;
    link.href = exportJobId && rows.length ? `/api/check/${exportJobId}/available.csv` : "#";
    link.classList.toggle("disabled", !exportJobId || !rows.length);
  } catch (error) {
    toast(error.message);
  }
}

async function loadConfig() {
  try {
    const data = await api("/api/config");
    const config = data.config;
    $("wimtechUrl").value = config.wimtech_url;
    $("testLogin").value = config.test_login;
    $("timeoutSeconds").value = config.timeout_seconds;
    $("headless").checked = Boolean(config.headless);
  } catch (error) {
    toast(error.message);
  }
}

async function saveConfiguration(event) {
  event.preventDefault();
  try {
    await api("/api/config", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        wimtech_url: $("wimtechUrl").value.trim(),
        test_login: $("testLogin").value.trim(),
        timeout_seconds: Number($("timeoutSeconds").value),
        headless: $("headless").checked,
      }),
    });
    toast("Configuration enregistrée.");
  } catch (error) {
    toast(error.message);
  }
}

async function checkServer() {
  try {
    await api("/api/health");
    $("serverDot").classList.add("ok");
    $("serverStatus").textContent = "Application prête";
  } catch (_) {
    $("serverStatus").textContent = "Serveur indisponible";
  }
}

document.querySelectorAll(".nav button").forEach((button) => button.addEventListener("click", () => switchTab(button.dataset.tab)));
$("splInput").addEventListener("input", () => { clearTimeout(window.__splPreview); window.__splPreview = setTimeout(previewSpl, 250); });
$("splInput").addEventListener("keydown", (event) => { if (event.key === "Enter") startCheck(); });
$("assignSplInput").addEventListener("input", () => { clearTimeout(window.__assignSplPreview); window.__assignSplPreview = setTimeout(previewAssignmentSpl, 250); });
$("assignLoginInput").addEventListener("keydown", (event) => { if (event.key === "Enter") startAssignment(); });
$("assignSplInput").addEventListener("keydown", (event) => { if (event.key === "Enter") startAssignment(); });
$("startBtn").addEventListener("click", startCheck);
$("pauseBtn").addEventListener("click", () => control("pause"));
$("resumeBtn").addEventListener("click", () => control("resume"));
$("stopBtn").addEventListener("click", () => control("stop"));
$("clearBtn").addEventListener("click", clearResults);
$("assignStartBtn").addEventListener("click", startAssignment);
$("assignStopBtn").addEventListener("click", stopAssignment);
$("assignClearBtn").addEventListener("click", clearAssignment);
$("resolveMsanBtn").addEventListener("click", resolveMsanPort);
$("uploadMsanMappingBtn").addEventListener("click", uploadMsanMapping);
$("bulkFileInput").addEventListener("change", updateBulkFileMeta);
$("bulkStartBtn").addEventListener("click", startBulkMutation);
$("bulkStopBtn").addEventListener("click", stopBulkMutation);
$("bulkClearBtn").addEventListener("click", clearBulkMutation);
$("bulkDownloadBtn").addEventListener("click", (event) => { if (event.currentTarget.classList.contains("disabled")) event.preventDefault(); });
$("refreshAvailableBtn").addEventListener("click", loadLatestAvailable);
$("downloadCsv").addEventListener("click", (event) => { if (event.currentTarget.classList.contains("disabled")) event.preventDefault(); });
$("configForm").addEventListener("submit", saveConfiguration);

checkServer();
loadConfig();
loadLatestAvailable();
previewSpl();
previewAssignmentSpl();
