(() => {
  const state = {
    currentView: "overview",
    currentTaskId: null,
    timer: null,
    history: [],
    approvals: []
  };

  const $ = (sel) => document.querySelector(sel);
  const $$ = (sel) => [...document.querySelectorAll(sel)];

  function escapeHtml(value) {
    return String(value ?? "").replace(/[&<>"']/g, ch => ({
      "&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#39;"
    }[ch]));
  }

  function showToast(message) {
    const wrap = $("#toastStack");
    const el = document.createElement("div");
    el.className = "toast";
    el.textContent = message;
    wrap.appendChild(el);
    setTimeout(() => el.remove(), 3500);
  }

  async function api(path, options = {}) {
    const res = await fetch(path, { cache: "no-store", ...options });
    const data = await res.json().catch(() => ({ error: "Invalid server response" }));
    if (!res.ok) throw new Error(data.error || data.reason || "Request failed");
    return data;
  }

  function badge(state) {
    const value = String(state || "unknown").toLowerCase();
    return '<span class="badge ' + escapeHtml(value) + '">' + escapeHtml(value.replace("_"," ")) + '</span>';
  }

  function switchView(view) {
    state.currentView = view;
    $$(".nav-item").forEach(btn => btn.classList.toggle("active", btn.dataset.view === view));
    $$(".view").forEach(el => el.classList.toggle("hidden", el.id !== "view-" + view));
    const titles = {overview:"Agent Overview",tasks:"Task Center",history:"Execution History",approvals:"Approval Queue"};
    $("#pageTitle").textContent = titles[view] || "Agent Overview";
  }

  async function loadHealth() {
    try {
      const data = await api("/health");
      $("#healthText").textContent = data.status === "ok" ? "Runtime online" : "Runtime attention";
      $("#rootLabel").textContent = data.root_label || "Local workspace";
    } catch (err) {
      $("#healthText").textContent = "Runtime offline";
    }
  }

  async function loadTasks() {
    const data = await api("/api/tasks");
    const active = data.tasks.filter(t => ["queued","running"].includes(t.state));
    $("#statActive").textContent = active.length;
    if (data.root_label) $("#rootLabel").textContent = data.root_label;

    const current = active[0] || null;
    $("#liveBadge").textContent = current ? current.state.toUpperCase() : "IDLE";
    if (!current) {
      $("#liveTask").innerHTML = '<div class="empty-state">No task is running.</div>';
    } else {
      $("#liveTask").innerHTML =
        '<div class="live-card">' +
          '<div class="live-title">' + escapeHtml(current.task) + '</div>' +
          '<div class="live-meta"><span>' + escapeHtml(current.task_id) + '</span><span>•</span><span>' + escapeHtml(current.execution_id) + '</span></div>' +
          '<div class="progress-line"><span></span></div>' +
        '</div>';
    }

    const html = data.tasks.map(t =>
      '<div class="task-item">' +
        '<div class="row"><div class="task-name">' + escapeHtml(t.task) + '</div>' + badge(t.state) + '</div>' +
        '<div class="meta">Task ' + escapeHtml(t.task_id) + ' · Execution ' + escapeHtml(t.execution_id) + '</div>' +
        (t.reason ? '<div class="detail-box">' + escapeHtml(t.reason) + '</div>' : '') +
      '</div>'
    ).join("");
    $("#taskTable").innerHTML = html || '<div class="empty-state">No tasks have been submitted yet.</div>';
  }

  async function loadHistory() {
    const data = await api("/api/history?limit=30");
    state.history = data.records || [];
    const verified = state.history.filter(r => r.state === "verified").length;
    const blocked = state.history.filter(r => ["blocked","failed"].includes(r.state)).length;
    $("#statVerified").textContent = verified;
    $("#statBlocked").textContent = blocked;

    const recent = state.history.slice(0, 6).map(r =>
      '<div class="history-item">' +
        '<div class="row"><div class="task-name">' + escapeHtml(r.task) + '</div>' + badge(r.state) + '</div>' +
        '<div class="meta">' + escapeHtml(r.recorded_at) + ' · attempts ' + escapeHtml(r.attempts) + '</div>' +
        '<div class="detail-box">' + escapeHtml(r.reason) + '</div>' +
      '</div>'
    ).join("");
    $("#recentHistory").innerHTML = recent || '<div class="empty-state">No execution history yet.</div>';

    const full = state.history.map(r =>
      '<div class="task-item">' +
        '<div class="row"><div class="task-name">' + escapeHtml(r.task) + '</div>' + badge(r.state) + '</div>' +
        '<div class="meta">Execution ' + escapeHtml(r.execution_id) + ' · ' + escapeHtml(r.recorded_at) + ' · attempts ' + escapeHtml(r.attempts) + '</div>' +
        '<div class="detail-box">' + escapeHtml(r.reason) + '</div>' +
      '</div>'
    ).join("");
    $("#historyTable").innerHTML = full || '<div class="empty-state">No execution history yet.</div>';
  }

  async function loadApprovals() {
    const data = await api("/api/approvals");
    state.approvals = data.actions || [];
    $("#statApprovals").textContent = data.pending_count || 0;
    $("#approvalList").innerHTML = state.approvals.length
      ? state.approvals.map(a =>
          '<div class="approval-item">' +
            '<div class="row"><div class="task-name">' + escapeHtml(a.task) + '</div><div class="risk">' + escapeHtml(a.risk) + '</div></div>' +
            '<div class="meta">Action ' + escapeHtml(a.id) + ' · ' + escapeHtml(a.status) + ' · ' + escapeHtml(a.created_at || "unknown time") + '</div>' +
            '<div class="detail-box">' + escapeHtml(a.reason) + '</div>' +
          '</div>'
        ).join("")
      : '<div class="empty-state">No pending approvals.</div>';
  }

  async function refresh() {
    await Promise.allSettled([loadHealth(), loadTasks(), loadHistory(), loadApprovals()]);
  }

  async function submitTask(task) {
    const cleaned = String(task || "").trim();
    if (!cleaned) {
      showToast("Write a task first.");
      return;
    }
    $("#runTaskButton").disabled = true;
    try {
      const data = await api("/api/tasks", {
        method: "POST",
        headers: {"Content-Type":"application/json"},
        body: JSON.stringify({task: cleaned})
      });
      $("#taskInput").value = "";
      state.currentTaskId = data.task_id;
      switchView("overview");
      showToast("Task accepted. Agent is running.");
      await refresh();
    } catch (err) {
      showToast(err.message);
    } finally {
      $("#runTaskButton").disabled = false;
    }
  }

  $$(".nav-item").forEach(btn => btn.addEventListener("click", () => switchView(btn.dataset.view)));
  $$(".quick-button").forEach(btn => btn.addEventListener("click", () => { $("#taskInput").value = btn.dataset.task; $("#taskInput").focus(); }));
  $$(".text-button").forEach(btn => btn.addEventListener("click", () => switchView(btn.dataset.viewTarget)));
  $("#runTaskButton").addEventListener("click", () => submitTask($("#taskInput").value));
  $("#focusTaskButton").addEventListener("click", () => { switchView("overview"); $("#taskInput").focus(); });
  $("#refreshButton").addEventListener("click", refresh);
  $("#taskInput").addEventListener("keydown", (event) => {
    if ((event.ctrlKey || event.metaKey) && event.key === "Enter") submitTask($("#taskInput").value);
  });

  refresh();
  state.timer = setInterval(refresh, 1800);
})();
