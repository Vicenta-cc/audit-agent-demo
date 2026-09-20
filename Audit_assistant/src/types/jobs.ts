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
  stage?: string;
  level?: "info" | "warning" | "error" | string;
  message?: string;
  reason?: string;
  error_code?: string;
  retryable?: boolean | null;
  action?: string;
}

export interface JobAuditConfigRevision {
  id?: string;
  job_id?: string;
  version?: string | number;
  source_policy_id?: string;
  source_policy_name?: string;
  source_policy_version?: string;
  config_hash?: string;
  effective_from?: string;
  created_at?: string;
  created_by?: string;
  prompt_version?: string;
  audit_config?: {
    schema_version?: string;
    source_policy_id?: string;
    source_policy_name?: string;
    source_policy_version?: string;
    library_ids?: string[];
    capabilities?: string[];
    scoring_template?: string;
    thresholds?: Record<string, number>;
    scoring_rules?: Array<Record<string, unknown>>;
    prompt_version?: string;
  };
  knowledge_package_snapshots?: Array<Record<string, unknown>>;
  capabilities?: string[];
  library_ids?: string[];
}

export interface AuditComment {
  comment_id?: string;
  id?: string | number;
  create_time?: number | string;
  created_at?: string;
  time?: string;
  ip_location?: string;
  note_id?: string;
  content?: string;
  text?: string;
  user_id?: string;
  nickname?: string;
  user_name?: string;
  avatar?: string;
  user_url?: string;
  profile_url?: string;
  sec_uid?: string;
  user_unique_id?: string;
  short_user_id?: string;
  like_count?: string | number;
  sub_comment_count?: string | number;
  source_text?: string;
  translation_zh?: string;
  translation_status?: string;
  audit_status?: "completed" | "failed" | string;
  audit_error?: string;
  risk_score?: number;
  risk_level?: string;
  risk_library_id?: string;
  risk_library_label?: string;
  secondary_library_ids?: string[];
  risk_type?: string;
  risk_basis?: string;
  exemption_basis?: string;
  evidence_quote?: string;
  [key: string]: unknown;
}

export interface AuditEvidenceGroupItem {
  id?: string;
  title?: string;
  source?: string;
  rule?: string;
  risk_contribution?: number;
  confidence?: string;
  content?: string;
  context?: string;
  position?: string;
  risk_library_id?: string;
  risk_library_label?: string;
  evidence_type?: string;
  hit_explanation?: string;
  is_supplementary?: boolean;
  text?: string;
  ocr_text?: string;
  ocr_text_zh?: string;
  features?: string[];
  evidence_risk_level?: string;
  supporting_modalities?: string[];
  primary_modality?: string;
  comment_id?: string;
  nickname?: string;
  source_label?: string;
  asset_rel?: string;
  local_path?: string;
  url?: string;
  timestamp?: number | string;
  start?: number | string;
  end?: number | string;
  frame_id?: string;
  frame_ids?: string[];
  frame_number?: number | string;
  frame_asset_rel?: string;
  review_sheet_rel?: string;
  ocr_chunk_id?: string;
  ocr_context?: Array<Record<string, unknown>>;
  asr_chunk_id?: string;
  source_text_dolphin?: string;
  source_text_mms?: string;
  translation_zh?: string;
  asr_consistency?: string;
  risk_score?: number;
  risk_level?: string;
  risk_basis?: string;
  exemption_basis?: string;
  evidence_quote?: string;
  [key: string]: unknown;
}

export interface AuditEvidenceGroup {
  id?: string;
  type?: "text" | "ocr" | "asr" | "comment" | "vision" | string;
  label?: string;
  icon?: string;
  count?: number;
  hit_summary?: Array<{ rule?: string; count?: number }>;
  confidence?: string;
  risk_contribution?: number;
  risk_libraries?: Array<{ id?: string; label?: string }>;
  preview_limit?: number;
  items?: AuditEvidenceGroupItem[];
}

export interface AuditResultDetail {
  report_snapshot?: { report_version_id: string; post_ref: string };
  audit_result: AuditResult;
  audit_config_revision?: JobAuditConfigRevision;
  evidence_groups?: AuditEvidenceGroup[];
  is_historical_config?: boolean;
}

export interface RawJob {
  id: string;
  status: JobStatus;
  platform?: string;
  crawler_account_id?: string;
  crawler_account_display_name?: string;
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
  start_page?: number;
  max_notes?: number;
  max_total_notes?: number;
  max_comments?: number;
  max_concurrency?: number;
  max_items_per_minute?: number;
  get_sub_comment?: boolean;
  auto_analyze?: boolean;
  analyze_limit?: number;
  requested_config?: Record<string, unknown>;
  effective_config?: Record<string, unknown>;
  analysis_batch_size?: number;
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
    end_task?: boolean;
    ending?: boolean;
    ended?: boolean;
    pause_crawl?: boolean;
    resume_crawl?: boolean;
    pause_analysis?: boolean;
    resume_analysis?: boolean;
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
  report_snapshot_source?: boolean;
  published_at?: string;
  id?: number;
  audit_result_id?: number;
  job_id?: string;
  content_id?: number;
  content_key?: string;
  note_id?: string;
  url?: string;
  title?: string;
  title_zh?: string;
  content_title?: string;
  desc?: string;
  desc_zh?: string;
  summary?: string;
  cover_url?: string;
  video_cover_url?: string;
  thumbnail_url?: string;
  thumbnail_asset?: AuditMediaAsset;
  duration_seconds?: number;
  decision?: string;
  risk_level?: string;
  risk_score?: number;
  primary_risk?: string;
  categories?: string[];
  prompt_category?: string;
  prompt_version?: string;
  risk_basis?: string;
  has_risk?: boolean;
  rule_matches?: Array<Record<string, unknown>>;
  score_breakdown?: Array<Record<string, unknown>>;
  comments?: AuditComment[];
  evidence_count?: number;
  risk_image_count?: number;
  risk_frame_count?: number;
  comment_count?: number;
  comments_count?: number;
  evidence?: unknown[];
  risk_evidence?: unknown[];
  evidence_groups?: AuditEvidenceGroup[];
  evidence_items?: Array<{
    evidence_id?: string;
    id?: string;
    primary_modality?: string;
    modality?: string;
    source?: string;
    source_label?: string;
    risk_library_id?: string;
    risk_library_label?: string;
    secondary_library_ids?: string[];
    risk_type?: string;
    text?: string;
    translation_zh?: string;
    ocr_text?: string;
    ocr_text_zh?: string;
    visual_elements?: string[];
    features?: string[];
    evidence_risk_level?: string;
    reason?: string;
    confidence?: string;
    supporting_modalities?: string[];
    asset_rel?: string;
  }>;
  risk_images?: unknown[];
  risk_frames?: unknown[];
  ocr_items?: unknown[];
  asr_segments?: unknown[];
  timeline_frames?: Array<{ ocr_text?: string; ocr_text_zh?: string }>;
  image_analyses?: Array<{
    ocr_text?: string;
    visual_summary?: string;
    asset_rel?: string;
    local_path?: string;
    original_path?: string;
    url?: string;
  }>;
  video_results?: Array<{
    transcript?: {
      segments?: unknown[];
      text?: string;
      text_zh?: string;
      translation?: { text?: string };
    };
    timeline_frames?: AuditMediaAsset[];
    review_sheets?: AuditMediaAsset[];
    segment_reviews?: AuditMediaAsset[];
    moments?: unknown[];
    moment_sheets?: unknown[];
    asset_rel?: string;
    local_path?: string;
    url?: string;
    duration?: number;
  }>;
  evidence_index?: {
    text_context?: {
      title?: string;
      title_zh?: string;
      desc?: string;
      desc_zh?: string;
      comments_count?: number;
    };
    image_units?: Array<{
      evidence_id?: string;
      index?: number;
      asset_rel?: string;
      local_path?: string;
      url?: string;
      ocr_text?: string;
      ocr_text_zh?: string;
      visual_summary?: string;
      benign_context?: string;
    }>;
    video_units?: Array<{
      evidence_id?: string;
      source?: string;
      timeline_frame_count?: number;
      review_sheet_count?: number;
      segment_review_count?: number;
      moment_count?: number;
      precise_sheet_count?: number;
      transcript_summary?: string;
      asset_rel?: string;
      local_path?: string;
      path?: string;
      url?: string;
      video_url?: string;
      video_play_url?: string;
      video_download_url?: string;
      duration?: number;
      segment_reviews?: AuditMediaAsset[];
    }>;
    timeline_frames?: AuditMediaAsset[];
    review_sheets?: AuditMediaAsset[];
    segment_reviews?: AuditMediaAsset[];
    ocr_chunks?: Array<Record<string, unknown>>;
    asr_chunks?: Array<Record<string, unknown>>;
    comment_units?: Array<Record<string, unknown>>;
    moment_sheets?: Array<Record<string, unknown>>;
    moments?: Array<Record<string, unknown>>;
    precise_sheets?: Array<Record<string, unknown>>;
    audio_units?: unknown[];
    ocr_items?: Array<Record<string, unknown>>;
    asr_segments?: Array<Record<string, unknown>>;
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
    platform?: string;
    nickname?: string;
    user_id?: string;
    user_unique_id?: string;
    short_user_id?: string;
    sec_uid?: string;
    avatar?: string;
  };
}

export interface AuditMediaAsset {
  asset_rel?: string;
  local_path?: string;
  original_path?: string;
  path?: string;
  url?: string;
  [key: string]: unknown;
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
