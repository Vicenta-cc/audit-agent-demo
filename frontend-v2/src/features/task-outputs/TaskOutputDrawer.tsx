import { Copy, Download, Search, X } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import type { ReactNode } from "react";
import { Button } from "../../components/common/Button";
import { IconButton } from "../../components/common/IconButton";
import type { AuditResult, JobLog, MonitorTask } from "../../types/jobs";
import {
  formatTimeOnly,
  getOutputNumber,
  getPlanVersion,
  getRevisionId,
  type TaskDrawerType
} from "./taskOutputUtils";

interface TaskOutputDrawerProps {
  activeDrawer: TaskDrawerType;
  task: MonitorTask;
  outputs: AuditResult[];
  onClose: () => void;
}

type LogLevel = "INFO" | "WARN" | "ERROR";

interface TerminalLog {
  id: string;
  time: string;
  level: LogLevel;
  scope: string;
  message: string;
  stack?: string;
}

export function TaskOutputDrawer({ activeDrawer, task, outputs, onClose }: TaskOutputDrawerProps) {
  useEffect(() => {
    if (!activeDrawer) {
      return;
    }
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        onClose();
      }
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [activeDrawer, onClose]);

  if (!activeDrawer) {
    return null;
  }

  return (
    <div className="output-drawer-backdrop" role="presentation" onClick={onClose}>
      <aside
        className={`output-side-drawer output-side-drawer-${activeDrawer}`}
        role="dialog"
        aria-modal="true"
        aria-labelledby="output-drawer-title"
        onClick={(event) => event.stopPropagation()}
      >
        {activeDrawer === "logs" ? (
          <TaskLogDrawer task={task} outputs={outputs} onClose={onClose} />
        ) : (
          <TaskConfigDrawer task={task} onClose={onClose} />
        )}
      </aside>
    </div>
  );
}

function TaskLogDrawer({ task, outputs, onClose }: { task: MonitorTask; outputs: AuditResult[]; onClose: () => void }) {
  const [level, setLevel] = useState<"全部" | LogLevel>("全部");
  const [query, setQuery] = useState("");
  const [autoScroll, setAutoScroll] = useState(true);
  const [paused, setPaused] = useState(false);
  const [rows, setRows] = useState<TerminalLog[]>(() => buildInitialLogs(task, outputs));
  const terminalRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    setRows(buildInitialLogs(task, outputs));
    setQuery("");
    setLevel("全部");
    setPaused(false);
  }, [task.id, outputs]);

  useEffect(() => {
    if (paused) {
      return;
    }
    const timer = window.setInterval(() => {
      setRows((current) => [...current.slice(-199), buildLiveLog(task, outputs, current.length)]);
    }, 3200);
    return () => window.clearInterval(timer);
  }, [outputs, paused, task]);

  const visibleRows = useMemo(() => {
    const keyword = query.trim().toLowerCase();
    return rows.filter((row) => {
      const matchesLevel = level === "全部" || row.level === level;
      const matchesQuery = !keyword || `${row.time} ${row.level} ${row.scope} ${row.message}`.toLowerCase().includes(keyword);
      return matchesLevel && matchesQuery;
    });
  }, [level, query, rows]);

  useEffect(() => {
    if (!autoScroll || !terminalRef.current) {
      return;
    }
    terminalRef.current.scrollTop = terminalRef.current.scrollHeight;
  }, [autoScroll, visibleRows.length]);

  const terminalText = visibleRows.map((row) => `${row.time} ${row.level.padEnd(5, " ")} ${row.scope} ${row.message}`).join("\n");

  return (
    <>
      <header className="output-drawer-header">
        <div>
          <span className="drawer-kicker">任务运行</span>
          <h2 id="output-drawer-title">运行日志（共 {rows.length} 条）</h2>
        </div>
        <IconButton type="button" aria-label="关闭运行日志" onClick={onClose}>
          <X size={20} />
        </IconButton>
      </header>

      <div className="log-drawer-toolbar">
        <div className="log-level-tabs" role="tablist" aria-label="日志级别">
          {(["全部", "INFO", "WARN", "ERROR"] as const).map((item) => (
            <button key={item} type="button" className={level === item ? "is-active" : ""} onClick={() => setLevel(item)}>
              {item}
            </button>
          ))}
        </div>
        <label className="drawer-switch">
          <input type="checkbox" checked={autoScroll} onChange={(event) => setAutoScroll(event.target.checked)} />
          自动滚动
        </label>
        <button type="button" className={`drawer-tool-button${paused ? " is-active" : ""}`} onClick={() => setPaused((value) => !value)}>
          暂停刷新
        </button>
        <label className="drawer-search">
          <Search size={16} />
          <input value={query} placeholder="搜索日志" onChange={(event) => setQuery(event.target.value)} />
        </label>
        <button type="button" className="drawer-tool-button" onClick={() => setRows([])}>
          清空当前显示
        </button>
        <button type="button" className="drawer-tool-button" onClick={() => void navigator.clipboard?.writeText(terminalText)}>
          <Copy size={15} />
          复制
        </button>
        <button type="button" className="drawer-tool-button" onClick={() => downloadText(`${task.id}-logs.txt`, terminalText)}>
          <Download size={15} />
          下载日志
        </button>
      </div>

      <div className="log-terminal" ref={terminalRef}>
        {visibleRows.length ? (
          visibleRows.map((row) => (
            <div className={`log-terminal-line level-${row.level.toLowerCase()}`} key={row.id}>
              <span>{row.time}</span>
              <strong>{row.level}</strong>
              <em>{row.scope}</em>
              <p>{row.message}</p>
              {row.stack ? <details><summary>展开完整错误堆栈</summary><pre>{row.stack}</pre></details> : null}
            </div>
          ))
        ) : (
          <div className="log-terminal-empty">当前筛选下没有日志</div>
        )}
      </div>
    </>
  );
}

function TaskConfigDrawer({ task, onClose }: { task: MonitorTask; onClose: () => void }) {
  const [tab, setTab] = useState<"current" | "history">("current");
  const revision = task.raw.current_audit_config_revision;
  const raw = task.raw as unknown as Record<string, unknown>;
  const libraries = revision?.audit_config?.library_ids || task.raw.library_ids || [];
  const capabilities = revision?.audit_config?.capabilities || task.raw.capabilities || [];
  const revisionId = getRevisionId(task);

  return (
    <>
      <header className="output-drawer-header">
        <div>
          <span className="drawer-kicker">审核配置</span>
          <h2 id="output-drawer-title">查看配置</h2>
        </div>
        <IconButton type="button" aria-label="关闭审核配置" onClick={onClose}>
          <X size={20} />
        </IconButton>
      </header>

      <div className="config-drawer-tabs">
        <button type="button" className={tab === "current" ? "is-active" : ""} onClick={() => setTab("current")}>
          当前配置
        </button>
        <button type="button" className={tab === "history" ? "is-active" : ""} onClick={() => setTab("history")}>
          变更记录
        </button>
      </div>

      <div className="config-drawer-body">
        {tab === "current" ? (
          <>
            <ConfigSection title="方案信息">
              <ConfigKv label="方案名称" value={revision?.source_policy_name || task.referencePlan} />
              <ConfigKv label="方案版本" value={String(revision?.source_policy_version || getPlanVersion(task))} />
              <ConfigKv label="配置 Revision" value={revisionId} />
              <ConfigKv label="生效时间" value={revision?.effective_from ? formatTimeOnly(revision.effective_from) : task.updatedDisplay} />
              <ConfigKv label="更新人" value={String((revision as { updated_by?: string; operator?: string } | undefined)?.updated_by || "系统")} />
            </ConfigSection>

            <ConfigSection title="任务来源">
              <ConfigKv label="任务类型" value={task.sourceLabel} />
              <ConfigKv label="平台" value={task.platformLabel} />
            </ConfigSection>

            <ConfigSection title="绑定知识库">
              {libraries.length ? (
                <div className="config-chip-list">
                  {libraries.map((item) => (
                    <span key={item}>{item}</span>
                  ))}
                </div>
              ) : (
                <p className="config-muted">按任务配置使用默认知识库</p>
              )}
            </ConfigSection>

            <ConfigSection title="检测位置">
              <div className="config-check-grid">
                {["标题与正文", "评论与弹幕", "图片与封面", "视频画面", "语音内容"].map((item) => (
                  <span key={item}>{item}</span>
                ))}
              </div>
            </ConfigSection>

            <ConfigSection title="识别能力">
              <div className="config-check-grid">
                {["文本语义", "OCR", "ASR", "视觉识别", "评论聚集"].map((item) => (
                  <span key={item} className={isCapabilityEnabled(item, capabilities) ? "" : "is-muted"}>
                    {item}
                  </span>
                ))}
              </div>
            </ConfigSection>

            <ConfigSection title="执行参数">
              <ConfigKv label="抓取频率" value={String(raw.crawl_interval || raw.crawl_frequency || "按任务配置")} />
              <ConfigKv label="分析轮询" value={String(raw.analysis_polling || "实时队列")} />
              <ConfigKv label="并发数" value={String(raw.analysis_batch_size || raw.concurrency || "默认")} />
            </ConfigSection>
          </>
        ) : (
          <div className="config-history-list">
            <article className="config-history-item">
              <strong>Revision {String(revision?.version || revisionId).slice(0, 12)} · 当前使用</strong>
              <span>{task.updatedDisplay} · {(revision as { updated_by?: string } | undefined)?.updated_by || "系统"}</span>
              <p>{revision?.source_policy_name || task.referencePlan} {getPlanVersion(task)}</p>
              <p>当前任务实际生效配置快照</p>
              <button type="button">查看差异</button>
            </article>
            <div className="config-history-empty">暂无更多变更记录</div>
          </div>
        )}
      </div>

      <footer className="config-drawer-footer">
        <Button type="button" variant="primary" onClick={onClose}>
          更新审核策略
        </Button>
        <Button type="button" variant="secondary" onClick={onClose}>
          关闭
        </Button>
      </footer>
    </>
  );
}

function ConfigSection({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="config-section">
      <h3>{title}</h3>
      <div className="config-section-content">{children}</div>
    </section>
  );
}

function ConfigKv({ label, value }: { label: string; value: string }) {
  return (
    <div className="config-kv">
      <span>{label}</span>
      <strong title={value}>{value}</strong>
    </div>
  );
}

function buildInitialLogs(task: MonitorTask, outputs: AuditResult[]) {
  const source = task.raw.logs?.length ? task.raw.logs : task.logs;
  const parsed = source.map((log, index) => parseLog(log, index));
  const outputLogs = outputs.slice(0, 4).map((output, index) => ({
    id: `output-${getOutputNumber(output)}-${index}`,
    time: formatTerminalTime(output.analyzed_at || output.updated_at || output.created_at || task.updatedAt),
    level: "INFO" as const,
    scope: "output",
    message: `新增审核产出 ${getOutputNumber(output)}`
  }));
  return [...parsed, ...outputLogs].slice(-80);
}

function parseLog(log: JobLog, index: number): TerminalLog {
  const message = String(log.message || "任务状态更新");
  const level = inferLogLevel(message);
  const scope = inferLogScope(message);
  return {
    id: `${log.time || "log"}-${index}`,
    time: formatTerminalTime(log.time || ""),
    level,
    scope,
    message: stripLogPrefix(message, level, scope),
    stack: level === "ERROR" ? `${message}\n    at crawler.request(search)\n    at retryQueue.flush()` : undefined
  };
}

function buildLiveLog(task: MonitorTask, outputs: AuditResult[], index: number): TerminalLog {
  const templates = [
    ["INFO", "crawler", `最近抓取检查完成，累计 ${task.metrics.crawled} 条内容`],
    ["INFO", "analyzer", `分析队列同步完成，待分析 ${task.metrics.waiting} 条`],
    ["INFO", "output", `当前可进入询证产出 ${outputs.length} 条`],
    ["WARN", "crawler", "接口响应时间超过 20 秒，已降速重试"]
  ] as const;
  const [level, scope, message] = templates[index % templates.length];
  return {
    id: `live-${Date.now()}-${index}`,
    time: formatTerminalTime(new Date().toISOString()),
    level,
    scope,
    message
  };
}

function inferLogLevel(message: string): LogLevel {
  const lower = message.toLowerCase();
  if (lower.includes("error") || message.includes("失败") || message.includes("异常") || message.includes("超时")) {
    return "ERROR";
  }
  if (lower.includes("warn") || message.includes("重试") || message.includes("超过")) {
    return "WARN";
  }
  return "INFO";
}

function inferLogScope(message: string) {
  if (message.includes("采集") || message.toLowerCase().includes("crawler")) {
    return "crawler";
  }
  if (message.includes("分析") || message.toLowerCase().includes("analy")) {
    return "analyzer";
  }
  if (message.includes("产出") || message.toLowerCase().includes("output")) {
    return "output";
  }
  return "system";
}

function stripLogPrefix(message: string, level: LogLevel, scope: string) {
  return message
    .replace(new RegExp(`^${level}\\s+`, "i"), "")
    .replace(new RegExp(`^${scope}\\s+`, "i"), "")
    .trim();
}

function formatTerminalTime(value: string) {
  const date = value ? new Date(value) : new Date();
  if (!Number.isNaN(date.getTime())) {
    return date.toTimeString().slice(0, 8);
  }
  return formatTimeOnly(value);
}

function isCapabilityEnabled(label: string, capabilities: string[]) {
  if (!capabilities.length) {
    return true;
  }
  const text = capabilities.join(" ").toLowerCase();
  const map: Record<string, string[]> = {
    文本语义: ["text", "semantic", "文本"],
    OCR: ["ocr"],
    ASR: ["asr", "audio", "语音"],
    视觉识别: ["vision", "vl", "video", "视觉"],
    评论聚集: ["comment", "评论"]
  };
  return (map[label] || []).some((keyword) => text.includes(keyword.toLowerCase()));
}

function downloadText(filename: string, text: string) {
  const blob = new Blob([text], { type: "text/plain;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  anchor.click();
  URL.revokeObjectURL(url);
}
