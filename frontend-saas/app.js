const API_BASE = window.XHS_AUDIT_API_BASE || window.location.origin;

const state = {
  jobs: [],
  lexicons: [],
  outputs: [],
  config: {},
  selectedJobId: "",
  view: "tasks",
};

const capabilityLabels = {
  text: "文本",
  ocr: "OCR",
  asr: "ASR",
  vision: "图片视觉",
  comment: "评论",
};

const importanceLabels = {
  off: "关闭",
  low: "较低",
  medium: "一般",
  high: "重要",
  very_high: "非常重要",
};

const importanceScores = {
  off: 0,
  low: 10,
  medium: 15,
  high: 20,
  very_high: 30,
};

const templateImportance = {
  balanced: { keyword: "high", text: "high", ocr: "high", asr: "medium", vision: "high", comment: "low" },
  strict: { keyword: "very_high", text: "high", ocr: "high", asr: "high", vision: "very_high", comment: "medium" },
  ocr_first: { keyword: "high", text: "medium", ocr: "very_high", asr: "medium", vision: "medium", comment: "low" },
  vision_first: { keyword: "high", text: "medium", ocr: "medium", asr: "medium", vision: "very_high", comment: "low" },
};

const sourceLabels = {
  keyword: "关键词/黑话命中",
  text: "文本语义",
  ocr: "OCR 命中",
  asr: "ASR 命中",
  vision: "视觉特征",
  comment: "评论聚集",
};

document.addEventListener("DOMContentLoaded", () => {
  bindEvents();
  boot();
});

async function boot() {
  await loadBaseData();
  renderLibraries();
  renderCapabilities();
  renderOutputs();
  renderRules();
  await loadJobs();
}

function bindEvents() {
  document.querySelectorAll(".nav-item").forEach((button) => {
    button.addEventListener("click", () => setNav(button.dataset.view));
  });
  document.getElementById("refreshBtn").addEventListener("click", loadJobs);
  document.getElementById("newTaskBtn").addEventListener("click", showForm);
  document.getElementById("cancelFormBtn").addEventListener("click", showTasks);
  document.getElementById("sourceMode").addEventListener("change", renderSourceMode);
  document.getElementById("keywordSource").addEventListener("change", renderSourceMode);
  document.getElementById("scoringTemplate").addEventListener("change", renderRules);
  document.getElementById("taskForm").addEventListener("submit", submitTask);
}

async function loadBaseData() {
  const [config, lexicons, outputs] = await Promise.all([
    apiGet("/api/config").catch(() => ({})),
    apiGet("/api/lexicons").catch(() => ({ categories: [] })),
    apiGet("/api/outputs").catch(() => ({ outputs: [] })),
  ]);
  state.config = config || {};
  state.lexicons = lexicons.categories || [];
  state.outputs = outputs.outputs || [];
}

async function loadJobs() {
  state.jobs = await apiGet("/api/jobs").catch(() => []);
  renderJobs();
  if (state.selectedJobId) {
    const exists = state.jobs.some((job) => job.id === state.selectedJobId);
    if (exists) {
      await selectJob(state.selectedJobId);
    } else {
      state.selectedJobId = "";
      renderEmptyDetail();
    }
  }
}

function setNav(view) {
  state.view = view;
  document.querySelectorAll(".nav-item").forEach((button) => {
    button.classList.toggle("is-active", button.dataset.view === view);
  });
  if (view === "tasks") {
    showTasks();
    return;
  }
  document.getElementById("tasksView").classList.remove("is-active");
  document.getElementById("formView").classList.remove("is-active");
  document.getElementById("placeholderView").classList.add("is-active");
  const title = { risk: "风险研判", users: "重点用户", config: "配置底座" }[view] || "暂未开放";
  document.getElementById("pageTitle").textContent = title;
  document.getElementById("pageSub").textContent = "当前版本先处理监控任务入口";
  document.getElementById("placeholderTitle").textContent = title;
}

function showTasks() {
  document.getElementById("tasksView").classList.add("is-active");
  document.getElementById("formView").classList.remove("is-active");
  document.getElementById("placeholderView").classList.remove("is-active");
  document.getElementById("pageTitle").textContent = "监控任务";
  document.getElementById("pageSub").textContent = "创建和查看多知识库综合审核任务";
}

function showForm() {
  document.getElementById("tasksView").classList.remove("is-active");
  document.getElementById("formView").classList.add("is-active");
  document.getElementById("placeholderView").classList.remove("is-active");
  document.getElementById("pageTitle").textContent = "新建监控任务";
  document.getElementById("pageSub").textContent = "配置知识库、检测能力和风险评分规则";
  renderSourceMode();
}

function renderJobs() {
  document.getElementById("taskCount").textContent = `${state.jobs.length} 个任务`;
  const list = document.getElementById("taskList");
  if (!state.jobs.length) {
    list.innerHTML = '<div class="empty-state">暂无任务</div>';
    return;
  }
  list.innerHTML = state.jobs.map((job) => `
    <button class="task-item ${job.id === state.selectedJobId ? "is-selected" : ""}" data-job-id="${escapeHtml(job.id)}">
      <div class="task-title">
        <span>${escapeHtml(job.display_name || job.keyword || job.id)}</span>
        <span class="status ${escapeHtml(job.status || "")}">${escapeHtml(job.status || "unknown")}</span>
      </div>
      <div class="task-meta">
        <span>${escapeHtml(job.platform || "-")}</span>
        <span>${escapeHtml((job.library_ids || [job.lexicon_category]).filter(Boolean).join(" / ") || "-")}</span>
        <span>${escapeHtml(job.created_at || "")}</span>
      </div>
    </button>
  `).join("");
  list.querySelectorAll(".task-item").forEach((item) => {
    item.addEventListener("click", () => selectJob(item.dataset.jobId));
  });
}

async function selectJob(jobId) {
  state.selectedJobId = jobId;
  const [job, results] = await Promise.all([
    apiGet(`/api/jobs/${jobId}`),
    apiGet(`/api/jobs/${jobId}/audit-results?limit=20&sort=risk`).catch(() => ({ items: [], total: 0 })),
  ]);
  renderJobs();
  renderDetail(job, results.items || [], results.total || 0);
}

function renderEmptyDetail() {
  document.getElementById("detailPanel").innerHTML = '<div class="empty-state">选择一个任务查看详情，或新建监控任务</div>';
}

function renderDetail(job, results, total) {
  const libraries = (job.library_ids || [job.lexicon_category]).filter(Boolean);
  const capabilities = (job.capabilities || []).map((item) => capabilityLabels[item] || item);
  const rules = ((job.rule_snapshot || {}).scoring_rules || []).slice(0, 8);
  const thresholds = (job.rule_snapshot || {}).thresholds || {};
  document.getElementById("detailPanel").innerHTML = `
    <div class="detail-body">
      <div class="detail-head">
        <div>
          <h2>${escapeHtml(job.display_name || job.keyword || job.id)}</h2>
          <div class="chip-row">
            <span class="status ${escapeHtml(job.status || "")}">${escapeHtml(job.status || "unknown")}</span>
            <span>${escapeHtml(job.platform || "-")}</span>
            <span>${escapeHtml(job.id)}</span>
          </div>
        </div>
        <div class="detail-actions">
          <button class="btn ghost" data-action="backfill_analysis">继续分析</button>
          <button class="btn ghost" data-action="pause_crawl">停采集</button>
          <button class="btn ghost" data-action="pause_analysis">停分析</button>
          <button class="btn danger" data-action="stop_all">停止</button>
          <button class="btn danger" data-action="delete">删除</button>
        </div>
      </div>

      <div class="detail-grid">
        <div class="metric"><span>知识库</span><strong>${escapeHtml(libraries.join(" / ") || "-")}</strong></div>
        <div class="metric"><span>检测能力</span><strong>${escapeHtml(capabilities.join(" / ") || "-")}</strong></div>
        <div class="metric"><span>Prompt</span><strong>${escapeHtml((job.prompt_profile_snapshot || {}).prompt_version || "-")}</strong></div>
      </div>

      <div class="score-grid">
        <div class="mini-panel"><span>高危</span><strong>${escapeHtml(thresholds.high ?? "-")}</strong></div>
        <div class="mini-panel"><span>中危</span><strong>${escapeHtml(thresholds.medium ?? "-")}</strong></div>
        <div class="mini-panel"><span>待复核</span><strong>${escapeHtml(thresholds.review ?? "-")}</strong></div>
      </div>

      <section>
        <h3>评分规则</h3>
        <div class="rules-table">
          ${rules.length ? rules.map((rule) => `
            <div class="rule-row">
              <div><strong>${escapeHtml(rule.label || rule.id)}</strong><small>${escapeHtml(rule.category || rule.library_id || "")}</small></div>
              <div>${escapeHtml(sourceLabels[rule.source] || rule.source || "-")}</div>
              <div>${escapeHtml(importanceLabels[rule.importance] || rule.importance || "-")} · ${escapeHtml(rule.score ?? 0)} 分</div>
            </div>
          `).join("") : '<div class="rule-row"><div>旧任务未保存评分规则</div></div>'}
        </div>
      </section>

      <section>
        <h3>审核结果 ${total ? `(${total})` : ""}</h3>
        <div class="result-list">
          ${results.length ? results.map(renderResultItem).join("") : '<div class="empty-state">暂无审核结果</div>'}
        </div>
      </section>

      <section>
        <h3>任务日志</h3>
        <div class="log-list">
          ${(job.logs || []).slice(-8).reverse().map((log) => `
            <div class="log-item"><strong>${escapeHtml(log.time || "")}</strong><br>${escapeHtml(log.message || "")}</div>
          `).join("") || '<div class="empty-state">暂无日志</div>'}
        </div>
      </section>
    </div>
  `;
  document.querySelectorAll(".detail-actions [data-action]").forEach((button) => {
    button.addEventListener("click", () => runJobAction(job.id, button.dataset.action));
  });
}

function renderResultItem(item) {
  const categoryScores = item.category_scores || [];
  const breakdown = item.score_breakdown || [];
  return `
    <article class="result-item">
      <div class="result-title">
        <strong>${escapeHtml(item.content_title || item.title || item.note_id || "未命名内容")}</strong>
        <span class="status ${escapeHtml(item.risk_level || "")}">${escapeHtml(item.risk_level || "unknown")} · ${escapeHtml(item.risk_score ?? 0)} 分</span>
      </div>
      <div class="task-meta">
        <span>${escapeHtml(item.decision || "-")}</span>
        <span>主风险：${escapeHtml(item.primary_risk || (item.categories || [])[0] || "-")}</span>
      </div>
      <p class="muted">${escapeHtml(item.summary || "")}</p>
      ${categoryScores.length ? `<div class="score-breakdown">${categoryScores.map((score) => `
        <div class="breakdown-row"><span>${escapeHtml(score.category)} · ${escapeHtml(score.level)}</span><strong>${escapeHtml(score.score)} 分</strong></div>
      `).join("")}</div>` : ""}
      ${breakdown.length ? `<div class="score-breakdown">${breakdown.slice(0, 5).map((rule) => `
        <div class="breakdown-row"><span>${escapeHtml(rule.rule || rule.rule_id || "-")}</span><strong>+${escapeHtml(rule.score ?? 0)}</strong></div>
      `).join("")}</div>` : ""}
    </article>
  `;
}

function renderLibraries() {
  const grid = document.getElementById("libraryGrid");
  grid.innerHTML = state.lexicons.map((item, index) => `
    <label class="choice-card">
      <input type="checkbox" name="library" value="${escapeHtml(item.id)}" ${index === 0 ? "checked" : ""} />
      <strong>${escapeHtml(item.title || item.id)}</strong>
      <small>${escapeHtml((item.chips || []).slice(0, 4).join("、") || "暂无关键词")}</small>
    </label>
  `).join("");
  grid.querySelectorAll('input[name="library"]').forEach((input) => {
    input.addEventListener("change", renderRules);
  });
}

function renderCapabilities() {
  const defaults = (((state.config || {}).risk_rule_defaults || {}).capabilities || ["text", "ocr", "asr", "vision", "comment"]);
  const grid = document.getElementById("capabilityGrid");
  grid.innerHTML = defaults.map((item) => `
    <label class="segment">
      <input type="checkbox" name="capability" value="${escapeHtml(item)}" checked />
      <span>${escapeHtml(capabilityLabels[item] || item)}</span>
    </label>
  `).join("");
  grid.querySelectorAll('input[name="capability"]').forEach((input) => {
    input.addEventListener("change", renderRules);
  });
}

function renderOutputs() {
  const select = document.getElementById("sourceOutput");
  if (!state.outputs.length) {
    select.innerHTML = '<option value="">暂无已有输出</option>';
    return;
  }
  select.innerHTML = state.outputs.map((item) => {
    const id = item.id || item.name || item.output_id || "";
    const label = item.label || item.name || id;
    return `<option value="${escapeHtml(id)}">${escapeHtml(label)}</option>`;
  }).join("");
}

function renderSourceMode() {
  const mode = document.getElementById("sourceMode").value;
  document.querySelectorAll(".search-only").forEach((node) => node.classList.toggle("is-hidden", mode !== "search"));
  document.querySelectorAll(".creator-only").forEach((node) => node.classList.toggle("is-hidden", mode !== "creator"));
  document.querySelectorAll(".existing-only").forEach((node) => node.classList.toggle("is-hidden", mode !== "existing"));
  const keywordInput = document.getElementById("keyword");
  keywordInput.disabled = mode === "search" && document.getElementById("keywordSource").value === "lexicon";
}

function renderRules() {
  const libraries = selectedLibraries();
  const capabilities = selectedCapabilities();
  const template = document.getElementById("scoringTemplate").value;
  const rules = buildScoringRules(libraries, capabilities, template);
  const table = document.getElementById("rulesTable");
  if (!rules.length) {
    table.innerHTML = '<div class="rule-row"><div>至少选择一个知识库和检测能力</div></div>';
    return;
  }
  table.innerHTML = rules.map((rule) => `
    <div class="rule-row" data-rule-id="${escapeHtml(rule.id)}">
      <div>
        <strong>${escapeHtml(rule.label)}</strong>
        <small>${escapeHtml(rule.category)}</small>
      </div>
      <select data-field="importance">
        ${Object.entries(importanceLabels).map(([value, label]) => `
          <option value="${value}" ${value === rule.importance ? "selected" : ""}>${label}</option>
        `).join("")}
      </select>
      <div><strong data-score>${rule.score}</strong> 分</div>
    </div>
  `).join("");
  table.querySelectorAll('select[data-field="importance"]').forEach((select) => {
    select.addEventListener("change", () => {
      const row = select.closest(".rule-row");
      row.querySelector("[data-score]").textContent = importanceScores[select.value] || 0;
    });
  });
}

function buildScoringRules(libraries, capabilities, template) {
  const sourceOrder = ["keyword"];
  if (capabilities.includes("text")) sourceOrder.push("text");
  capabilities.filter((item) => item !== "text").forEach((item) => sourceOrder.push(item));
  const config = templateImportance[template] || templateImportance.balanced;
  const rules = [];
  libraries.forEach((library) => {
    const title = (library.title || library.id).replace("词库", "").replace("知识包", "").trim();
    sourceOrder.forEach((source) => {
      const importance = config[source] || "medium";
      const score = importanceScores[importance] || 0;
      if (score <= 0) return;
      rules.push({
        id: `${source}_${library.id}`,
        label: `${title}${sourceLabels[source] || source}`,
        source,
        library_id: library.id,
        category: title,
        importance,
        score,
      });
    });
  });
  return rules;
}

function currentRulesFromDom() {
  return Array.from(document.querySelectorAll("#rulesTable .rule-row[data-rule-id]")).map((row) => {
    const ruleId = row.dataset.ruleId;
    const [source, ...libraryParts] = ruleId.split("_");
    const libraryId = libraryParts.join("_");
    const library = state.lexicons.find((item) => item.id === libraryId) || {};
    const title = (library.title || libraryId).replace("词库", "").replace("知识包", "").trim();
    const importance = row.querySelector('select[data-field="importance"]').value;
    return {
      id: ruleId,
      label: row.querySelector("strong").textContent.trim(),
      source,
      library_id: libraryId,
      category: title,
      importance,
      score: importanceScores[importance] || 0,
    };
  }).filter((rule) => rule.score > 0);
}

async function submitTask(event) {
  event.preventDefault();
  const sourceMode = document.getElementById("sourceMode").value;
  const libraryIds = selectedLibraries().map((item) => item.id);
  const capabilities = selectedCapabilities();
  const payload = {
    display_name: document.getElementById("displayName").value.trim(),
    platform: document.getElementById("platform").value,
    crawl_mode: sourceMode === "creator" ? "creator" : "search",
    keyword: document.getElementById("keyword").value.trim(),
    keyword_source: sourceMode === "search" ? document.getElementById("keywordSource").value : "keyword",
    creator_url: document.getElementById("creatorUrl").value.trim(),
    start_page: numberValue("startPage", 1),
    max_notes: numberValue("maxNotes", 20),
    max_comments: 100,
    max_concurrency: 1,
    analyze_limit: numberValue("analyzeLimit", 20),
    run_crawler: sourceMode !== "existing",
    source_output_id: sourceMode === "existing" ? document.getElementById("sourceOutput").value : null,
    analysis_batch_size: 5,
    lexicon_category: libraryIds[0] || "soft",
    library_ids: libraryIds,
    capabilities,
    scoring_template: document.getElementById("scoringTemplate").value,
    rule_snapshot: {
      thresholds: {
        high: numberValue("thresholdHigh", 80),
        medium: numberValue("thresholdMedium", 60),
        review: numberValue("thresholdReview", 40),
      },
      scoring_rules: currentRulesFromDom(),
    },
  };
  if (payload.keyword_source === "lexicon") {
    payload.keyword = "";
  }
  await apiPost("/api/jobs", payload);
  event.target.reset();
  renderLibraries();
  renderCapabilities();
  renderRules();
  showTasks();
  await loadJobs();
}

async function runJobAction(jobId, action) {
  if (action === "delete") {
    await apiDelete(`/api/jobs/${jobId}`);
    state.selectedJobId = "";
    renderEmptyDetail();
    await loadJobs();
    return;
  }
  await apiPost(`/api/jobs/${jobId}/control`, { action });
  await loadJobs();
}

function selectedLibraries() {
  const ids = Array.from(document.querySelectorAll('input[name="library"]:checked')).map((input) => input.value);
  return state.lexicons.filter((item) => ids.includes(item.id));
}

function selectedCapabilities() {
  return Array.from(document.querySelectorAll('input[name="capability"]:checked')).map((input) => input.value);
}

function numberValue(id, fallback) {
  const value = Number(document.getElementById(id).value);
  return Number.isFinite(value) ? value : fallback;
}

async function apiGet(path) {
  const response = await fetch(`${API_BASE}${path}`);
  if (!response.ok) throw new Error(await response.text());
  return response.json();
}

async function apiPost(path, body) {
  const response = await fetch(`${API_BASE}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!response.ok) throw new Error(await response.text());
  return response.json();
}

async function apiDelete(path) {
  const response = await fetch(`${API_BASE}${path}`, { method: "DELETE" });
  if (!response.ok) throw new Error(await response.text());
  return response.json();
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}
