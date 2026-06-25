let currentJobId = null;
let pollTimer = null;
let currentItems = [];

const apiBase = (window.XHS_AUDIT_API_BASE || "").replace(/\/$/, "");
const form = document.getElementById("job-form");
const localVideoForm = document.getElementById("local-video-form");
const statusEl = document.getElementById("job-status");
const logsEl = document.getElementById("logs");
const listEl = document.getElementById("item-list");
const detailEl = document.getElementById("detail");
const runCrawlerEl = document.getElementById("run-crawler");
const sourceOutputEl = document.getElementById("source-output");

loadOutputs();
updateSourceOutputState();
runCrawlerEl.addEventListener("change", updateSourceOutputState);

form.addEventListener("submit", async (event) => {
  event.preventDefault();

  const runCrawler = runCrawlerEl.checked;
  if (!runCrawler && !sourceOutputEl.value) {
    statusEl.textContent = "请先选择一个已有输出";
    return;
  }

  const payload = {
    platform: document.getElementById("platform").value,
    keyword: document.getElementById("keyword").value,
    start_page: Number(document.getElementById("start-page").value),
    max_notes: Number(document.getElementById("max-notes").value),
    max_comments: Number(document.getElementById("max-comments").value),
    max_concurrency: Number(document.getElementById("max-concurrency").value),
    get_sub_comment: document.getElementById("get-sub-comment").checked,
    analyze_limit: Number(document.getElementById("analyze-limit").value),
    run_crawler: runCrawler,
    source_output_id: runCrawler ? null : sourceOutputEl.value,
    analysis_batch_size: Number(document.getElementById("analysis-batch-size").value)
  };

  statusEl.textContent = "提交中...";
  logsEl.textContent = "";

  const response = await apiFetch("/api/jobs", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload)
  });
  await handleCreatedJobResponse(response);
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
  logsEl.textContent = "";

  const response = await apiFetch("/api/local-video-jobs", {
    method: "POST",
    body: formData
  });
  await handleCreatedJobResponse(response);
});

async function handleCreatedJobResponse(response) {
  const data = await response.json();
  if (!response.ok) {
    statusEl.textContent = "提交失败";
    logsEl.textContent = JSON.stringify(data, null, 2);
    return;
  }

  currentJobId = data.id;
  currentItems = [];
  renderList();
  renderDetail(null);
  startPolling();
}

function apiFetch(path, options) {
  return fetch(`${apiBase}${path}`, options);
}

function updateSourceOutputState() {
  const enabled = !runCrawlerEl.checked;
  sourceOutputEl.disabled = !enabled;
  sourceOutputEl.closest("label").classList.toggle("disabled", !enabled);
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
      option.value = output.id;
      option.textContent = `${output.id} · ${output.contents_count} 条 · 图 ${output.image_count} · 视频 ${output.video_count}`;
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

function startPolling() {
  if (pollTimer) clearInterval(pollTimer);
  poll();
  pollTimer = setInterval(poll, 2500);
}

async function poll() {
  if (!currentJobId) return;
  const response = await apiFetch(`/api/jobs/${currentJobId}`);
  const job = await response.json();
  const source = job.input_type === "local_video"
    ? `本地视频 ${job.input_filename || ""}`
    : (job.run_crawler ? job.keyword : `已有输出 ${job.source_output_id || ""}`);
  statusEl.textContent = `${job.status} · ${source}`;
  logsEl.textContent = (job.logs || []).map(log => `[${log.time}] ${log.message}`).join("\n");
  logsEl.scrollTop = logsEl.scrollHeight;
  currentItems = job.items || [];
  renderList();
  if (job.status === "failed" && job.error) {
    logsEl.textContent = `${logsEl.textContent}\n\n错误详情：\n${job.error}`;
    logsEl.scrollTop = logsEl.scrollHeight;
  }
  if (["completed", "failed"].includes(job.status) && pollTimer) {
    clearInterval(pollTimer);
    pollTimer = null;
  }
}

function renderList() {
  if (!currentItems.length) {
    listEl.innerHTML = `<p class="detail-empty">暂无结果</p>`;
    return;
  }
  listEl.innerHTML = currentItems.map((item, index) => {
    const decision = item.decision || "review";
    return `
      <div class="item-card ${decision}" onclick="renderDetailByIndex(${index})">
        <div class="item-title">${escapeHtml(item.title || item.note_id)}</div>
        <div class="item-meta">${escapeHtml(item.url || item.local_path || "")}</div>
        <span class="badge ${decision}">${decision} · ${item.risk_level || "unknown"}</span>
      </div>
    `;
  }).join("");
}

window.renderDetailByIndex = (index) => {
  renderDetail(currentItems[index]);
};

function renderDetail(item) {
  if (!item) {
    detailEl.className = "detail-empty";
    detailEl.textContent = "选择左侧内容查看详情";
    return;
  }
  detailEl.className = "";
  detailEl.innerHTML = `
    <h3>${escapeHtml(item.title || item.note_id)}</h3>
    <p><a href="${escapeAttr(item.url)}" target="_blank">打开原文</a></p>
    <div class="detail-section">
      <strong>审核结论</strong>
      <p><span class="badge ${item.decision}">${item.decision}</span> 风险等级：${escapeHtml(item.risk_level || "")}</p>
      <p>类别：${escapeHtml((item.categories || []).join("、"))}</p>
      <p>${escapeHtml(item.summary || "")}</p>
    </div>
    <div class="detail-section">
      <strong>违规/疑似违规证据</strong>
      ${renderEvidence(item.evidence || [])}
    </div>
    <div class="detail-section">
      <strong>正文</strong>
      <p>${escapeHtml(item.desc || "")}</p>
    </div>
    <div class="detail-section">
      <strong>图片分析</strong>
      <pre class="json">${escapeHtml(JSON.stringify(item.image_analyses || [], null, 2))}</pre>
    </div>
    <div class="detail-section">
      <strong>视频分析</strong>
      <pre class="json">${escapeHtml(JSON.stringify(item.video_results || [], null, 2))}</pre>
    </div>
  `;
}

function renderEvidence(evidence) {
  if (!evidence.length) return `<p class="detail-empty">暂无明确证据</p>`;
  return evidence.map(ev => `
    <div class="evidence">
      <div><strong>${escapeHtml(ev.source || "")}</strong> <span class="risk-text">${escapeHtml(ev.severity || "")}</span></div>
      <div>${escapeHtml(ev.text || "")}</div>
      <div>${escapeHtml(ev.reason || "")}</div>
      ${(ev.start || ev.end) ? `<div>时间：${escapeHtml(ev.start || "")} - ${escapeHtml(ev.end || "")}</div>` : ""}
    </div>
  `).join("");
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
