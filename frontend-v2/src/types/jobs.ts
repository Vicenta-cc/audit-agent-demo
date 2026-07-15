export type JobStatus =
  | "queued"
  | "running"
  | "completed"
  | "failed"
  | "stopped"
  | "interrupted"
  | "analysis_running"
  | "analysis_stopped"
  | "analysis_paused"
  | "analysis_stopping"
  | "crawl_paused"
  | "crawl_pausing"
  | "stopping"
  | string;

export type TaskStatusFilter = "全部" | "运行中" | "已暂停" | "已完成" | "失败";
export type TaskSourceFilter = "全部" | "平台抓取" | "直播接入" | "重点用户" | "本地视频";
export type TaskSortKey = "default" | "updated" | "waiting" | "high" | "outputs";
export type MonitorTaskSource = Exclude<TaskSourceFilter, "全部">;

export interface JobTaskStats {
  ingested_count?: number;
  queued_analysis_count?: number;
  pending_analysis_count?: number;
  analyzing_count?: number;
  completed_analysis_count?: number;
  failed_analysis_count?: number;
  batch_count?: number;
  batch_item_count?: number;
  batch_processed_count?: number;
  analysis_status_counts?: Record<string, number>;
}

export interface JobLog {
  id?: number;
  time?: string;
  message?: string;
}

export interface JobAuditConfigRevision {
  id?: string;
  version?: string | number;
  source_policy_id?: string;
  source_policy_name?: string;
  source_policy_version?: string;
  effective_from?: string;
  prompt_version?: string;
  audit_config?: {
    library_ids?: string[];
    capabilities?: string[];
  };
}

export interface RawJob {
  id: string;
  status: JobStatus;
  platform?: string;
  display_name?: string;
  crawl_mode?: string;
  keyword?: string;
  keyword_source?: string;
  lexicon_category?: string;
  library_ids?: string[];
  capabilities?: string[];
  lexicon_keywords?: string[];
  creator_url?: string;
  creator_id?: string;
  creator_nickname?: string;
  run_crawler?: boolean;
  input_type?: string;
  input_filename?: string;
  source_output_id?: string;
  created_at?: string;
  updated_at?: string;
  logs?: JobLog[];
  task_stats?: JobTaskStats;
  crawl_status?: string;
  analysis_status?: string;
  available_actions?: {
    pause_crawl?: boolean;
    stop_analysis?: boolean;
    backfill_analysis?: boolean;
    delete_job?: boolean;
  };
  current_audit_config_revision?: JobAuditConfigRevision;
  current_audit_config_revision_id?: string;
  prompt_profile_snapshot?: {
    prompt_version?: string;
  };
  error?: string;
}

export interface AuditResult {
  id?: number;
  audit_result_id?: number;
  job_id?: string;
  content_id?: number;
  content_key?: string;
  note_id?: string;
  url?: string;
  title?: string;
  content_title?: string;
  desc?: string;
  summary?: string;
  decision?: string;
  risk_level?: string;
  risk_score?: number;
  primary_risk?: string;
  categories?: string[];
  evidence_count?: number;
  risk_image_count?: number;
  risk_frame_count?: number;
  comment_count?: number;
  comments_count?: number;
  evidence?: unknown[];
  risk_evidence?: unknown[];
  risk_images?: unknown[];
  risk_frames?: unknown[];
  ocr_items?: unknown[];
  asr_segments?: unknown[];
  timeline_frames?: Array<{ ocr_text?: string; ocr_text_zh?: string }>;
  image_analyses?: Array<{ ocr_text?: string; visual_summary?: string }>;
  video_results?: Array<{
    transcript?: { segments?: unknown[]; text?: string };
    timeline_frames?: unknown[];
    moments?: unknown[];
    moment_sheets?: unknown[];
  }>;
  evidence_index?: {
    text_context?: { title?: string; desc?: string; comments_count?: number };
    image_units?: unknown[];
    video_units?: Array<{
      timeline_frame_count?: number;
      moment_count?: number;
      precise_sheet_count?: number;
      transcript_summary?: string;
    }>;
    audio_units?: unknown[];
    ocr_items?: unknown[];
    asr_segments?: unknown[];
  };
  platform?: string;
  author_key?: string;
  analyzed_at?: string;
  updated_at?: string;
  created_at?: string;
  review_status?: string;
  review_note?: string;
  reviewed_at?: string;
  audit_config_revision_id?: string;
  author?: {
    nickname?: string;
    user_id?: string;
    user_unique_id?: string;
    short_user_id?: string;
    sec_uid?: string;
  };
}

export interface TaskRunMetrics {
  crawled: number;
  analyzed: number;
  waiting: number;
  failed: number;
  outputs: number;
  highRisk: number;
}

export interface MonitorTask {
  id: string;
  name: string;
  source: MonitorTaskSource;
  sourceLabel: string;
  platformLabel: string;
  objectLabel: string;
  referencePlan: string;
  status: TaskStatusFilter | "排队中" | "已停止" | "已中断" | "停止中";
  statusTone: "success" | "warning" | "danger" | "info" | "neutral";
  statusTimeLabel: string;
  createdAt: string;
  updatedAt: string;
  updatedDisplay: string;
  metrics: TaskRunMetrics;
  logs: JobLog[];
  recentOutputs: AuditResult[];
  raw: RawJob;
}

export interface TaskStatsSummary {
  runningTasks: number;
  recentRiskCount: number;
  liveTaskCount: number;
  focusUserTaskCount: number;
  platformCrawlTaskCount: number;
}

export interface JobsSnapshot {
  jobs: RawJob[];
  auditResults: AuditResult[];
  tasks: MonitorTask[];
  stats: TaskStatsSummary;
}

export interface DeleteJobResponse {
  ok: boolean;
  id: string;
  deleted_result_count: number;
}
