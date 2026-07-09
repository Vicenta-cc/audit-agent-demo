let currentJobId = null;
let pollTimer = null;
let jobsById = new Map();
let currentItems = [];
let auditView = {
  mode: "live",
  liveItems: [],
  viewItems: [],
  lastResultId: 0,
  bufferedCount: 0,
  bloggerIdKeyword: "",
  bloggerNicknameKeyword: "",
  userSort: "default",
  itemSort: "default",
};
let selectedUserKey = "";
let selectedResultId = "";
let profileExpandedUserKey = "";
let activeWorkspaceView = "tasks";
let activeLexiconCategory = "soft";
let activeLexiconPanel = "keywords";
let promptProfileDirty = false;
let activeDetailTab = "audit";
let logsFollowTail = true;
const RAW_COMMENT_LIMIT = 5;
const DEFAULT_START_PAGE = 1;
const DEFAULT_MAX_NOTES = 10000;
const DEFAULT_MAX_COMMENTS = 1000;
const DEFAULT_MAX_CONCURRENCY = 1;
const DEFAULT_ANALYZE_LIMIT = DEFAULT_MAX_NOTES;
const LOG_BOTTOM_THRESHOLD = 32;

const apiBase = (window.XHS_AUDIT_API_BASE || "").replace(/\/$/, "");
const navItems = [...document.querySelectorAll(".nav-item")];
const workspaceViews = [...document.querySelectorAll(".workspace-view")];
const categoryPaneEl = document.getElementById("category-pane");
const workspaceTitleEl = document.getElementById("workspace-title");
const workspaceSubtitleEl = document.getElementById("workspace-subtitle");
const lexiconTitleEl = document.getElementById("lexicon-title");
const keywordChipsEl = document.getElementById("keyword-chips");
const keywordTableBodyEl = document.getElementById("keyword-table-body");
const keywordTableHeadRowEl = keywordTableBodyEl?.closest("table")?.querySelector("thead tr");
const addKeywordButtonEl = document.getElementById("add-keyword-button");
const lexiconTabButtons = [...document.querySelectorAll(".lexicon-tab")];
const lexiconKeywordsPanelEl = document.getElementById("lexicon-keywords-panel");
const lexiconPromptPanelEl = document.getElementById("lexicon-prompt-panel");
let keywordFormEl = document.getElementById("keyword-form");
let newKeywordEl = document.getElementById("new-keyword");
let newKeywordMatchTypeEl = document.getElementById("new-keyword-match-type");
let newKeywordEnabledEl = document.getElementById("new-keyword-enabled");
let cancelKeywordButtonEl = document.getElementById("cancel-keyword-button");
const promptProfileFormEl = document.getElementById("prompt-profile-form");
const promptImagePromptEl = document.getElementById("prompt-image-prompt");
const promptFramePromptEl = document.getElementById("prompt-frame-prompt");
const promptFusionPromptEl = document.getElementById("prompt-fusion-prompt");
const promptProfileDirtyEl = document.getElementById("prompt-profile-dirty");
const resetPromptProfileButtonEl = document.getElementById("reset-prompt-profile-button");
const savePromptProfileButtonEl = document.getElementById("save-prompt-profile-button");
const form = document.getElementById("job-form");
const localVideoForm = document.getElementById("local-video-form");
const statusEl = document.getElementById("job-status") || { textContent: "" };
const logsEl = document.getElementById("logs");
const taskStateGridEl = document.getElementById("task-state-grid");
const controlButtons = [...document.querySelectorAll(".job-controls button")];
const jobListEl = document.getElementById("job-list");
const refreshJobsButtonEl = document.getElementById("refresh-jobs-button");
const openAuditButtonEl = document.getElementById("open-audit-button");
const userListEl = document.getElementById("user-list");
const listEl = document.getElementById("item-list");
const detailEl = document.getElementById("detail");
const auditContentEl = document.querySelector("#view-audit .content");
const auditSnapshotBarEl = document.getElementById("audit-snapshot-bar");
const auditSnapshotTextEl = document.getElementById("audit-snapshot-text");
const auditApplyUpdatesButtonEl = document.getElementById("audit-apply-updates");
const auditLiveButtonEl = document.getElementById("audit-live-mode");
const auditBloggerIdKeywordEl = document.getElementById("audit-blogger-id-keyword");
const auditBloggerNicknameKeywordEl = document.getElementById("audit-blogger-nickname-keyword");
const auditClearSearchButtonEl = document.getElementById("audit-clear-search");
const auditUserRiskSortButtonEl = document.getElementById("audit-user-risk-sort");
const auditItemRiskSortButtonEl = document.getElementById("audit-item-risk-sort");
const runCrawlerEl = document.getElementById("run-crawler");
const sourceOutputEl = document.getElementById("source-output");
const platformEl = document.getElementById("platform");
const crawlModeEl = document.getElementById("crawl-mode");
const taskLexiconFieldEl = document.getElementById("task-lexicon-field");
const taskLexiconLabelEl = document.getElementById("task-lexicon-label");
const taskLexiconEl = document.getElementById("task-lexicon");
const creatorFieldEl = document.getElementById("creator-field");
const creatorUrlEl = document.getElementById("creator-url");
const creatorPlaceholders = {
  xhs: "小红书完整主页 URL，最好带 xsec_token/xsec_source",
  dy: "抖音博主主页 URL",
  ks: "快手博主主页 URL",
};
const workspaceCopy = {
  lexicon: {
    title: "词库管理",
    subtitle: "维护风险分类、关键词和黑话规则",
  },
  tasks: {
    title: "任务管理",
    subtitle: "抓取任务、上传审核和任务运行状态",
  },
  audit: {
    title: "审核研判",
    subtitle: "用户列表、帖子列表、内容证据和用户画像",
  },
};

document.querySelectorAll(".status-pill").forEach(element => element.remove());
let lexiconData = {
  soft: {
    title: "软色情词库",
    chips: ["擦边", "福利", "私拍", "原味", "泳装"],
    keywords: [
      { id: 0, keyword: "泳装", match_type: "模糊", enabled: true, hit_count_7d: 0 },
      { id: 0, keyword: "私拍", match_type: "精确", enabled: true, hit_count_7d: 0 },
      { id: 0, keyword: "擦边", match_type: "模糊", enabled: true, hit_count_7d: 0 },
    ],
    prompt_profile: emptyPromptProfile("soft"),
  },
  gambling: {
    title: "赌博黑话词库",
    chips: ["上分", "回血", "盘口", "带飞", "庄"],
    keywords: [
      { id: 0, keyword: "上分", match_type: "模糊", enabled: true, hit_count_7d: 0 },
      { id: 0, keyword: "盘口", match_type: "精确", enabled: true, hit_count_7d: 0 },
      { id: 0, keyword: "回血", match_type: "模糊", enabled: false, hit_count_7d: 0 },
    ],
    prompt_profile: emptyPromptProfile("gambling"),
  },
  fraud: {
    title: "涉诈话术词库",
    chips: ["刷流水", "返利", "兼职", "认证金", "解冻"],
    keywords: [
      { id: 0, keyword: "刷流水", match_type: "精确", enabled: true, hit_count_7d: 0 },
      { id: 0, keyword: "认证金", match_type: "模糊", enabled: true, hit_count_7d: 0 },
      { id: 0, keyword: "返利", match_type: "模糊", enabled: true, hit_count_7d: 0 },
    ],
    prompt_profile: emptyPromptProfile("fraud"),
  },
  minority: {
    title: "民族语言词库",
    chips: ["维语待标注", "敏感短语 A", "敏感短语 B"],
    keywords: [
      { id: 0, keyword: "维语待标注", match_type: "tag", enabled: true, hit_count_7d: 0 },
      { id: 0, keyword: "敏感短语 A", match_type: "模糊", enabled: false, hit_count_7d: 0 },
      { id: 0, keyword: "敏感短语 B", match_type: "正则", enabled: true, hit_count_7d: 0 },
    ],
    prompt_profile: emptyPromptProfile("minority"),
  },
};

ensureKeywordForm();
renderLexiconTableHead();
loadOutputs();
loadLexicons();
loadJobs();
navItems.forEach(item => {
  item.addEventListener("click", () => setWorkspaceView(item.dataset.view));
});
setWorkspaceView(activeWorkspaceView);
updateSourceOutputState();
updateCrawlModeState();
runCrawlerEl.addEventListener("change", updateSourceOutputState);
crawlModeEl.addEventListener("change", updateCrawlModeState);
platformEl.addEventListener("change", updateCrawlModeState);
taskLexiconEl.addEventListener("change", updateKeywordSourceState);
lexiconTabButtons.forEach(button => {
  button.addEventListener("click", () => setLexiconPanel(button.dataset.lexiconPanel || "keywords"));
});
addKeywordButtonEl.addEventListener("click", () => {
  const hidden = keywordFormEl?.classList.contains("hidden");
  setKeywordFormVisible(Boolean(hidden));
});
if (keywordFormEl) {
  keywordFormEl.addEventListener("submit", addKeywordToActiveLexicon);
}
if (cancelKeywordButtonEl) {
  cancelKeywordButtonEl.addEventListener("click", () => setKeywordFormVisible(false));
}
if (promptProfileFormEl) {
  promptProfileFormEl.addEventListener("submit", savePromptProfile);
}
[promptImagePromptEl, promptFramePromptEl, promptFusionPromptEl].forEach(input => {
  input?.addEventListener("input", markPromptProfileDirty);
});
if (resetPromptProfileButtonEl) {
  resetPromptProfileButtonEl.addEventListener("click", resetPromptProfile);
}
controlButtons.forEach(button => {
  button.addEventListener("click", () => sendJobControl(button.dataset.action));
});
if (auditApplyUpdatesButtonEl) {
  auditApplyUpdatesButtonEl.addEventListener("click", refreshSnapshot);
}
if (auditLiveButtonEl) {
  auditLiveButtonEl.addEventListener("click", returnToLiveMode);
}
if (auditBloggerIdKeywordEl) {
  auditBloggerIdKeywordEl.addEventListener("input", () => {
    enterSnapshotMode();
    auditView.bloggerIdKeyword = auditBloggerIdKeywordEl.value;
    renderAuditQueryControls();
    renderList();
  });
}
if (auditBloggerNicknameKeywordEl) {
  auditBloggerNicknameKeywordEl.addEventListener("input", () => {
    enterSnapshotMode();
    auditView.bloggerNicknameKeyword = auditBloggerNicknameKeywordEl.value;
    renderAuditQueryControls();
    renderList();
  });
}
if (auditClearSearchButtonEl) {
  auditClearSearchButtonEl.addEventListener("click", () => {
    enterSnapshotMode();
    auditView.bloggerIdKeyword = "";
    auditView.bloggerNicknameKeyword = "";
    renderAuditQueryControls();
    renderList();
  });
}
if (auditUserRiskSortButtonEl) {
  auditUserRiskSortButtonEl.addEventListener("click", () => {
    enterSnapshotMode();
    auditView.userSort = auditView.userSort === "risk" ? "default" : "risk";
    renderAuditQueryControls();
    renderList();
  });
}
if (auditItemRiskSortButtonEl) {
  auditItemRiskSortButtonEl.addEventListener("click", () => {
    enterSnapshotMode();
    auditView.itemSort = auditView.itemSort === "risk" ? "default" : "risk";
    renderAuditQueryControls();
    renderList();
  });
}
if (refreshJobsButtonEl) {
  refreshJobsButtonEl.addEventListener("click", () => loadJobs({ keepSelection: true }));
}
if (logsEl) {
  logsEl.addEventListener("scroll", () => {
    logsFollowTail = isLogsAtBottom();
  }, { passive: true });
}
if (openAuditButtonEl) {
  openAuditButtonEl.addEventListener("click", () => {
    if (!currentJobId) {
      statusEl.textContent = "请先选择一个任务";
      return;
    }
    setWorkspaceView("audit");
  });
}
updateControlButtons(null);
renderTaskState(null);
renderAuditSnapshotBar();
renderAuditQueryControls();

async function loadLexicons() {
  try {
    const response = await apiFetch("/api/lexicons");
    const data = await response.json();
    if (!response.ok) {
      throw new Error(JSON.stringify(data));
    }
    const categories = data.categories || [];
    if (categories.length) {
      lexiconData = categoriesToLexiconData(categories);
      activeLexiconCategory = categories[0].id;
      promptProfileDirty = false;
    }
  } catch (error) {
    logsEl.textContent = `读取词库失败，已使用内置词库：${error}`;
  }
  renderLexiconNav();
  renderLexiconOptions();
  renderLexicon();
}

function categoriesToLexiconData(categories) {
  return Object.fromEntries(categories.map(category => [
    category.id,
    normalizeLexiconCategory(category),
  ]));
}

function normalizeLexiconCategory(category) {
  const keywords = (category.keywords || []).map(normalizeKeyword);
  return {
    title: category.title || category.id || "词库",
    chips: category.chips || keywords.slice(0, 12).map(item => item.keyword),
    keywords,
    prompt_profile: normalizePromptProfile(category.prompt_profile, category.id),
  };
}

function normalizeKeyword(item) {
  return {
    id: Number(item.id || 0),
    category_id: item.category_id || "",
    keyword: item.keyword || "",
    match_type: item.match_type || "模糊",
    platform: item.platform || "全平台",
    risk_level: item.risk_level || "中",
    enabled: Boolean(item.enabled),
    hit_count_7d: Number(item.hit_count_7d || 0),
    note: item.note || "",
  };
}

function emptyPromptProfile(categoryId) {
  return normalizePromptProfile(null, categoryId);
}

function normalizePromptProfile(profile, categoryId) {
  const preview = profile?.preview || {};
  return {
    category_id: profile?.category_id || categoryId || "",
    image_prompt: profile?.image_prompt || preview.image_prompt || "",
    frame_prompt: profile?.frame_prompt || preview.frame_prompt || "",
    fusion_prompt_template: profile?.fusion_prompt_template || preview.fusion_prompt_template || "",
    version: Number(profile?.version || 0),
    prompt_version: profile?.prompt_version || "-",
    updated_at: profile?.updated_at || "",
    preview: {
      image_prompt: preview.image_prompt || profile?.image_prompt || "",
      frame_prompt: preview.frame_prompt || profile?.frame_prompt || "",
      fusion_prompt_template: preview.fusion_prompt_template || profile?.fusion_prompt_template || "",
    },
  };
}

function renderLexiconTableHead() {
  const row = keywordTableHeadRowEl || keywordTableBodyEl?.closest("table")?.querySelector("thead tr");
  if (!row) return;
  row.innerHTML = ["关键词", "匹配方式", "状态", "近 7 天命中", "操作"]
    .map(label => `<th>${label}</th>`)
    .join("");
}

function ensureKeywordForm() {
  if (keywordFormEl) return;
  const panelHead = addKeywordButtonEl?.closest(".panel-head");
  if (!panelHead) return;
  panelHead.insertAdjacentHTML("afterend", `
    <form id="keyword-form" class="keyword-form hidden">
      <label class="keyword-field">
        关键词
        <input id="new-keyword" placeholder="输入关键词" />
      </label>
      <label>
        匹配方式
        <select id="new-keyword-match-type">
          <option value="模糊">模糊</option>
          <option value="精确">精确</option>
          <option value="正则">正则</option>
          <option value="tag">tag</option>
        </select>
      </label>
      <label>
        状态
        <span class="inline-checkbox">
          <input id="new-keyword-enabled" type="checkbox" checked />
          启用
        </span>
      </label>
      <div class="keyword-form-actions">
        <button class="secondary-button" type="button" id="cancel-keyword-button">取消</button>
        <button type="submit">保存</button>
      </div>
    </form>
  `);
  keywordFormEl = document.getElementById("keyword-form");
  newKeywordEl = document.getElementById("new-keyword");
  newKeywordMatchTypeEl = document.getElementById("new-keyword-match-type");
  newKeywordEnabledEl = document.getElementById("new-keyword-enabled");
  cancelKeywordButtonEl = document.getElementById("cancel-keyword-button");
}

function setKeywordFormVisible(visible) {
  if (!keywordFormEl) return;
  keywordFormEl.classList.toggle("hidden", !visible);
  addKeywordButtonEl.textContent = visible ? "收起" : "新增关键词";
  if (visible) {
    newKeywordEl?.focus();
  } else {
    keywordFormEl.reset();
    if (newKeywordEnabledEl) newKeywordEnabledEl.checked = true;
    if (newKeywordMatchTypeEl) newKeywordMatchTypeEl.value = "模糊";
  }
}

async function addKeywordToActiveLexicon(event) {
  event.preventDefault();
  const keyword = newKeywordEl?.value.trim() || "";
  if (!keyword) {
    newKeywordEl?.focus();
    return;
  }
  const response = await apiFetch("/api/lexicon-keywords", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      category_id: activeLexiconCategory,
      keyword,
      match_type: newKeywordMatchTypeEl?.value || "模糊",
      enabled: Boolean(newKeywordEnabledEl?.checked),
    }),
  });
  const data = await response.json();
  if (!response.ok) {
    statusEl.textContent = "新增关键词失败";
    logsEl.textContent = JSON.stringify(data, null, 2);
    return;
  }
  setKeywordFormVisible(false);
  applyLexiconCategories(data.categories || []);
}

async function toggleKeywordEnabled(keywordId, currentStatus) {
  const response = await apiFetch(`/api/lexicon-keywords/${keywordId}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ enabled: currentStatus !== "启用" }),
  });
  const data = await response.json();
  if (!response.ok) {
    statusEl.textContent = "更新关键词失败";
    logsEl.textContent = JSON.stringify(data, null, 2);
    return;
  }
  applyLexiconCategories(data.categories || []);
}

window.toggleKeywordEnabled = toggleKeywordEnabled;

async function deleteKeyword(keywordId, keyword) {
  const confirmed = window.confirm(`删除词「${keyword}」？\n\n删除后不会出现在词库和后续任务选词中。`);
  if (!confirmed) return;
  const response = await apiFetch(`/api/lexicon-keywords/${keywordId}`, { method: "DELETE" });
  const data = await response.json();
  if (!response.ok) {
    statusEl.textContent = "删除关键词失败";
    logsEl.textContent = JSON.stringify(data, null, 2);
    return;
  }
  if ((data.categories || []).length) {
    applyLexiconCategories(data.categories || []);
  } else {
    await loadLexicons();
  }
}

window.deleteKeyword = deleteKeyword;

function applyLexiconCategories(categories) {
  if (!categories.length) return;
  const previousCategory = activeLexiconCategory;
  lexiconData = categoriesToLexiconData(categories);
  activeLexiconCategory = lexiconData[previousCategory] ? previousCategory : categories[0].id;
  promptProfileDirty = false;
  renderLexiconNav();
  renderLexiconOptions();
  renderLexicon();
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();

  const runCrawler = runCrawlerEl.checked;
  if (!runCrawler && !sourceOutputEl.value) {
    statusEl.textContent = "请先选择一个已有输出";
    return;
  }

  const sourceOutputId = runCrawler ? null : selectedSourceOutputId();
  const isSearch = crawlModeEl.value === "search";
  const keywordSource = isSearch ? "lexicon" : "keyword";
  const selectedPromptCategory = taskLexiconEl.value;
  const selectedLexiconKeywords = isSearch
    ? lexiconKeywords(selectedPromptCategory)
    : [];
  if (isSearch && !selectedLexiconKeywords.length) {
    statusEl.textContent = "当前词库没有启用关键词";
    return;
  }
  const payload = {
    platform: platformEl.value,
    crawl_mode: crawlModeEl.value,
    keyword: isSearch ? selectedLexiconKeywords.join(",") : "",
    keyword_source: keywordSource,
    lexicon_category: selectedPromptCategory,
    lexicon_keywords: selectedLexiconKeywords,
    creator_url: creatorUrlEl.value,
    start_page: DEFAULT_START_PAGE,
    max_notes: DEFAULT_MAX_NOTES,
    max_comments: DEFAULT_MAX_COMMENTS,
    max_concurrency: DEFAULT_MAX_CONCURRENCY,
    get_sub_comment: document.getElementById("get-sub-comment").checked,
    analyze_limit: DEFAULT_ANALYZE_LIMIT,
    run_crawler: runCrawler,
    source_output_id: sourceOutputId,
  };

  statusEl.textContent = "提交中...";
  renderLogsText("", { forceBottom: true });

  try {
    const response = await apiFetch("/api/jobs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    });
    await handleCreatedJobResponse(response);
  } catch (error) {
    statusEl.textContent = "提交失败";
    renderLogsText(String(error), { forceBottom: true });
  }
});

localVideoForm.addEventListener("submit", async (event) => {
  event.preventDefault();

  const fileInput = document.getElementById("local-video-file");
  const file = fileInput.files && fileInput.files[0];
  if (!file) {
    statusEl.textContent = "请先选择一个视频文件";
    return;
  }

  const formData = new FormData();
  formData.append("video", file);
  formData.append("title", document.getElementById("local-video-title").value);
  formData.append("desc", document.getElementById("local-video-desc").value);

  statusEl.textContent = "上传中...";
  renderLogsText("", { forceBottom: true });

  try {
    const response = await apiFetch("/api/local-video-jobs", {
      method: "POST",
      body: formData
    });
    await handleCreatedJobResponse(response);
  } catch (error) {
    statusEl.textContent = "上传失败";
    renderLogsText(String(error), { forceBottom: true });
  }
});

async function handleCreatedJobResponse(response) {
  const data = await safeJson(response);
  if (!response.ok) {
    statusEl.textContent = "提交失败";
    renderLogsText(JSON.stringify(data, null, 2), { forceBottom: true });
    return;
  }

  await selectJob(data.id, { job: data, view: "tasks" });
  await loadJobs({ keepSelection: true });
  startPolling();
}

async function safeJson(response) {
  const text = await response.text();
  if (!text) return {};
  try {
    return JSON.parse(text);
  } catch (error) {
    return { detail: text };
  }
}

async function sendJobControl(action) {
  if (!currentJobId) {
    statusEl.textContent = "当前没有可控制的任务";
    return;
  }
  if (action === "delete_job") {
    await deleteCurrentJob();
    return;
  }
  const button = controlButtons.find(item => item.dataset.action === action);
  if (button) button.disabled = true;
  try {
    const response = await apiFetch(`/api/jobs/${currentJobId}/control`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action })
    });
    const data = await response.json();
    if (!response.ok) {
      statusEl.textContent = "控制失败";
      logsEl.textContent = JSON.stringify(data, null, 2);
      return;
    }
    await updateJobFromResponse(data);
    startPolling();
  } finally {
    if (button) button.disabled = false;
  }
}

async function deleteCurrentJob() {
  if (!currentJobId) return;
  const job = jobsById.get(currentJobId);
  const confirmed = window.confirm(`删除任务「${jobTitle(job || { id: currentJobId })}」？\n\n任务会从列表隐藏，已生成的审核结果暂不物理删除。`);
  if (!confirmed) return;

  const button = controlButtons.find(item => item.dataset.action === "delete_job");
  if (button) button.disabled = true;
  try {
    const response = await apiFetch(`/api/jobs/${currentJobId}`, { method: "DELETE" });
    const data = await response.json();
    if (!response.ok) {
      statusEl.textContent = "删除任务失败";
      renderLogsText(JSON.stringify(data, null, 2), { forceBottom: true });
      return;
    }
    jobsById.delete(currentJobId);
    currentJobId = null;
    resetAuditView();
    selectedUserKey = "";
    selectedResultId = "";
    statusEl.textContent = "任务已删除";
    renderLogsText("", { forceBottom: true });
    renderTaskState(null);
    renderList();
    renderJobList();
    updateControlButtons(null);
    await loadJobs({ keepSelection: true });
  } finally {
    if (button) button.disabled = false;
  }
}

function apiFetch(path, options) {
  return fetch(`${apiBase}${path}`, options);
}

function setWorkspaceView(view) {
  activeWorkspaceView = view;
  navItems.forEach(item => item.classList.toggle("active", item.dataset.view === view));
  workspaceViews.forEach(panel => panel.classList.toggle("active", panel.dataset.view === view));
  const copy = workspaceCopy[view] || workspaceCopy.tasks;
  workspaceTitleEl.textContent = copy.title;
  workspaceSubtitleEl.textContent = copy.subtitle;
}

function setLexiconCategory(category) {
  if (category === activeLexiconCategory) return;
  if (!confirmPromptProfileDiscard()) return;
  activeLexiconCategory = category;
  promptProfileDirty = false;
  renderLexicon();
  if (taskLexiconEl.value !== category) {
    taskLexiconEl.value = category;
    updateKeywordSourceState();
  }
}

function setLexiconPanel(panel) {
  const nextPanel = panel === "prompt" ? "prompt" : "keywords";
  if (nextPanel === activeLexiconPanel) return;
  activeLexiconPanel = nextPanel;
  renderLexiconPanel();
}

function confirmPromptProfileDiscard() {
  if (!promptProfileDirty) return true;
  return window.confirm("研判方案有未保存修改，切换后将丢失这些修改。");
}

function renderLexiconPanel() {
  const showPrompt = activeLexiconPanel === "prompt";
  lexiconTabButtons.forEach(button => {
    button.classList.toggle("active", button.dataset.lexiconPanel === activeLexiconPanel);
  });
  lexiconKeywordsPanelEl?.classList.toggle("active", !showPrompt);
  lexiconPromptPanelEl?.classList.toggle("active", showPrompt);
  addKeywordButtonEl?.classList.toggle("hidden", showPrompt);
}

function renderLexiconNav() {
  if (!categoryPaneEl) return;
  const categories = Object.entries(lexiconData);
  categoryPaneEl.innerHTML = `
    <h3>风险分类</h3>
    ${categories.map(([id, data]) => `
      <button class="category-item ${id === activeLexiconCategory ? "active" : ""}" type="button" data-category="${escapeAttr(id)}">
        ${escapeHtml(data.title.replace(/词库$/, ""))}
      </button>
    `).join("")}
  `;
  categoryPaneEl.querySelectorAll(".category-item").forEach(item => {
    item.addEventListener("click", () => setLexiconCategory(item.dataset.category));
  });
}

function renderLexiconOptions() {
  const entries = Object.entries(lexiconData);
  taskLexiconEl.innerHTML = entries.map(([id, data]) => `
    <option value="${escapeAttr(id)}">${escapeHtml(data.title.replace(/词库$/, ""))}</option>
  `).join("");
  if (!lexiconData[taskLexiconEl.value]) {
    taskLexiconEl.value = activeLexiconCategory;
  }
}

function renderLexicon() {
  const data = lexiconData[activeLexiconCategory] || lexiconData.soft;
  document.querySelectorAll(".category-item").forEach(item => {
    item.classList.toggle("active", item.dataset.category === activeLexiconCategory);
  });
  renderLexiconTableHead();
  lexiconTitleEl.textContent = data.title;
  keywordChipsEl.innerHTML = (data.chips || []).map(word => `<span>${escapeHtml(word)}</span>`).join("");
  keywordTableBodyEl.innerHTML = (data.keywords || []).map(keyword => {
    const keywordId = Number(keyword.id || 0);
    const disabled = keywordId ? "" : "disabled";
    const status = keyword.enabled ? "启用" : "停用";
    return `
      <tr>
        <td>${escapeHtml(keyword.keyword)}</td>
        <td>${escapeHtml(keyword.match_type)}</td>
        <td>
          <button class="table-action" type="button" ${disabled} onclick="toggleKeywordEnabled(${keywordId}, '${escapeJs(status)}')">${escapeHtml(status)}</button>
        </td>
        <td>${escapeHtml(String(keyword.hit_count_7d || 0))}</td>
        <td>
          <button class="table-action danger-action" type="button" ${disabled} onclick="deleteKeyword(${keywordId}, '${escapeJs(keyword.keyword)}')">删除</button>
        </td>
      </tr>
    `;
  }).join("");
  renderPromptProfile(data.prompt_profile);
  renderLexiconPanel();
  updateKeywordSourceState();
}

function enabledLexiconRows(category) {
  const data = lexiconData[category] || lexiconData.soft;
  return (data.keywords || []).filter(keyword => keyword.enabled);
}

function lexiconKeywords(category) {
  return enabledLexiconRows(category).map(keyword => keyword.keyword).filter(Boolean);
}

function renderPromptProfile(profile) {
  const normalized = normalizePromptProfile(profile, activeLexiconCategory);
  if (promptImagePromptEl) promptImagePromptEl.value = normalized.image_prompt;
  if (promptFramePromptEl) promptFramePromptEl.value = normalized.frame_prompt;
  if (promptFusionPromptEl) promptFusionPromptEl.value = normalized.fusion_prompt_template;
  setPromptProfileDirty(false);
}

function markPromptProfileDirty() {
  setPromptProfileDirty(true);
}

function setPromptProfileDirty(dirty) {
  promptProfileDirty = Boolean(dirty);
  if (promptProfileDirtyEl) {
    promptProfileDirtyEl.textContent = promptProfileDirty ? "未保存" : "";
  }
  if (savePromptProfileButtonEl) {
    savePromptProfileButtonEl.disabled = !promptProfileDirty;
  }
}

function promptProfileFormValues() {
  return {
    image_prompt: promptImagePromptEl?.value || "",
    frame_prompt: promptFramePromptEl?.value || "",
    fusion_prompt_template: promptFusionPromptEl?.value || "",
  };
}

async function savePromptProfile(event) {
  event.preventDefault();
  const payload = promptProfileFormValues();
  if (!payload.image_prompt.trim()) {
    promptImagePromptEl?.focus();
    return;
  }
  if (!payload.frame_prompt.trim()) {
    promptFramePromptEl?.focus();
    return;
  }
  if (!payload.fusion_prompt_template.trim()) {
    promptFusionPromptEl?.focus();
    return;
  }
  if (savePromptProfileButtonEl) savePromptProfileButtonEl.disabled = true;
  const response = await apiFetch(`/api/lexicons/${encodeURIComponent(activeLexiconCategory)}/prompt-profile`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  const data = await response.json();
  if (!response.ok) {
    statusEl.textContent = "保存研判方案失败";
    logsEl.textContent = JSON.stringify(data, null, 2);
    setPromptProfileDirty(true);
    return;
  }
  statusEl.textContent = "研判方案已保存";
  applyLexiconCategories(data.categories || []);
  setLexiconPanel("prompt");
}

async function resetPromptProfile() {
  const confirmed = window.confirm("恢复默认研判方案？\n\n恢复后只影响后续新建任务。");
  if (!confirmed) return;
  if (resetPromptProfileButtonEl) resetPromptProfileButtonEl.disabled = true;
  const response = await apiFetch(`/api/lexicons/${encodeURIComponent(activeLexiconCategory)}/prompt-profile/reset`, {
    method: "POST",
  });
  const data = await response.json();
  if (resetPromptProfileButtonEl) resetPromptProfileButtonEl.disabled = false;
  if (!response.ok) {
    statusEl.textContent = "恢复默认方案失败";
    logsEl.textContent = JSON.stringify(data, null, 2);
    return;
  }
  statusEl.textContent = "研判方案已恢复默认";
  applyLexiconCategories(data.categories || []);
  setLexiconPanel("prompt");
}

function updateSourceOutputState() {
  const enabled = !runCrawlerEl.checked;
  sourceOutputEl.disabled = !enabled;
  sourceOutputEl.closest("label").classList.toggle("disabled", !enabled);
}

function updateCrawlModeState() {
  const isCreator = crawlModeEl.value === "creator";
  creatorFieldEl.classList.toggle("hidden", !isCreator);
  creatorUrlEl.disabled = !isCreator;
  creatorUrlEl.placeholder = creatorPlaceholders[platformEl.value] || "博主主页 URL";
  updateKeywordSourceState();
}

function updateKeywordSourceState() {
  const isCreator = crawlModeEl.value === "creator";
  taskLexiconFieldEl.classList.remove("hidden");
  taskLexiconEl.disabled = false;
  if (taskLexiconLabelEl) {
    taskLexiconLabelEl.textContent = isCreator ? "审核规则" : "词库";
  }
}

async function loadOutputs() {
  try {
    const response = await apiFetch("/api/outputs");
    const data = await response.json();
    const outputs = data.outputs || [];
    sourceOutputEl.innerHTML = "";

    if (!outputs.length) {
      const option = document.createElement("option");
      option.value = "";
      option.textContent = "outputs 下暂无可用结果";
      sourceOutputEl.appendChild(option);
      return;
    }

    for (const output of outputs) {
      const option = document.createElement("option");
      option.value = `${output.platform || "xhs"}:${output.id}`;
      option.textContent = `${platformLabel(output.platform)} · ${output.id} · ${output.contents_count} 条 · 图 ${output.image_count} · 视频 ${output.video_count}`;
      sourceOutputEl.appendChild(option);
    }
  } catch (error) {
    sourceOutputEl.innerHTML = "";
    const option = document.createElement("option");
    option.value = "";
    option.textContent = "读取 outputs 失败";
    sourceOutputEl.appendChild(option);
    logsEl.textContent = String(error);
  }
}

function selectedSourceOutputId() {
  const [platform, id] = String(sourceOutputEl.value || "").split(":", 2);
  if (id) {
    platformEl.value = platform;
    return id;
  }
  return sourceOutputEl.value;
}

function platformLabel(platform) {
  return {
    xhs: "小红书",
    dy: "抖音",
    ks: "快手",
    local: "本地",
  }[platform || "xhs"] || platform;
}

async function loadJobs(options = {}) {
  try {
    const response = await apiFetch("/api/jobs");
    const jobs = await response.json();
    if (!response.ok) {
      throw new Error(JSON.stringify(jobs));
    }
    jobsById = new Map((jobs || []).map(job => [job.id, job]));
    if (!currentJobId && jobs.length && !options.keepSelection) {
      await selectJob(jobs[0].id, { job: jobs[0], view: activeWorkspaceView });
      return;
    }
    renderJobList();
    if (currentJobId && jobsById.has(currentJobId)) {
      await updateJobFromResponse(jobsById.get(currentJobId));
    } else if (currentJobId && !jobsById.has(currentJobId)) {
      currentJobId = null;
      statusEl.textContent = "请选择一个任务";
      renderLogsText("", { forceBottom: true });
      renderTaskState(null);
      resetAuditView();
      renderList();
      updateControlButtons(null);
    }
  } catch (error) {
    if (jobListEl) {
      jobListEl.innerHTML = `<p class="detail-empty">读取任务列表失败</p>`;
    }
    renderLogsText(`${logsEl.textContent ? logsEl.textContent + "\n\n" : ""}读取任务列表失败：${error}`);
  }
}

async function selectJob(jobId, options = {}) {
  const job = options.job || jobsById.get(jobId);
  currentJobId = jobId;
  logsFollowTail = true;
  resetAuditView();
  selectedUserKey = "";
  selectedResultId = "";
  renderList();
  renderDetail(null);
  renderJobList();
  if (job) {
    await updateJobFromResponse(job);
  } else {
    await poll();
  }
  startPolling();
  setWorkspaceView(options.view || "tasks");
}

function renderJobList() {
  if (!jobListEl) return;
  const jobs = [...jobsById.values()];
  if (!jobs.length) {
    jobListEl.innerHTML = `<p class="detail-empty">暂无任务</p>`;
    return;
  }
  jobListEl.innerHTML = jobs.map(job => {
    const active = job.id === currentJobId;
    const title = jobTitle(job);
    const subtitle = jobSubtitle(job);
    const stats = job.task_stats || {};
    const done = numberText(stats.completed_analysis_count);
    const pending = numberText(stats.pending_analysis_count);
    return `
      <button class="job-card ${active ? "active" : ""}" type="button" onclick="selectJobFromList('${escapeJs(job.id)}')">
        <span class="job-card-title">${escapeHtml(title)}</span>
        <span class="job-card-meta">${escapeHtml(subtitle)}</span>
        <span class="job-card-stats">
          <span>完成 ${escapeHtml(done)}</span>
          <span>待分析 ${escapeHtml(pending)}</span>
        </span>
      </button>
    `;
  }).join("");
}

function jobTitle(job) {
  if (job.display_title) {
    return job.display_title;
  }
  if (job.input_type === "local_video") {
    return "本地视频审核";
  }
  if (job.crawl_mode === "creator") {
    return `${platformLabel(job.platform)}博主主页巡检`;
  }
  if (job.keyword_source === "lexicon") {
    return `${lexiconDisplayName(job.lexicon_category || job.keyword)}风险巡检`;
  }
  if (job.keyword) {
    return `${job.keyword}关键词巡检`;
  }
  if (job.source_output_id) {
    return "已有输出补充分析";
  }
  return `任务 ${shortIdentifier(job.id || "")}`;
}

function jobSubtitle(job) {
  if (job.display_subtitle) {
    return job.display_subtitle;
  }
  if (job.input_type === "local_video") {
    return [job.input_filename || "未命名视频", job.lexicon_category || job.keyword || "", formatJobTime(job.created_at)]
      .filter(Boolean)
      .join(" · ");
  }
  if (job.crawl_mode === "creator") {
    const stats = job.task_stats || {};
    const nickname = jobCreatorNickname(job);
    if (nickname) {
      return [
        platformLabel(job.platform),
        `博主 ${nickname}`,
        `已完成 ${numberText(stats.completed_analysis_count)} / 待分析 ${numberText(stats.pending_analysis_count)}`,
      ].join(" · ");
    }
    return [
      platformLabel(job.platform),
      shortCreatorRef(job.creator_url || job.creator_id || ""),
      formatJobTime(job.created_at),
    ].filter(Boolean).join(" · ");
  }
  if (job.keyword_source === "lexicon") {
    return [
      platformLabel(job.platform),
      "词库",
      job.lexicon_category || job.keyword || "",
      formatJobTime(job.created_at),
    ].filter(Boolean).join(" · ");
  }
  return [
    platformLabel(job.platform),
    job.keyword ? "关键词" : "已有输出",
    job.keyword || job.source_output_id || "",
    formatJobTime(job.created_at),
  ].filter(Boolean).join(" · ");
}

function lexiconDisplayName(category) {
  const key = String(category || "").trim();
  return {
    soft: "软色情",
    gambling: "赌博",
    fraud: "涉诈",
    minority: "民族语言",
  }[key] || (lexiconCategories[key]?.title || key || "词库");
}

function jobCreatorNickname(job) {
  const direct = job.creator_nickname || job.nickname || "";
  return String(direct || "").trim();
}

function shortCreatorRef(value) {
  const raw = String(value || "").trim();
  if (!raw) {
    return "";
  }
  try {
    const url = new URL(raw);
    const parts = url.pathname.split("/").filter(Boolean);
    const userIndex = parts.findIndex(part => part === "user");
    if (userIndex >= 0 && parts[userIndex + 1]) {
      return `user/${truncateMiddle(parts[userIndex + 1], 22)}`;
    }
  } catch (_) {
    // Treat non-URL creator IDs below.
  }
  return truncateMiddle(raw.replace(/^user\//, ""), 28);
}

function shortIdentifier(value) {
  return truncateMiddle(String(value || ""), 18);
}

function truncateMiddle(value, maxChars) {
  const text = String(value || "");
  if (!maxChars || text.length <= maxChars) {
    return text;
  }
  const keep = Math.max(2, Math.floor((maxChars - 3) / 2));
  const tail = Math.max(2, maxChars - 3 - keep);
  return `${text.slice(0, keep)}...${text.slice(-tail)}`;
}

function formatJobTime(value) {
  const text = String(value || "");
  return text ? text.replace("T", " ").slice(0, 16) : "";
}

function isLogsAtBottom() {
  if (!logsEl) return true;
  const distance = logsEl.scrollHeight - logsEl.clientHeight - logsEl.scrollTop;
  return distance <= LOG_BOTTOM_THRESHOLD;
}

function renderLogsText(text, options = {}) {
  if (!logsEl) return;
  const shouldFollow = Boolean(options.forceBottom) || logsFollowTail || isLogsAtBottom();
  const previousTop = logsEl.scrollTop;
  logsEl.textContent = text;
  if (shouldFollow) {
    logsEl.scrollTop = logsEl.scrollHeight;
    logsFollowTail = true;
  } else {
    logsEl.scrollTop = previousTop;
  }
}

window.selectJobFromList = (jobId) => {
  selectJob(jobId, { view: "tasks" });
};

function startPolling() {
  if (pollTimer) clearInterval(pollTimer);
  poll();
  pollTimer = setInterval(poll, 2500);
}

async function poll() {
  await loadJobs({ keepSelection: true });
}

async function updateJobFromResponse(job) {
  statusEl.textContent = statusLabel(job.status || "idle");
  renderTaskState(job);
  renderLogsText((job.logs || []).map(log => `[${log.time}] ${log.message}`).join("\n"));
  await loadAuditResults(job);
  updateControlButtons(job);
  renderList();
  if (job.status === "failed" && job.error) {
    renderLogsText(`${logsEl.textContent}\n\n错误详情：\n${job.error}`);
  }
}

async function loadAuditResults(job) {
  try {
    const afterId = auditView.lastResultId > 0 ? `&after_id=${auditView.lastResultId}` : "";
    const response = await apiFetch(`/api/jobs/${job.id}/audit-results?limit=1000&sort=id${afterId}`);
    const data = await response.json();
    if (!response.ok) {
      throw new Error(JSON.stringify(data));
    }
    const items = normalizeAuditItems(data.items || []);
    if (items.length) {
      applyFetchedAuditItems(items);
      return;
    }
    if (!auditView.liveItems.length && (job.items || []).length) {
      applyFetchedAuditItems(normalizeAuditItems(job.items || []));
    } else {
      syncCurrentItemsFromView();
    }
  } catch (error) {
    if (!auditView.liveItems.length) {
      applyFetchedAuditItems(normalizeAuditItems(job.items || []));
    } else {
      syncCurrentItemsFromView();
    }
    renderLogsText(`${logsEl.textContent}\n\n读取审核结果表失败，已回退到任务缓存：${error}`);
  }
  renderAuditSnapshotBar();
}

function normalizeAuditItems(items) {
  return items.map(item => ({
    ...item,
    stable_id: resultStableId(item),
  }));
}

function resultStableId(item) {
  return String(
    item.audit_result_id
    || item.id
    || item.content_key
    || item.note_id
    || item.url
    || item.local_path
    || ""
  );
}

function findItemByStableId(id) {
  return currentItems.find(item => resultStableId(item) === String(id)) || null;
}

function resetAuditView() {
  currentItems = [];
  profileExpandedUserKey = "";
  auditView = {
    mode: "live",
    liveItems: [],
    viewItems: [],
    lastResultId: 0,
    bufferedCount: 0,
    bloggerIdKeyword: "",
    bloggerNicknameKeyword: "",
    userSort: "default",
    itemSort: "default",
  };
  renderAuditSnapshotBar();
  renderAuditQueryControls();
}

function applyFetchedAuditItems(items) {
  const previousViewIds = new Set(auditView.viewItems.map(resultStableId));
  const previousLiveCount = auditView.liveItems.length;
  const liveMap = new Map(auditView.liveItems.map(item => [resultStableId(item), item]));
  items.forEach(item => {
    const id = resultStableId(item);
    if (id) {
      liveMap.set(id, item);
    }
  });
  auditView.liveItems = [...liveMap.values()];
  auditView.lastResultId = maxAuditResultId(auditView.liveItems);

  if (auditView.mode === "live") {
    auditView.viewItems = [...auditView.liveItems];
    auditView.bufferedCount = 0;
  } else {
    const newBuffered = auditView.liveItems.filter(item => !previousViewIds.has(resultStableId(item))).length;
    auditView.bufferedCount = Math.max(auditView.bufferedCount, newBuffered);
    if (!auditView.viewItems.length && previousLiveCount === 0) {
      auditView.viewItems = [...auditView.liveItems];
      auditView.bufferedCount = 0;
    }
  }

  syncCurrentItemsFromView();
  renderAuditSnapshotBar();
}

function syncCurrentItemsFromView() {
  currentItems = auditView.mode === "live" ? auditView.liveItems : auditView.viewItems;
}

function maxAuditResultId(items) {
  return items.reduce((max, item) => {
    const id = Number(item.audit_result_id || item.id || 0);
    return Number.isFinite(id) ? Math.max(max, id) : max;
  }, 0);
}

function enterSnapshotMode() {
  if (auditView.mode === "snapshot") return;
  auditView.mode = "snapshot";
  auditView.viewItems = [...currentItems];
  auditView.bufferedCount = 0;
  syncCurrentItemsFromView();
  renderAuditSnapshotBar();
}

function refreshSnapshot() {
  auditView.mode = "snapshot";
  auditView.viewItems = [...auditView.liveItems];
  auditView.bufferedCount = 0;
  syncCurrentItemsFromView();
  renderAuditSnapshotBar();
  renderAuditQueryControls();
  renderList();
}

function returnToLiveMode() {
  auditView.mode = "live";
  auditView.viewItems = [...auditView.liveItems];
  auditView.bufferedCount = 0;
  auditView.bloggerIdKeyword = "";
  auditView.bloggerNicknameKeyword = "";
  auditView.userSort = "default";
  auditView.itemSort = "default";
  profileExpandedUserKey = "";
  syncCurrentItemsFromView();
  renderAuditSnapshotBar();
  renderAuditQueryControls();
  renderList();
}

function renderAuditSnapshotBar() {
  if (!auditSnapshotBarEl || !auditSnapshotTextEl) return;
  const isSnapshot = auditView.mode === "snapshot";
  auditSnapshotBarEl.classList.toggle("snapshot", isSnapshot);
  auditSnapshotBarEl.classList.toggle("has-buffer", auditView.bufferedCount > 0);
  auditSnapshotTextEl.textContent = isSnapshot
    ? (auditView.bufferedCount > 0
      ? `当前列表已暂停自动更新，有 ${auditView.bufferedCount} 条新结果待加载`
      : "当前列表已暂停自动更新")
    : "实时更新中";
  if (auditApplyUpdatesButtonEl) {
    auditApplyUpdatesButtonEl.disabled = auditView.bufferedCount <= 0;
  }
  if (auditLiveButtonEl) {
    auditLiveButtonEl.disabled = !isSnapshot;
  }
}

function renderAuditQueryControls() {
  if (auditBloggerIdKeywordEl && auditBloggerIdKeywordEl.value !== auditView.bloggerIdKeyword) {
    auditBloggerIdKeywordEl.value = auditView.bloggerIdKeyword;
  }
  if (auditBloggerNicknameKeywordEl && auditBloggerNicknameKeywordEl.value !== auditView.bloggerNicknameKeyword) {
    auditBloggerNicknameKeywordEl.value = auditView.bloggerNicknameKeyword;
  }
  if (auditClearSearchButtonEl) {
    auditClearSearchButtonEl.disabled = !hasBloggerSearch();
  }
  if (auditUserRiskSortButtonEl) {
    const enabled = auditView.userSort === "risk";
    auditUserRiskSortButtonEl.textContent = `博主风险排序：${enabled ? "开" : "关"}`;
    auditUserRiskSortButtonEl.classList.toggle("active", enabled);
  }
  if (auditItemRiskSortButtonEl) {
    const enabled = auditView.itemSort === "risk";
    auditItemRiskSortButtonEl.textContent = `帖子风险排序：${enabled ? "开" : "关"}`;
    auditItemRiskSortButtonEl.classList.toggle("active", enabled);
  }
}

function renderTaskState(job) {
  if (!taskStateGridEl) return;
  const stats = job?.task_stats || {};
  const cells = [
    ["采集状态", statusLabel(job?.crawl_status || "idle")],
    ["分析状态", statusLabel(job?.analysis_status || "idle")],
    ["待分析", numberText(stats.pending_analysis_count)],
    ["处理中", numberText(stats.analyzing_count)],
    ["已完成", numberText(stats.completed_analysis_count)],
    ["失败", numberText(stats.failed_analysis_count)],
  ];
  taskStateGridEl.innerHTML = cells.map(([label, value]) => `
    <div>
      <span>${escapeHtml(label)}</span>
      <strong>${escapeHtml(value)}</strong>
    </div>
  `).join("");
}

function statusLabel(status) {
  return {
    idle: "未开始",
    queued: "排队中",
    running: "运行中",
    completed: "已完成",
    stopped: "已停止",
    stopping: "停止中",
    analysis_running: "分析中",
    analysis_stopping: "停止分析中",
    analysis_stopped: "分析已停止",
    crawl_pausing: "停止采集中",
    crawl_paused: "采集已停止",
    paused: "已暂停",
    analysis_paused: "分析已暂停",
    pending: "待补分析",
    interrupted: "已中断",
    skipped: "跳过",
    unknown: "未知",
  }[status || "idle"] || status;
}

function numberText(value) {
  const number = Number(value || 0);
  return Number.isFinite(number) ? String(number) : "0";
}

function updateControlButtons(job) {
  const hasJob = Boolean(job && job.id);
  const actions = job?.available_actions || fallbackAvailableActions(job);
  controlButtons.forEach(button => {
    const action = button.dataset.action;
    button.disabled = !hasJob || !actions[action];
  });
  if (openAuditButtonEl) {
    openAuditButtonEl.disabled = !hasJob;
  }
}

function fallbackAvailableActions(job) {
  if (!job?.id) {
    return {};
  }
  const status = job.status || "";
  const control = job.control || {};
  const stats = job.task_stats || {};
  const hasPending = Number(stats.pending_analysis_count || 0) > 0;
  const hasFailed = Number(stats.failed_analysis_count || 0) > 0;
  const hasAnalyzing = Number(stats.analyzing_count || 0) > 0;
  const hasRetriable = hasPending || hasFailed;
  const stoppingAll = status === "stopping" || Boolean(control.stop_all_requested);
  const analysisStopping = status === "analysis_stopping" || Boolean(control.analysis_stop_requested);
  const crawlActive = Boolean(job.run_crawler) && (
    status === "queued"
    || status === "running"
    || (status === "crawl_pausing" && !control.crawl_stop_requested)
  );
  const analysisActive = ["running", "analysis_running", "crawl_pausing"].includes(status)
    && !control.analysis_stop_requested
    && !control.stop_all_requested;
  let canBackfill = hasRetriable
    && !stoppingAll
    && !["queued", "running", "crawl_pausing", "analysis_running"].includes(status);
  if (analysisStopping) {
    canBackfill = (hasRetriable || hasAnalyzing || status === "analysis_stopping") && !stoppingAll;
  }
  return {
    pause_crawl: crawlActive && !stoppingAll,
    stop_analysis: analysisActive,
    backfill_analysis: canBackfill,
    delete_job: true,
  };
}

function renderList() {
  const groups = buildUserGroups(currentItems);
  if (!currentItems.length || !groups.length) {
    setProfileExpandedLayout(false);
    if (userListEl) userListEl.innerHTML = `<p class="detail-empty">暂无用户</p>`;
    listEl.innerHTML = `<p class="detail-empty">${currentItems.length ? "没有匹配的博主" : "暂无结果"}</p>`;
    renderDetail(null);
    return;
  }

  if (!selectedUserKey || !groups.some(group => group.key === selectedUserKey)) {
    selectedUserKey = groups[0].key;
  }
  if (profileExpandedUserKey && !groups.some(group => group.key === profileExpandedUserKey)) {
    profileExpandedUserKey = "";
  }
  if (profileExpandedUserKey) {
    selectedUserKey = profileExpandedUserKey;
  }
  renderUserPool(groups);

  const selectedGroup = groups.find(group => group.key === selectedUserKey) || groups[0];
  if (profileExpandedUserKey) {
    selectedUserKey = profileExpandedUserKey;
    const expandedGroup = groups.find(group => group.key === profileExpandedUserKey) || selectedGroup;
    if (!expandedGroup.items.some(entry => entry.id === selectedResultId)) {
      selectedResultId = expandedGroup.items[0]?.id || "";
    }
    setProfileExpandedLayout(true);
    renderContentList(groups);
    renderExpandedUserProfile(expandedGroup);
    return;
  }

  setProfileExpandedLayout(false);
  renderContentList(groups);
  if (!selectedGroup.items.some(entry => entry.id === selectedResultId)) {
    selectedResultId = selectedGroup.items[0]?.id || "";
  }
  renderDetail(selectedResultId ? findItemByStableId(selectedResultId) : null);
}

function setProfileExpandedLayout(expanded) {
  auditContentEl?.classList.toggle("profile-expanded", Boolean(expanded));
}

function buildUserGroups(items) {
  const map = new Map();
  items.forEach((item, index) => {
    const author = item.author || {};
    const key = item.author_key || author.sec_uid || author.user_id || author.user_unique_id || author.nickname || "unknown";
    if (!map.has(key)) {
      map.set(key, {
        key,
        author,
        items: [],
        reject: 0,
        review: 0,
        pass: 0,
        riskScore: 0,
        latestResultId: 0,
        firstSeenOrder: index,
        evidenceCount: 0,
      });
    }
    const group = map.get(key);
    const decision = item.decision || "review";
    group[decision] = (group[decision] || 0) + 1;
    group.evidenceCount += evidenceCount(item);
    group.latestResultId = Math.max(group.latestResultId, auditResultId(item));
    group.items.push({ item, id: resultStableId(item) });
  });
  const groups = [...map.values()].map(group => ({
    ...group,
    riskScore: group.reject * 2 + group.review,
  }));
  const visibleGroups = auditView.mode === "snapshot"
    ? groups.filter(group => matchesBloggerSearch(group))
    : groups;
  return sortUserGroups(visibleGroups, auditView.mode === "snapshot" ? auditView.userSort : "default");
}

function sortUserGroups(groups, userSort) {
  if (userSort !== "risk") {
    return [...groups].sort((a, b) => a.firstSeenOrder - b.firstSeenOrder);
  }
  return [...groups].sort((a, b) => (
    (b.riskScore - a.riskScore)
    || (b.reject - a.reject)
    || (b.review - a.review)
    || (b.items.length - a.items.length)
    || (b.latestResultId - a.latestResultId)
  ));
}

function hasBloggerSearch() {
  return Boolean(
    String(auditView.bloggerIdKeyword || "").trim()
    || String(auditView.bloggerNicknameKeyword || "").trim()
  );
}

function matchesBloggerSearch(group) {
  return matchesBloggerIdKeyword(group, auditView.bloggerIdKeyword)
    && matchesBloggerNicknameKeyword(group, auditView.bloggerNicknameKeyword);
}

function matchesBloggerIdKeyword(group, keyword) {
  const query = normalizedSearchText(keyword);
  if (!query) return true;
  const author = group.author || {};
  const exactFields = [
    group.key,
    author.sec_uid,
    author.user_id,
    author.user_unique_id,
    author.short_user_id,
  ];
  return exactFields.some(value => normalizedSearchText(value) === query);
}

function matchesBloggerNicknameKeyword(group, keyword) {
  const query = normalizedSearchText(keyword);
  if (!query) return true;
  const author = group.author || {};
  return normalizedSearchText(author.nickname).includes(query);
}

function normalizedSearchText(value) {
  return String(value || "").trim().toLowerCase();
}

function evidenceCount(item) {
  const storedCount = Number(item.evidence_count || 0);
  if (Number.isFinite(storedCount) && storedCount > 0) return storedCount;
  return (item.risk_evidence || []).length
    + (item.risk_frames || []).length
    + (item.risk_images || []).length;
}

function auditResultId(item) {
  const id = Number(item.audit_result_id || item.id || 0);
  return Number.isFinite(id) ? id : 0;
}

function renderUserPool(groups) {
  if (!userListEl) return;
  userListEl.innerHTML = groups.map(group => {
    const author = group.author || {};
    const name = author.nickname || author.user_unique_id || author.short_user_id || author.user_id || "未知账号";
    const selected = group.key === selectedUserKey;
    const expanded = group.key === profileExpandedUserKey;
    const meta = [
      author.user_unique_id && `抖音号 ${author.user_unique_id}`,
      author.short_user_id && `短号 ${author.short_user_id}`,
      author.ip_location,
    ].filter(Boolean).join(" · ");
    return `
      <div class="user-card ${selected ? "active" : ""} ${expanded ? "profile-open" : ""}">
        <button class="user-card-main" type="button" onclick="selectUser('${escapeJs(group.key)}')">
          <span class="user-name">${escapeHtml(name)}</span>
          <span class="user-meta">${escapeHtml(meta || author.user_id || "")}</span>
          <span class="user-stats">
            <span>风险值 ${group.riskScore}</span>
            <span>${group.items.length} 内容</span>
            <span>${group.reject} 拒绝</span>
            <span>${group.review} 复核</span>
          </span>
        </button>
        <button class="profile-toggle-button" type="button" onclick="toggleUserProfile('${escapeJs(group.key)}')">
          ${expanded ? "收起画像" : "展开画像"}
        </button>
      </div>
    `;
  }).join("");
}

function renderContentList(groups) {
  const selectedGroup = groups.find(group => group.key === selectedUserKey) || groups[0];
  if (!selectedGroup || !selectedGroup.items.length) {
    listEl.innerHTML = `<p class="detail-empty">暂无内容</p>`;
    return;
  }

  const entries = auditView.mode === "snapshot"
    ? sortItemEntries(selectedGroup.items, auditView.itemSort)
    : sortItemEntries(selectedGroup.items, "default");
  listEl.innerHTML = entries.map(({ item, id }) => {
    const decision = item.decision || "review";
    const evCount = evidenceCount(item);
    const text = contentDisplayTitle(item);
    const meta = contentDisplayMeta(item);
    return `
      <button class="item-card ${decision} ${id === selectedResultId ? "active" : ""}" type="button" data-item-id="${escapeAttr(id)}" onclick="selectItem('${escapeJs(id)}')">
        <div class="item-title">${escapeHtml(text)}</div>
        <div class="item-meta">${escapeHtml(meta)}</div>
        <span class="badge ${decision}">${escapeHtml(decisionLabel(decision))} · ${escapeHtml(riskLevelLabel(item.risk_level))}</span>
        ${evCount ? `<span class="ev-count">证据 ${evCount}</span>` : ""}
      </button>
    `;
  }).join("");
}

function sortItemEntries(entries, itemSort) {
  if (itemSort !== "risk") {
    return [...entries];
  }
  return [...entries].sort((a, b) => (
    decisionRank(b.item) - decisionRank(a.item)
    || riskLevelRank(b.item) - riskLevelRank(a.item)
    || evidenceCount(b.item) - evidenceCount(a.item)
    || auditResultId(b.item) - auditResultId(a.item)
  ));
}

function contentDisplayTitle(item, maxChars = 42) {
  const value = [
    item.content_title,
    item.raw_audit?.content_title,
    item.title,
    item.desc,
    item.note_id,
    item.content_key,
    item.id,
  ].map(next => String(next || "").trim()).find(Boolean) || "未命名内容";
  return truncateText(value.replace(/\s+/g, " "), maxChars);
}

function contentDisplayMeta(item) {
  const id = item.note_id || item.content_key || item.id || "";
  const source = item.url || item.local_path || "";
  return [id, source].filter(Boolean).join(" · ");
}

function truncateText(value, maxChars) {
  const text = String(value || "");
  if (!maxChars || text.length <= maxChars) {
    return text;
  }
  return `${text.slice(0, Math.max(1, maxChars - 1))}…`;
}

function decisionRank(item) {
  return {
    reject: 3,
    review: 2,
    pass: 1,
  }[item.decision || "unknown"] || 0;
}

function riskLevelRank(item) {
  return {
    high: 3,
    medium: 2,
    low: 1,
  }[item.risk_level || "unknown"] || 0;
}

window.selectUser = (key) => {
  enterSnapshotMode();
  selectedUserKey = key;
  selectedResultId = "";
  profileExpandedUserKey = "";
  renderList();
};

window.selectItem = (id) => {
  enterSnapshotMode();
  profileExpandedUserKey = "";
  setProfileExpandedLayout(false);
  selectedResultId = id;
  updateSelectedItemHighlight(id);
  renderDetail(findItemByStableId(id));
};

function updateSelectedItemHighlight(id) {
  listEl.querySelectorAll(".item-card.active").forEach(card => {
    card.classList.remove("active");
  });
  const activeCard = [...listEl.querySelectorAll(".item-card")].find(card => card.dataset.itemId === String(id));
  activeCard?.classList.add("active");
}

window.setDetailTab = (tab) => {
  if (!["audit", "raw"].includes(tab)) return;
  activeDetailTab = tab;
  renderDetail(findItemByStableId(selectedResultId));
};

window.openProfileContent = (id) => {
  enterSnapshotMode();
  profileExpandedUserKey = "";
  selectedResultId = id;
  renderList();
};

window.toggleUserProfile = (key) => {
  enterSnapshotMode();
  if (profileExpandedUserKey === key) {
    profileExpandedUserKey = "";
  } else {
    selectedUserKey = key;
    profileExpandedUserKey = key;
  }
  renderList();
};

window.collapseUserProfile = () => {
  profileExpandedUserKey = "";
  renderList();
};

function renderDetail(item) {
  if (!item) {
    detailEl.className = "detail-empty";
    detailEl.textContent = "选择账号和内容查看详情";
    return;
  }
  detailEl.className = "";
  const author = item.author || {};
  const title = contentDisplayTitle(item, 80);
  const activeTab = activeDetailTab === "raw" ? "raw" : "audit";
  detailEl.innerHTML = `
    <div class="detail-head">
      <div>
        <h3>${escapeHtml(title)}</h3>
        <p>${escapeHtml(author.nickname || author.user_unique_id || author.user_id || "未知账号")} · ${escapeHtml(item.note_id || "")}</p>
      </div>
      ${item.url ? `<a class="open-link" href="${escapeAttr(item.url)}" target="_blank">打开原文</a>` : ""}
    </div>
    <div class="detail-tabs" role="tablist" aria-label="帖子详情切换">
      <button class="detail-tab ${activeTab === "audit" ? "active" : ""}" type="button" role="tab" aria-selected="${activeTab === "audit"}" onclick="setDetailTab('audit')">内容审核</button>
      <button class="detail-tab ${activeTab === "raw" ? "active" : ""}" type="button" role="tab" aria-selected="${activeTab === "raw"}" onclick="setDetailTab('raw')">原文内容</button>
    </div>
    <div class="detail-tab-panel">
      ${activeTab === "raw" ? renderRawContent(item) : renderAuditDetail(item)}
    </div>
  `;
}

function renderExpandedUserProfile(group) {
  const firstItem = group?.items?.[0]?.item || {};
  const author = group?.author || firstItem.author || {};
  const name = author.nickname || author.user_unique_id || author.user_id || "未知账号";
  detailEl.className = "profile-expanded-detail";
  detailEl.innerHTML = `
    <div class="detail-head profile-expanded-head">
      <div>
        <h3>用户画像：@${escapeHtml(name)}</h3>
        <p>${escapeHtml(group?.items?.length || 0)} 条内容 · 风险值 ${escapeHtml(group?.riskScore || 0)}</p>
      </div>
      <button class="small-button secondary-button" type="button" onclick="collapseUserProfile()">收起画像</button>
    </div>
    ${renderUserProfile(firstItem, { showTitle: false })}
  `;
}

function assetUrl(rel) {
  return `${apiBase}/api/jobs/${currentJobId}/assets?path=${encodeURIComponent(rel)}`;
}

function renderAuditDetail(item) {
  const checkSections = [
    renderOriginalTextEvidence(item),
    renderCommentEvidence(item),
    renderImageEvidence(item),
    renderMomentOverviewEvidence(item),
    renderFrameEvidence(item),
    renderAudioEvidence(item),
  ].filter(Boolean).join("");
  return `
    ${renderConclusion(item)}
    <section class="detail-section check-results-section">
      <h4>分项检查结果</h4>
      <div class="check-results-list">
        ${checkSections}
      </div>
    </section>
  `;
}

function renderConclusion(item) {
  return `
    <section class="detail-section">
      <h4>结论</h4>
      <div class="conclusion-line">
        <span class="badge ${escapeAttr(normalizeDecision(item.decision))}">${escapeHtml(decisionLabel(item.decision))}</span>
        <span>风险等级：${escapeHtml(riskLevelLabel(item.risk_level))}</span>
        <span>类别：${escapeHtml((item.categories || []).join("、") || "未分类")}</span>
      </div>
      <p>${escapeHtml(item.summary || "暂无结论摘要")}</p>
    </section>
  `;
}

function renderRawContent(item) {
  return `
    ${renderRawTextContent(item)}
    ${renderRawMediaContent(item)}
    ${renderRawCommentsContent(item)}
  `;
}

function renderRawTextContent(item) {
  const author = item.author || {};
  const title = String(item.title || "").trim();
  const desc = String(item.desc || "").trim();
  const body = desc && desc !== title ? desc : "";
  const platform = item.platform || author.platform || "";
  const accountId = author.user_unique_id || author.short_user_id || author.user_id || author.sec_uid || "";
  const textBlocks = [
    title ? renderRawTextBlock(body ? "标题" : "标题 / 正文", title) : "",
    body ? renderRawTextBlock("正文", body) : "",
  ].filter(Boolean).join("");
  return `
    <section class="detail-section raw-content-section">
      <h4>原文信息</h4>
      <div class="raw-meta-grid">
        ${renderRawMeta("作者", author.nickname || "未知账号")}
        ${renderRawMeta("账号 ID", accountId)}
        ${renderRawMeta("平台", platform ? platformLabel(platform) : "")}
        ${renderRawMeta("内容 ID", item.note_id || item.content_key || item.id || "")}
      </div>
      ${textBlocks || renderEmptyEvidenceNote("暂无标题或正文")}
      ${item.url ? `<a class="raw-link" href="${escapeAttr(item.url)}" target="_blank">打开原文链接</a>` : ""}
    </section>
  `;
}

function renderRawMeta(label, value) {
  return `
    <div>
      <span>${escapeHtml(label)}</span>
      <strong>${escapeHtml(value || "暂无")}</strong>
    </div>
  `;
}

function renderRawTextBlock(label, text) {
  return `
    <div class="raw-text-block">
      <span>${escapeHtml(label)}</span>
      <p dir="auto">${escapeHtml(text)}</p>
    </div>
  `;
}

function renderRawMediaContent(item) {
  const mediaItems = [
    ...rawVideoCoverItems(item),
    ...rawImageItems(item),
  ].filter(entry => entry.src);
  return `
    <section class="detail-section raw-content-section">
      <h4>图片 / 视频封面</h4>
      ${mediaItems.length ? `
        <div class="raw-media-grid">
          ${mediaItems.map(renderRawMediaCard).join("")}
        </div>
      ` : renderEmptyEvidenceNote("暂无可展示的图片或视频封面")}
    </section>
  `;
}

function rawVideoCoverItems(item) {
  const videos = item.video_results || [];
  const directCover = rawCoverUrl(item);
  if (!videos.length && !directCover) return [];
  const video = videos[0] || {};
  const frame = (video.frames || [])[0] || {};
  const src = rawAssetUrl(directCover)
    || rawAssetUrl(video.cover_asset_rel || video.cover_rel || video.thumbnail_asset_rel || video.thumbnail_rel)
    || rawAssetUrl(frame.asset_rel)
    || rawAssetUrl(frame.path)
    || rawAssetUrl(video.cover_url || video.thumbnail_url)
    || "";
  return src ? [{
    src,
    label: "视频封面",
    caption: formatSeconds(frame.timestamp) ? `代表画面 · ${formatSeconds(frame.timestamp)}` : "视频封面",
  }] : [];
}

function rawCoverUrl(item) {
  return item.video_cover_url
    || item.cover_url
    || item.cover
    || item.thumbnail_url
    || item.thumbnail
    || "";
}

function rawImageItems(item) {
  const images = item.image_analyses || item.image_results || [];
  return images.slice(0, 6).map((image, index) => ({
    src: rawAssetUrl(image.asset_rel)
      || rawAssetUrl(image.local_path)
      || rawAssetUrl(image.path_or_url)
      || rawAssetUrl(image.url)
      || "",
    label: `图片 ${index + 1}`,
    caption: image.visual_summary || image.ocr_text || "原文图片",
  }));
}

function formatSeconds(value) {
  const number = Number(value);
  return Number.isFinite(number) ? `${number.toFixed(1)}s` : "";
}

function rawAssetUrl(pathOrRel) {
  if (!pathOrRel) return "";
  const value = String(pathOrRel);
  if (/^https?:\/\//i.test(value)) return value;
  const rel = jobAssetRel(value);
  return rel ? assetUrl(rel) : "";
}

function jobAssetRel(pathOrRel) {
  if (!pathOrRel || !currentJobId) return "";
  const normalized = String(pathOrRel).replaceAll("\\", "/");
  if (/^(assets|crawler)\//.test(normalized)) return normalized;
  const marker = `/outputs/${currentJobId}/`;
  const index = normalized.indexOf(marker);
  return index >= 0 ? normalized.slice(index + marker.length) : "";
}

function renderRawMediaCard(item) {
  return `
    <figure class="raw-media-card">
      <img src="${escapeAttr(item.src)}" alt="${escapeAttr(item.label)}" loading="lazy" />
      <figcaption>
        <strong>${escapeHtml(item.label)}</strong>
        <span>${escapeHtml(item.caption || "")}</span>
      </figcaption>
    </figure>
  `;
}

function renderRawCommentsContent(item) {
  const comments = item.comments || [];
  const shown = comments.slice(0, RAW_COMMENT_LIMIT);
  return `
    <section class="detail-section raw-content-section">
      <h4>评论区</h4>
      ${shown.length ? `
        <div class="raw-comment-list">
          ${shown.map(renderRawCommentRow).join("")}
        </div>
        ${comments.length > shown.length ? `<p class="raw-comment-more">仅展示前 ${shown.length} 条，共 ${comments.length} 条评论</p>` : ""}
      ` : renderEmptyEvidenceNote("暂无评论")}
    </section>
  `;
}

function renderRawCommentRow(comment) {
  const userId = comment.user_unique_id || comment.short_user_id || comment.user_id || comment.sec_uid || "";
  const meta = [
    comment.nickname || "评论者",
    userId ? `ID：${userId}` : "",
    comment.ip_location || "",
  ].filter(Boolean).join(" · ");
  return `
    <div class="raw-comment-row">
      <div class="comment-meta">${escapeHtml(meta)}</div>
      <p dir="auto">${escapeHtml(comment.content || comment.text || "（无文本评论）")}</p>
    </div>
  `;
}

function renderTranscriptDebug(item) {
  const videos = (item.video_results || []).filter(video => {
    const transcript = video.transcript || {};
    return (transcript.text || transcript.text_zh || transcript.error || (transcript.translation || {}).error);
  });
  if (!videos.length) return "";

  return `
    <section class="detail-section transcript-debug-section">
      <h4>ASR / 翻译验证</h4>
      ${videos.map(renderTranscriptDebugCard).join("")}
    </section>
  `;
}

function renderTranscriptDebugCard(video) {
  const transcript = video.transcript || {};
  const translation = transcript.translation || {};
  const meta = [
    transcript.asr_engine || transcript.provider || "",
    transcript.model ? `model=${transcript.model}` : "",
    transcript.language ? `lang=${transcript.language}` : "",
    transcript.region ? `region=${transcript.region}` : "",
    transcript.device ? `device=${transcript.device}` : "",
  ].filter(Boolean);
  const translationState = transcript.text_zh
    ? `已翻译${translation.provider ? ` · ${translation.provider}` : ""}`
    : translation.error
      ? `翻译失败：${translation.error}`
      : translation.reason
        ? `未翻译：${translation.reason}`
        : "未触发翻译";
  const sourceLabel = video.source || (typeof video.index === "number" ? `video:${video.index + 1}` : "video");

  return `
    <div class="transcript-debug-card">
      <div class="transcript-debug-head">
        <strong>${escapeHtml(sourceLabel)}</strong>
        <span>${escapeHtml(meta.join(" · ") || "ASR")}</span>
        <em>${escapeHtml(translationState)}</em>
      </div>
      ${transcript.error ? `<div class="transcript-debug-error">${escapeHtml(transcript.error)}</div>` : ""}
      ${transcript.text ? `
        <div class="transcript-debug-block">
          <span>ASR 原文</span>
          <p>${escapeHtml(transcript.text)}</p>
        </div>
      ` : ""}
      ${transcript.text_zh ? `
        <div class="transcript-debug-block translated">
          <span>中文译文</span>
          <p>${escapeHtml(transcript.text_zh)}</p>
        </div>
      ` : ""}
      ${(transcript.segments || []).some(seg => seg.translation_zh) ? `
        <div class="transcript-debug-block translated">
          <span>分段译文</span>
          ${(transcript.segments || []).filter(seg => seg.translation_zh || seg.text).slice(0, 8).map(seg => `
            <p>${escapeHtml(formatSegmentLine(seg))}</p>
          `).join("")}
        </div>
      ` : ""}
    </div>
  `;
}

function renderVideoOcrDebug(item) {
  const videos = (item.video_results || []).filter(video => {
    const track = video.ocr_track || {};
    const states = track.states || [];
    const metrics = track.metrics || {};
    return track.error || states.some(state => state.text || state.text_zh || state.error) || metrics.ocr_calls;
  });
  if (!videos.length) return "";

  return `
    <section class="detail-section transcript-debug-section">
      <h4>视频 OCR / 翻译验证</h4>
      ${videos.map(renderVideoOcrDebugCard).join("")}
    </section>
  `;
}

function renderVideoOcrDebugCard(video) {
  const track = video.ocr_track || {};
  const metrics = track.metrics || {};
  const states = (track.states || []).filter(state => state.text || state.text_zh || state.error).slice(0, 12);
  const sourceLabel = video.source || (typeof video.index === "number" ? `video:${video.index + 1}` : "video");
  const meta = [
    `samples=${metrics.sampled_frames || 0}`,
    `ocr=${metrics.ocr_calls || 0}`,
    `dedupe=${metrics.deduped_samples || 0}`,
    `translate=${metrics.translation_calls || 0}`,
  ];
  const engine = states.find(state => state.engine)?.engine || track.engine || "OCR";
  return `
    <div class="transcript-debug-card">
      <div class="transcript-debug-head">
        <strong>${escapeHtml(sourceLabel)}</strong>
        <span>${escapeHtml(meta.join(" · "))}</span>
        <em>${escapeHtml(track.error ? "OCR 异常" : engine)}</em>
      </div>
      ${track.error ? `<div class="transcript-debug-error">${escapeHtml(track.error)}</div>` : ""}
      ${states.length ? states.map(renderVideoOcrState).join("") : `<p class="detail-empty">暂无 OCR 文本</p>`}
    </div>
  `;
}

function renderVideoOcrState(state) {
  const start = typeof state.start_ts === "number" ? state.start_ts.toFixed(1) : "";
  const end = typeof state.end_ts === "number" ? state.end_ts.toFixed(1) : start;
  const time = start ? `${start}-${end}s` : "";
  const reused = state.reused_count ? ` · 复用 ${state.reused_count}` : "";
  const thumb = state.frame_asset_rel
    ? `<img class="ocr-thumb" src="${escapeAttr(assetUrl(state.frame_asset_rel))}" alt="ocr frame" loading="lazy" />`
    : "";
  return `
    <div class="transcript-debug-block ocr-state">
      <span>${escapeHtml([time, state.engine || "paddleocr_vl"].filter(Boolean).join(" · ") + reused)}</span>
      ${thumb}
      ${state.error ? `<p class="transcript-debug-error">${escapeHtml(state.error)}</p>` : ""}
      ${state.text ? `<p>${escapeHtml(state.text)}</p>` : ""}
      ${state.text_zh ? `<p class="translated-text">${escapeHtml(state.text_zh)}</p>` : ""}
    </div>
  `;
}

function renderOriginalTextEvidence(item) {
  const textEvidence = (item.risk_evidence || []).filter(ev => ev.kind === "text");
  return renderCheckResult(
    "标题 / 正文",
    textEvidence.length ? renderEvidenceCards(textEvidence) : renderSafeTextCheck(item)
  );
}

function renderAudioEvidence(item) {
  const audioEvidence = (item.risk_evidence || []).filter(ev => ev.kind === "audio");
  const transcriptDetails = audioEvidence.length ? renderAudioTranscriptDetails(item) : "";
  return renderCheckResult(
    "视频声音",
    audioEvidence.length ? `${renderEvidenceCards(audioEvidence)}${transcriptDetails}` : renderSafeAudioCheck(item)
  );
}

function renderImageEvidence(item) {
  const images = item.risk_images || [];
  return renderCheckResult(
    "图片内容",
    `
      ${images.length ? `<div class="thumb-grid">${images.map(renderImageCard).join("")}</div>` : ""}
      ${!images.length ? renderSafeImageCheck(item) : ""}
    `
  );
}

function renderImageCard(image) {
  const img = image.asset_rel
    ? `<img class="thumb" src="${escapeAttr(assetUrl(image.asset_rel))}" alt="risk image" loading="lazy" />`
    : "";
  const ocr = image.ocr_text ? `<div>图片文字：${escapeHtml(image.ocr_text)}</div>` : "";
  const ocrZh = image.ocr_text_zh ? `<div>译文：${escapeHtml(image.ocr_text_zh)}</div>` : "";
  return `
    <figure class="risk-image sev-${escapeAttr(image.severity || "")}">
      ${img}
      <figcaption>
        <div><strong>${escapeHtml(image.risk_type || "图片内容")}</strong> · ${escapeHtml(riskLevelLabel(image.severity))}</div>
        <div>${escapeHtml(image.reason || image.evidence || "")}</div>
        ${ocr}
        ${ocrZh}
      </figcaption>
    </figure>
  `;
}

function renderMomentOverviewEvidence(item) {
  const moments = collectVideoMoments(item);
  const hasVideo = moments.length || (item.video_results || []).length;
  if (!hasVideo) return "";
  return renderCheckResult(
    "整体概览",
    moments.length
      ? `<div class="moment-card-grid">${moments.map(renderMomentCard).join("")}</div>`
      : renderSafeOverviewCheck(item)
  );
}

function collectVideoMoments(item) {
  const fromIndex = (item.evidence_index?.moments || []).map(moment => ({
    ...moment,
    analysis: {
      status: moment.status,
      candidate_frame_ids: moment.candidate_frame_ids || [],
      risk_types: moment.risk_types || [],
      reason: moment.reason || "",
      safe_context: moment.safe_context || "",
    },
  }));
  if (fromIndex.length) return fromIndex;
  const moments = [];
  for (const video of (item.video_results || [])) {
    for (const moment of (video.moments || [])) {
      moments.push(moment);
    }
  }
  return moments;
}

function renderMomentCard(moment) {
  const analysis = moment.analysis || moment || {};
  const status = analysis.status === "suspicious" ? "suspicious" : "safe";
  const img = moment.asset_rel
    ? `<img class="moment-sheet-thumb" src="${escapeAttr(assetUrl(moment.asset_rel))}" alt="moment sheet" loading="lazy" />`
    : "";
  const riskTypes = (analysis.risk_types || []).filter(Boolean).join("、");
  const candidateFrames = (analysis.candidate_frame_ids || []).filter(Boolean).join("、");
  const time = moment.start !== undefined || moment.end !== undefined
    ? `${formatSeconds(Number(moment.start || 0))} - ${formatSeconds(Number(moment.end || moment.start || 0))}`
    : "";
  const text = status === "suspicious"
    ? (analysis.reason || "粗审发现可疑画面，已进入局部精审。")
    : (analysis.safe_context || "未发现明显违规线索。");
  return `
    <figure class="moment-card ${status}">
      ${img}
      <figcaption>
        <div class="moment-card-head">
          <span class="moment-status">${status === "suspicious" ? "待复核" : "未发现明显违规线索"}</span>
          <span>${escapeHtml(moment.source || moment.moment_id || "moment")}</span>
        </div>
        ${time ? `<div class="moment-meta">时间：${escapeHtml(time)}</div>` : ""}
        ${riskTypes ? `<div class="moment-meta">类型：${escapeHtml(riskTypes)}</div>` : ""}
        ${candidateFrames ? `<div class="moment-meta">候选帧：${escapeHtml(candidateFrames)}</div>` : ""}
        <p>${escapeHtml(text)}</p>
      </figcaption>
    </figure>
  `;
}

function renderFrameEvidence(item) {
  const showRiskCandidates = shouldShowRiskCandidates(item);
  const frames = showRiskCandidates ? (item.risk_frames || []) : [];
  return renderCheckResult(
    "视频画面",
    `
      ${frames.length ? `<div class="frame-evidence-list">${frames.map(frame => renderFrameCard(hydrateFrameEvidence(frame, item), item)).join("")}</div>` : ""}
      ${!frames.length ? renderSafeFrameCheck(item) : ""}
    `
  );
}

function shouldShowRiskCandidates(item) {
  return item.decision !== "pass" && item.risk_level !== "none";
}

function hydrateFrameEvidence(frame, item) {
  const original = findOriginalFrame(frame, item);
  if (!original) {
    return frame;
  }
  const hydrated = { ...original, ...frame };
  for (const key of ["asset_rel", "local_path", "path", "url"]) {
    if (!hydrated[key] && original[key]) {
      hydrated[key] = original[key];
    }
  }
  return hydrated;
}

function findOriginalFrame(frame, item) {
  const videoIndex = numericValue(frame.video_index);
  const frameIndex = numericValue(frame.frame_index);
  const frameNumber = numericValue(frame.frame_number);
  const timestamp = numericValue(frame.timestamp);
  const frameRel = jobAssetRel(frame.asset_rel || frame.local_path || frame.path || frame.url);
  for (const [videoPosition, video] of (item.video_results || []).entries()) {
    const originalVideoIndex = numericValue(video.index);
    const matchesVideo = videoIndex === null
      || videoIndex === (originalVideoIndex ?? videoPosition);
    if (!matchesVideo) {
      continue;
    }
    const originals = [
      ...(video.timeline_frames || []),
      ...(video.frames || []),
      ...((video.precise_sheets || []).flatMap(sheet => sheet.frames || [])),
    ];
    for (const [originalPosition, original] of originals.entries()) {
      const originalRel = jobAssetRel(original.asset_rel || original.local_path || original.path || original.url);
      if (frameRel && originalRel && frameRel === originalRel) {
        return original;
      }
      const originalFrameIndex = numericValue(original.frame_index) ?? (originalPosition + 1);
      if (frameIndex !== null && originalFrameIndex === frameIndex) {
        return original;
      }
      const originalFrameNumber = numericValue(original.frame_number);
      if (frameNumber !== null && originalFrameNumber === frameNumber) {
        return original;
      }
      const originalTimestamp = numericValue(original.timestamp);
      if (timestamp !== null && originalTimestamp !== null && Math.abs(originalTimestamp - timestamp) < 0.05) {
        return original;
      }
    }
  }
  return null;
}

function numericValue(value) {
  const number = Number(value);
  return Number.isFinite(number) ? number : null;
}

function renderFrameCard(frame, item = {}) {
    const ocrText = frameOcrValue(frame, "ocr_text", "text");
    const ocrTextZh = frameOcrValue(frame, "ocr_text_zh", "text_zh");
    const src = rawAssetUrl(frame.asset_rel || frame.local_path || frame.path || frame.url);
    const isSheet = Boolean(frame.precise_sheet_id || String(frame.asset_rel || "").includes("precise_sheets"));
    const thumb = src ? `<img class="thumb ${isSheet ? "sheet-thumb" : ""}" src="${escapeAttr(src)}" alt="video frame evidence" loading="lazy" />` : "";
    const meta = [
      frame.risk_type || "",
      typeof frame.timestamp === "number" ? formatSeconds(frame.timestamp) : "",
      frame.precise_sheet_id || "",
    ].filter(Boolean).join(" · ");
    const header = meta
      ? `<div><strong>${escapeHtml(meta)}</strong></div>`
      : "";
    const originalText = ocrText ? `<div><span class="frame-card-label">画面文字：</span>${escapeHtml(ocrText)}</div>` : "";
    const translatedText = ocrTextZh ? `<div><span class="frame-card-label">译文：</span>${escapeHtml(ocrTextZh)}</div>` : "";
    const analysisResult = frameDisplayAnalysisResult(frame, item);
    const reason = analysisResult
      ? `<div><span class="frame-card-label">分析结果：</span>${escapeHtml(analysisResult)}</div>`
      : "";
    const moment = frame.moment_id ? `<div><span class="frame-card-label">来源：</span>${escapeHtml(frame.moment_id)}</div>` : "";
    return `
      <figure class="risk-frame sev-${escapeAttr(frame.severity || "")}">
        ${thumb}
        <figcaption>
          ${header}
          ${originalText}
          ${translatedText}
          ${reason}
          ${moment}
        </figcaption>
      </figure>
    `;
}

function frameDisplayAnalysisResult(frame, item = {}) {
  if (frame.analysis_result) {
    return String(frame.analysis_result);
  }
  const summary = String(item.summary || "").trim();
  if (isVideoFrameSummary(summary)) {
    return summary;
  }
  return String(frame.reason || frame.evidence || "");
}

function isVideoFrameSummary(text) {
  return ["视频关键帧", "关键帧", "OCR", "字幕", "画面文字"].some(marker => text.includes(marker));
}

function frameOcrValue(frame, directKey, externalKey) {
  if (frame[directKey]) {
    return String(frame[directKey]);
  }
  for (const item of (frame.external_ocr || [])) {
    if (item && item[externalKey]) {
      return String(item[externalKey]);
    }
  }
  return "";
}

function renderCommentEvidence(item) {
  const commentEvidence = (item.risk_evidence || []).filter(ev => ev.kind === "comment");
  const comments = item.comments || [];
  const commentsById = new Map(comments.map(comment => [String(comment.comment_id || ""), comment]));
  return renderCheckResult(
    "评论区",
    commentEvidence.length ? commentEvidence.map(ev => renderCommentEvidenceRow(ev, commentsById)).join("") : renderSafeCommentCheck(item)
  );
}

function renderCheckResult(title, content) {
  return `
    <div class="check-result-item">
      <h5>${escapeHtml(title)}</h5>
      ${content}
    </div>
  `;
}

function renderCommentEvidenceRow(ev, commentsById) {
  const commentId = String(ev.source || "").replace(/^comment:/, "");
  const comment = commentsById.get(commentId) || {};
  const userId = comment.user_id || comment.short_user_id || comment.user_unique_id || comment.sec_uid || ev.user_id || "未知评论者";
  return `
    <div class="comment-row evidence sev-${escapeAttr(ev.severity || "")}">
      <div class="comment-meta">
        <span>${escapeHtml(comment.nickname || "评论者")}</span>
        <span>ID：${escapeHtml(userId)}</span>
        ${commentId ? `<span>评论：${escapeHtml(commentId)}</span>` : ""}
        ${comment.ip_location ? `<span>${escapeHtml(comment.ip_location)}</span>` : ""}
      </div>
      <div class="ev-text">${escapeHtml(ev.text || comment.content || "（无文本评论）")}</div>
      ${ev.reason ? `<div class="ev-reason">${escapeHtml(ev.reason)}</div>` : ""}
    </div>
  `;
}

function renderEvidenceCards(list) {
  if (!list.length) return "";
  return list.map(ev => {
    const time = (ev.start || ev.end)
      ? `<div class="ev-time">时间：${escapeHtml(ev.start || "")} - ${escapeHtml(ev.end || "")}</div>`
      : "";
    return `
      <div class="evidence sev-${escapeAttr(ev.severity || "")}">
        <div class="ev-head">
          <span class="risk-text">${escapeHtml(riskLevelLabel(ev.severity))}</span>
          <span class="ev-src">${escapeHtml(ev.source || "")}</span>
        </div>
        <div class="ev-text">${escapeHtml(ev.text || "")}</div>
        <div class="ev-reason">${escapeHtml(ev.reason || "")}</div>
        ${time}
      </div>
    `;
  }).join("");
}

function renderEmptyEvidenceNote(text) {
  return `<p class="pass-note">${escapeHtml(text)}</p>`;
}

function renderSafeTextCheck(item) {
  const title = String(item.title || "").trim();
  const desc = String(item.desc || "").trim();
  const body = desc && desc !== title ? desc : "";
  const sample = [
    title ? `标题：${title}` : "",
    body ? `正文：${body}` : "",
  ].filter(Boolean).join("\n");
  return renderSafeCheckCard({
    sourceLabel: "文本检测",
    sampleLabel: sample ? "检测内容" : "检测状态",
    sampleText: sample || "未检测到标题或正文内容。",
  });
}

function renderSafeCommentCheck(item) {
  const comment = (item.comments || []).find(next => String(next.content || next.text || "").trim()) || {};
  const text = String(comment.content || comment.text || "").trim();
  const userId = comment.user_unique_id || comment.short_user_id || comment.user_id || comment.sec_uid || "";
  return renderSafeCheckCard({
    sourceLabel: "评论检测",
    sampleLabel: text ? "评论样例" : "检测状态",
    sampleText: text || "未抓取到可展示的评论内容。",
    meta: [
      ["评论者", comment.nickname || ""],
      ["ID", userId],
      ["位置", comment.ip_location || ""],
    ],
  });
}

function renderSafeImageCheck(item) {
  const image = firstImageAnalysis(item);
  const sample = image ? [
    image.visual_summary ? `画面摘要：${image.visual_summary}` : "",
    image.benign_context ? `安全语境：${image.benign_context}` : "",
    image.ocr_text ? `图片文字：${image.ocr_text}` : "",
    image.ocr_text_zh ? `译文：${image.ocr_text_zh}` : "",
  ].filter(Boolean).join("\n") : "";
  const src = image ? rawAssetUrl(image.asset_rel || image.local_path || image.path_or_url || image.url) : "";
  return renderSafeCheckCard({
    sourceLabel: "图片检测",
    sampleLabel: sample ? "图片分析摘要" : "检测状态",
    sampleText: sample || "未检测到可展示的图片分析结果。",
    thumbnail: src,
    thumbnailAlt: "图片检测样例",
  });
}

function renderSafeOverviewCheck(item) {
  const moment = collectVideoMoments(item)[0] || firstMomentSheet(item);
  const analysis = moment?.analysis || moment || {};
  const sample = moment ? [
    analysis.safe_context ? `概览结论：${analysis.safe_context}` : "",
    analysis.reason ? `粗审摘要：${analysis.reason}` : "",
  ].filter(Boolean).join("\n") : "";
  const src = moment ? rawAssetUrl(moment.asset_rel || moment.path || "") : "";
  return renderSafeCheckCard({
    sourceLabel: "视频整体概览",
    sampleLabel: sample ? "Moment 摘要" : "检测状态",
    sampleText: sample || "未检测到可展示的视频整体概览结果。",
    thumbnail: src,
    thumbnailAlt: "视频整体概览样例",
    meta: [
      ["时间", moment && moment.start !== undefined ? `${formatSeconds(Number(moment.start || 0))} - ${formatSeconds(Number(moment.end || moment.start || 0))}` : ""],
    ],
  });
}

function renderSafeFrameCheck(item) {
  const frame = firstVideoFrame(item);
  const ocrText = frame ? frameOcrValue(frame, "ocr_text", "text") : "";
  const ocrTextZh = frame ? frameOcrValue(frame, "ocr_text_zh", "text_zh") : "";
  const sample = frame ? [
    frame.visual_summary ? `画面摘要：${frame.visual_summary}` : "",
    frame.benign_context ? `安全语境：${frame.benign_context}` : "",
    ocrText ? `画面文字：${ocrText}` : "",
    ocrTextZh ? `译文：${ocrTextZh}` : "",
  ].filter(Boolean).join("\n") : "";
  const src = frame ? rawAssetUrl(frame.asset_rel || frame.local_path || frame.path || frame.url) : "";
  return renderSafeCheckCard({
    sourceLabel: "视频画面检测",
    sampleLabel: sample ? "关键帧摘要" : "检测状态",
    sampleText: sample || "未检测到可展示的视频关键帧分析结果。",
    thumbnail: src,
    thumbnailAlt: "关键帧检测样例",
    meta: [
      ["时间", frame && typeof frame.timestamp === "number" ? formatSeconds(frame.timestamp) : ""],
    ],
  });
}

function renderSafeAudioCheck(item) {
  const transcript = firstTranscript(item);
  const original = transcriptOriginalText(transcript);
  const translated = transcriptTranslatedText(transcript);
  const segments = transcriptSegments(transcript)
    .filter(seg => seg.translation_zh || seg.source_text_dolphin || seg.source_text || seg.text)
    .slice(0, 6);
  const provider = translated && original ? ((transcript.translation || {}).provider || "中文译文") : "";
  const details = [
    original ? renderSafeAudioTextBlock("ASR 全文原文", original) : "",
    translated ? renderSafeAudioTextBlock("ASR 全文译文", translated, "translated") : "",
    segments.length ? `
      <div class="safe-check-detail">
        <span>带时间戳分段</span>
        ${segments.map(seg => `<p>${escapeHtml(formatSegmentLine(seg))}</p>`).join("")}
      </div>
    ` : "",
  ].filter(Boolean).join("");
  return renderSafeCheckCard({
    sourceLabel: "视频声音检测",
    sampleLabel: original || translated ? "语音转写与翻译" : "检测状态",
    sampleText: original || translated
      ? "已完成语音转写，原文、译文和分段如下。"
      : "未检测到可展示的口播或声音转写内容。",
    meta: [
      ["来源", provider],
      ["语言", transcript?.language || ""],
      ["引擎", transcript?.asr_engine || transcript?.provider || ""],
    ],
    detailsHtml: details,
  });
}

function renderAudioTranscriptDetails(item) {
  const transcript = firstTranscript(item);
  const original = transcriptOriginalText(transcript);
  const translated = transcriptTranslatedText(transcript);
  const segments = transcriptSegments(transcript)
    .filter(seg => seg.translation_zh || seg.source_text_dolphin || seg.source_text || seg.text)
    .slice(0, 8);
  if (!original && !translated && !segments.length) return "";
  const meta = [
    transcript?.language ? `语言：${transcript.language}` : "",
    (transcript?.asr_engine || transcript?.provider) ? `引擎：${transcript.asr_engine || transcript.provider}` : "",
    (transcript?.translation || {}).provider ? `翻译：${transcript.translation.provider}` : "",
  ].filter(Boolean).join(" · ");
  return `
    <div class="safe-check-card audio-transcript-card">
      <div class="safe-check-head">
        <span class="safe-check-badge neutral">ASR 转写详情</span>
        ${meta ? `<span>${escapeHtml(meta)}</span>` : ""}
      </div>
      <p class="safe-check-copy">以下展示语音转写原文、中文译文和时间戳分段供复核。</p>
      <div class="safe-check-details">
        ${original ? renderSafeAudioTextBlock("ASR 全文原文", original) : ""}
        ${translated ? renderSafeAudioTextBlock("ASR 全文译文", translated, "translated") : ""}
        ${segments.length ? `
          <div class="safe-check-detail">
            <span>带时间戳分段</span>
            ${segments.map(seg => `<p>${escapeHtml(formatSegmentLine(seg))}</p>`).join("")}
          </div>
        ` : ""}
      </div>
    </div>
  `;
}

function renderSafeAudioTextBlock(label, text, className = "") {
  const value = String(text || "").trim();
  if (!value) return "";
  const truncated = truncateText(value, 520);
  const suffix = truncated.length < value.length ? "（截断展示）" : "";
  return `
    <div class="safe-check-detail ${escapeAttr(className)}">
      <span>${escapeHtml(label + suffix)}</span>
      <p dir="auto">${escapeHtml(truncated)}</p>
    </div>
  `;
}

function formatSegmentLine(seg) {
  const start = Number.isFinite(Number(seg.start)) ? formatSeconds(Number(seg.start)) : "";
  const end = Number.isFinite(Number(seg.end)) ? formatSeconds(Number(seg.end)) : "";
  const range = start ? `${start}-${end || start}` : "";
  const translation = seg.translation_zh || "";
  const original = seg.source_text_dolphin || seg.source_text || seg.text || "";
  return [range, translation, original ? `原文：${original}` : ""].filter(Boolean).join(" · ");
}

function transcriptSegments(transcript) {
  if (!transcript) return [];
  const direct = Array.isArray(transcript.segments) ? transcript.segments : [];
  if (direct.length) return direct;
  const translated = transcript.translation && Array.isArray(transcript.translation.segments)
    ? transcript.translation.segments
    : [];
  return translated;
}

function transcriptOriginalText(transcript) {
  if (!transcript) return "";
  const direct = String(transcript.text || "").trim();
  if (direct) return direct;
  return transcriptSegments(transcript)
    .map(seg => String(seg.source_text_dolphin || seg.source_text || seg.text || "").trim())
    .filter(Boolean)
    .join("\n");
}

function transcriptTranslatedText(transcript) {
  if (!transcript) return "";
  const direct = String(transcript.text_zh || (transcript.translation || {}).text || "").trim();
  if (direct) return direct;
  return transcriptSegments(transcript)
    .map(seg => String(seg.translation_zh || "").trim())
    .filter(Boolean)
    .join("\n");
}

function renderSafeCheckCard({ sourceLabel, sampleLabel, sampleText, meta = [], thumbnail = "", thumbnailAlt = "", detailsHtml = "" }) {
  const metaHtml = meta
    .filter(([, value]) => String(value || "").trim())
    .map(([label, value]) => `<span>${escapeHtml(label)}：${escapeHtml(value)}</span>`)
    .join("");
  const thumb = thumbnail
    ? `<img class="safe-check-thumb" src="${escapeAttr(thumbnail)}" alt="${escapeAttr(thumbnailAlt || sourceLabel)}" loading="lazy" />`
    : "";
  return `
    <div class="safe-check-card">
      <div class="safe-check-head">
        <span class="safe-check-badge">未发现明显违规线索</span>
        <span>${escapeHtml(sourceLabel || "检测结果")}</span>
      </div>
      <p class="safe-check-copy">以下展示一条检测摘要供复核。</p>
      <div class="safe-check-body">
        ${thumb}
        <div class="safe-check-main">
          ${metaHtml ? `<div class="safe-check-meta">${metaHtml}</div>` : ""}
          <span>${escapeHtml(sampleLabel || "检测摘要")}</span>
          <p dir="auto">${escapeHtml(sampleText || "暂无可展示的检测摘要。")}</p>
          ${detailsHtml ? `<div class="safe-check-details">${detailsHtml}</div>` : ""}
        </div>
      </div>
    </div>
  `;
}

function firstImageAnalysis(item) {
  return (item.image_analyses || item.image_results || [])
    .find(image => image && (image.visual_summary || image.ocr_text || image.benign_context || image.asset_rel || image.local_path || image.url)) || null;
}

function firstVideoFrame(item) {
  for (const video of (item.video_results || [])) {
    const frame = ([...(video.timeline_frames || []), ...(video.frames || [])]).find(next => next && (
      next.visual_summary
      || next.benign_context
      || next.ocr_text
      || next.ocr_text_zh
      || next.asset_rel
      || next.local_path
      || next.path
    ));
    if (frame) return frame;
  }
  return null;
}

function firstMomentSheet(item) {
  const indexed = item.evidence_index?.moment_sheets || [];
  if (indexed.length) return indexed[0];
  for (const video of (item.video_results || [])) {
    const sheet = (video.moment_sheets || [])[0];
    if (sheet) return sheet;
  }
  return null;
}

function firstTranscript(item) {
  for (const video of (item.video_results || [])) {
    const transcript = video.transcript || {};
    if (transcriptOriginalText(transcript) || transcriptTranslatedText(transcript) || (transcriptSegments(transcript) || []).length) {
      return transcript;
    }
  }
  return null;
}

function selectedProfileContext(item) {
  const groups = buildUserGroups(currentItems);
  const group = groups.find(candidate => candidate.key === selectedUserKey);
  const items = group ? group.items.map(entry => entry.item) : [item].filter(Boolean);
  return {
    group,
    items,
    author: group?.author || item?.author || {},
  };
}

function buildProfileStats(items) {
  const decisions = { reject: 0, review: 0, pass: 0 };
  items.forEach(next => {
    const decision = normalizeDecision(next.decision);
    decisions[decision] += 1;
  });
  const evidenceSources = items.reduce((acc, next) => addEvidenceSources(acc, next), {
    text: 0,
    comment: 0,
    image: 0,
    video: 0,
  });
  const totalEvidence = Object.values(evidenceSources).reduce((sum, value) => sum + value, 0);
  const tagCounts = profileTagCounts(items);
  const topTags = [...tagCounts.entries()]
    .sort((a, b) => (b[1] - a[1]) || a[0].localeCompare(b[0], "zh-CN"))
    .slice(0, 6)
    .map(([label, count]) => ({ label, count }));
  return {
    decisions,
    evidenceSources,
    totalEvidence,
    topTags,
    riskScore: decisions.reject * 2 + decisions.review,
    representativeItems: sortProfileRepresentativeItems(items).slice(0, 5),
  };
}

function addEvidenceSources(acc, item) {
  (item.risk_evidence || []).forEach(ev => {
    const kind = ev.kind || "";
    if (kind === "comment") {
      acc.comment += 1;
    } else if (kind === "audio") {
      acc.video += 1;
    } else if (kind === "ocr") {
      acc.video += 1;
    } else if (kind === "image_ref") {
      acc.image += 1;
    } else if (kind === "frame_ref") {
      acc.video += 1;
    } else {
      acc.text += 1;
    }
  });
  acc.image += (item.risk_images || []).length;
  acc.video += (item.risk_frames || []).length;
  return acc;
}

function profileTagCounts(items) {
  const counts = new Map();
  items.forEach(item => {
    const categories = (item.categories || []).filter(Boolean);
    const fallbackRiskTypes = [
      ...(item.risk_images || []).map(image => image.risk_type),
      ...(item.risk_frames || []).map(frame => frame.risk_type),
    ].filter(Boolean);
    const labels = categories.length ? categories : fallbackRiskTypes;
    labels.forEach(label => {
      const key = String(label).trim();
      if (!key) return;
      counts.set(key, (counts.get(key) || 0) + 1);
    });
  });
  return counts;
}

function sortProfileRepresentativeItems(items) {
  return [...items].sort((a, b) => (
    decisionRank(b) - decisionRank(a)
    || riskLevelRank(b) - riskLevelRank(a)
    || evidenceCount(b) - evidenceCount(a)
    || auditResultId(b) - auditResultId(a)
  ));
}

function normalizeDecision(decision) {
  return ["reject", "review", "pass"].includes(decision) ? decision : "review";
}

function decisionLabel(decision) {
  return {
    reject: "拒绝",
    review: "复核",
    pass: "通过",
  }[normalizeDecision(decision)];
}

function riskLevelLabel(level) {
  return {
    high: "高风险",
    medium: "中风险",
    low: "低风险",
    none: "无风险",
    unknown: "未知",
  }[level || "unknown"] || level;
}

function profileUrl(author, platform) {
  const direct = author.profile_url || author.homepage_url || author.homepage || author.user_url || author.url;
  if (direct) return direct;
  if (platform === "xhs" && author.user_id) {
    return `https://www.xiaohongshu.com/user/profile/${encodeURIComponent(author.user_id)}`;
  }
  if (platform === "dy" && author.sec_uid) {
    return `https://www.douyin.com/user/${encodeURIComponent(author.sec_uid)}`;
  }
  if (platform === "ks" && author.user_id) {
    return `https://www.kuaishou.com/profile/${encodeURIComponent(author.user_id)}`;
  }
  return "";
}

function percentText(count, total) {
  if (!total) return "0%";
  return `${Math.round((count / total) * 100)}%`;
}

function profilePieStyle(decisions) {
  const total = decisions.reject + decisions.review + decisions.pass;
  if (!total) {
    return "--pie: conic-gradient(#e2e8f0 0deg 360deg);";
  }
  const rejectEnd = (decisions.reject / total) * 360;
  const reviewEnd = rejectEnd + (decisions.review / total) * 360;
  return `--pie: conic-gradient(#dc2626 0deg ${rejectEnd.toFixed(2)}deg, #f59e0b ${rejectEnd.toFixed(2)}deg ${reviewEnd.toFixed(2)}deg, #16a34a ${reviewEnd.toFixed(2)}deg 360deg);`;
}

function renderProfileIdentity(author, item, items) {
  const platform = author.platform || item.platform || "";
  const homeUrl = profileUrl(author, platform);
  const userId = author.user_id || author.sec_uid || author.user_unique_id || author.short_user_id || selectedUserKey || "-";
  const fields = [
    ["昵称", author.nickname || "未知"],
    ["ID", userId],
    ["平台", platformLabel(platform)],
    ["IP 属地", author.ip_location || "-"],
    ["本任务内内容数", `${items.length}`],
  ];
  return `
    <section class="detail-section profile-section">
      <div class="profile-section-head">
        <h4>基础身份</h4>
        ${homeUrl ? `<a class="open-link" href="${escapeAttr(homeUrl)}" target="_blank">主页链接</a>` : ""}
      </div>
      <div class="profile-grid">
        ${fields.map(([label, value]) => `
          <div><span>${escapeHtml(label)}</span><strong>${escapeHtml(value)}</strong></div>
        `).join("")}
      </div>
    </section>
  `;
}

function renderRiskOverview(stats, items) {
  const { decisions, riskScore } = stats;
  const total = items.length;
  const riskCount = decisions.reject + decisions.review;
  const riskPercent = percentText(riskCount, total);
  const summary = riskCount > 0
    ? `本任务内 ${total} 条内容中，${riskCount} 条需要关注。`
    : "本任务内暂无拒绝或复核内容。";
  return `
    <section class="detail-section profile-section">
      <h4>风险概览</h4>
      <div class="profile-overview">
        <div class="profile-risk-score">
          <span>风险值</span>
          <strong>${riskScore}</strong>
          <em>拒绝 ${decisions.reject}×2 + 复核 ${decisions.review}×1</em>
        </div>
        <div class="profile-risk-summary profile-risk-chart-card">
          <div class="profile-risk-copy">
            <p class="profile-note">${escapeHtml(summary)}</p>
          </div>
          <div class="profile-pie" style="${escapeAttr(profilePieStyle(decisions))}">
            <span>${escapeHtml(riskPercent)}</span>
            <small>风险内容</small>
          </div>
        </div>
      </div>
    </section>
  `;
}

function renderEvidenceSourceBars(stats) {
  const rows = [
    ["文本", "text"],
    ["评论", "comment"],
    ["图片", "image"],
    ["视频证据", "video"],
  ].map(([label, key]) => ({ label, key, value: stats.evidenceSources[key] || 0 }));
  const max = Math.max(1, ...rows.map(row => row.value));
  return `
    <section class="detail-section profile-section">
      <h4>证据来源分布</h4>
      <div class="source-bars">
        ${rows.map(row => `
          <div class="source-bar-row">
            <span>${escapeHtml(row.label)}</span>
            <div class="source-bar-track"><i style="width: ${row.value ? Math.max(4, (row.value / max) * 100).toFixed(1) : 0}%"></i></div>
            <strong>${row.value}</strong>
          </div>
        `).join("")}
      </div>
      <p class="profile-note">按结构化证据条数统计，共 ${stats.totalEvidence} 条。</p>
    </section>
  `;
}

function renderRepresentativeItems(stats) {
  return `
    <section class="detail-section profile-section">
      <h4>代表风险内容</h4>
      <div class="profile-risk-list">
        ${stats.representativeItems.length ? stats.representativeItems.map(next => {
          const id = resultStableId(next);
          const tags = (next.categories || []).filter(Boolean).slice(0, 3).join("、") || "未分类";
          return `
            <div class="profile-risk-item">
              <div>
                <strong>${escapeHtml(contentDisplayTitle(next, 52))}</strong>
                <span>${escapeHtml(tags)}</span>
              </div>
              <div class="profile-risk-meta">
                <span class="badge ${escapeAttr(normalizeDecision(next.decision))}">${escapeHtml(decisionLabel(next.decision))}</span>
                <span>风险 ${escapeHtml(riskLevelLabel(next.risk_level))}</span>
                <span>证据 ${evidenceCount(next)}</span>
                <button class="table-action" type="button" onclick="openProfileContent('${escapeJs(id)}')">查看详情</button>
              </div>
            </div>
          `;
        }).join("") : `<p class="detail-empty">暂无内容</p>`}
      </div>
    </section>
  `;
}

function renderProfileConclusion(stats, items) {
  const { decisions, topTags, evidenceSources } = stats;
  const riskyCount = decisions.reject + decisions.review;
  const topLabelText = topTags.slice(0, 2).map(tag => tag.label).join("和") || "明确风险标签";
  const sourcePriority = [
    ["评论", evidenceSources.comment],
    ["图片", evidenceSources.image],
    ["文本", evidenceSources.text],
    ["视频证据", evidenceSources.video],
  ].filter(([, value]) => value > 0).sort((a, b) => b[1] - a[1]).slice(0, 2).map(([label]) => label);
  const sourceText = sourcePriority.length ? `，并重点查看${sourcePriority.join("与")}证据` : "";
  let conclusion = `该账号本任务内 ${items.length} 条内容暂未出现拒绝或复核结论，可按普通优先级抽检。`;
  if (decisions.reject > 0) {
    conclusion = `该账号本任务内多次出现${topLabelText}风险，已有 ${decisions.reject} 条拒绝、${decisions.review} 条需复核，建议优先复核${sourceText}。`;
  } else if (riskyCount > 0) {
    conclusion = `该账号本任务内出现 ${decisions.review} 条需复核内容，主要涉及${topLabelText}，建议进入人工复核${sourceText}。`;
  }
  return `
    <section class="detail-section profile-section profile-conclusion">
      <h4>画像结论</h4>
      <p>${escapeHtml(conclusion)}</p>
    </section>
  `;
}

function renderUserProfile(item, options = {}) {
  const { author, items } = selectedProfileContext(item);
  const stats = buildProfileStats(items);
  const showTitle = options.showTitle !== false;
  return `
    <div class="profile-panel">
      ${showTitle ? `<div class="profile-title">
        <h4>用户画像：@${escapeHtml(author.nickname || author.user_unique_id || author.user_id || "未知账号")}</h4>
      </div>` : ""}
      ${renderProfileIdentity(author, item, items)}
      ${renderRiskOverview(stats, items)}
      ${renderEvidenceSourceBars(stats)}
      ${renderRepresentativeItems(stats)}
      ${renderProfileConclusion(stats, items)}
    </div>
  `;
}

function escapeJs(value) {
  return String(value ?? "").replaceAll("\\", "\\\\").replaceAll("'", "\\'");
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function escapeAttr(value) {
  return escapeHtml(value).replaceAll("`", "&#096;");
}
