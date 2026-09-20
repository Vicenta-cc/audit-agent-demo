import os
from pathlib import Path

from dotenv import load_dotenv


ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / ".env")


def environment_flag(name: str, *, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true"}


def parse_cors_allow_origins(value: str, *, auth_mode: str) -> list[str]:
    origins = [origin.strip() for origin in str(value).split(",") if origin.strip()]
    if auth_mode == "required" and "*" in origins:
        raise ValueError(
            "CORS_ALLOW_ORIGINS cannot contain * when APP_AUTH_MODE=required"
        )
    return origins


class Settings:
    root_dir = ROOT
    data_dir = Path(os.getenv("XHS_AUDIT_DATA_DIR", str(ROOT / "data"))).expanduser()
    outputs_dir = Path(
        os.getenv("XHS_AUDIT_OUTPUTS_DIR", str(ROOT / "outputs"))
    ).expanduser()
    app_auth_mode = os.getenv("APP_AUTH_MODE", "required").strip().lower()
    if app_auth_mode not in {"required", "disabled"}:
        raise ValueError("APP_AUTH_MODE must be required or disabled")
    _app_auth_db_value = os.getenv("APP_AUTH_DB", "").strip()
    app_auth_db = Path(
        _app_auth_db_value or str(data_dir / "investigation_creation.sqlite3")
    ).expanduser()
    app_account_validity_days = max(
        1, int(os.getenv("APP_ACCOUNT_VALIDITY_DAYS", "7"))
    )
    app_account_activation_mode = os.getenv(
        "APP_ACCOUNT_ACTIVATION_MODE", "first_login"
    ).strip().lower()
    app_auth_cookie_name = (
        os.getenv("APP_AUTH_COOKIE_NAME", "xhs_audit_session").strip()
        or "xhs_audit_session"
    )
    app_auth_cookie_secure = environment_flag(
        "APP_AUTH_COOKIE_SECURE", default=True
    )
    app_auth_cookie_samesite = os.getenv(
        "APP_AUTH_COOKIE_SAMESITE", "lax"
    ).strip().lower()
    if app_auth_cookie_samesite not in {"lax", "strict", "none"}:
        raise ValueError("APP_AUTH_COOKIE_SAMESITE must be lax, strict, or none")

    dashscope_api_key = os.getenv("DASHSCOPE_API_KEY", "")
    dashscope_base_url = os.getenv(
        "DASHSCOPE_BASE_URL",
        "https://dashscope.aliyuncs.com/compatible-mode/v1",
    ).rstrip("/")
    qwen_text_model = os.getenv("QWEN_TEXT_MODEL", "qwen3.7-plus")
    hermes_authorized_report_version_ids = tuple(
        item.strip()
        for item in os.getenv("HERMES_AUTHORIZED_REPORT_VERSION_IDS", "").split(",")
        if item.strip()
    )
    _historical_report_a_db_value = os.getenv("HISTORICAL_REPORT_A_DB", "").strip()
    historical_report_a_db = (
        Path(_historical_report_a_db_value).expanduser()
        if _historical_report_a_db_value
        else None
    )
    _historical_report_b_db_value = os.getenv("HISTORICAL_REPORT_B_DB", "").strip()
    historical_report_b_db = (
        Path(_historical_report_b_db_value).expanduser()
        if _historical_report_b_db_value
        else None
    )
    hermes_investigation_max_workers = max(
        2,
        int(os.getenv("HERMES_INVESTIGATION_MAX_WORKERS", "2")),
    )
    hermes_turn_timeout_seconds = max(
        1.0, float(os.getenv("HERMES_TURN_TIMEOUT_SECONDS", "600"))
    )
    hermes_creation_fake_runtime = (
        os.getenv("HERMES_CREATION_FAKE_RUNTIME", "false").lower() == "true"
        or os.getenv("HERMES_CREATION_FAKE_RUNTIME", "") == "1"
    )
    hermes_creation_fake_stage_seconds = max(
        0.05,
        float(os.getenv("HERMES_CREATION_FAKE_STAGE_SECONDS", "1.2")),
    )
    # Investigation streaming is introduced behind independent, default-off
    # switches so the accepted synchronous behavior remains the rollback path.
    activity_stream_enabled = environment_flag(
        "INVESTIGATION_ACTIVITY_STREAM_ENABLED"
    )
    answer_stream_enabled = environment_flag("INVESTIGATION_ANSWER_STREAM_ENABLED")
    creation_answer_stream_enabled = environment_flag(
        "INVESTIGATION_CREATION_ANSWER_STREAM_ENABLED"
    )
    activity_recovery_turn_limit = max(
        1,
        min(
            100,
            int(os.getenv("INVESTIGATION_ACTIVITY_RECOVERY_TURN_LIMIT", "20")),
        ),
    )
    stream_replay_batch_size = max(
        1,
        min(
            1_000,
            int(os.getenv("INVESTIGATION_STREAM_REPLAY_BATCH_SIZE", "256")),
        ),
    )
    qwen_report_model = os.getenv("QWEN_REPORT_MODEL", qwen_text_model).strip()
    report_prompt_version = os.getenv("REPORT_PROMPT_VERSION", "report-v2-human").strip()
    report_request_timeout = int(os.getenv("REPORT_REQUEST_TIMEOUT", "180"))
    report_max_tokens = int(os.getenv("REPORT_MAX_TOKENS", "3000"))
    qwen_vl_model = os.getenv("QWEN_VL_MODEL", "qwen3.7-plus")
    qwen_contact_sheet_model = os.getenv("QWEN_CONTACT_SHEET_MODEL", "qwen3.6-flash").strip()
    qwen_image_audit_model = os.getenv("QWEN_IMAGE_AUDIT_MODEL", qwen_contact_sheet_model).strip()
    asr_translate_model = os.getenv("ASR_TRANSLATE_MODEL", qwen_text_model).strip()
    asr_translate_max_tokens = int(os.getenv("ASR_TRANSLATE_MAX_TOKENS", "6000"))
    asr_translate_enable_thinking = (
        os.getenv("ASR_TRANSLATE_ENABLE_THINKING", "false").lower() == "true"
    )
    qwen_use_response_format = os.getenv("QWEN_USE_RESPONSE_FORMAT", "false").lower() == "true"
    qwen_max_tokens = int(os.getenv("QWEN_MAX_TOKENS", "1024"))
    fusion_max_tokens = int(os.getenv("FUSION_MAX_TOKENS", "3000"))
    fusion_request_timeout = int(os.getenv("FUSION_REQUEST_TIMEOUT", "90"))
    fusion_timeout_retries = int(os.getenv("FUSION_TIMEOUT_RETRIES", "1"))
    request_timeout = int(os.getenv("REQUEST_TIMEOUT", "180"))
    remote_asr_request_retries = max(
        0,
        int(os.getenv("REMOTE_ASR_REQUEST_RETRIES", "3")),
    )
    remote_asr_retry_backoff_seconds = max(
        0.0,
        float(os.getenv("REMOTE_ASR_RETRY_BACKOFF_SECONDS", "2")),
    )
    remote_inference_base_url = os.getenv("REMOTE_INFERENCE_BASE_URL", "").rstrip("/")
    remote_asr_base_url = os.getenv("REMOTE_ASR_BASE_URL", remote_inference_base_url).rstrip("/")
    remote_mms_asr_base_url = os.getenv("REMOTE_MMS_ASR_BASE_URL", "").rstrip("/")
    remote_translation_base_url = os.getenv("REMOTE_TRANSLATION_BASE_URL", remote_inference_base_url).rstrip("/")
    remote_ocr_base_url = os.getenv("REMOTE_OCR_BASE_URL", remote_inference_base_url).rstrip("/")
    remote_inference_api_key = os.getenv("REMOTE_INFERENCE_API_KEY", "")
    inference_api_key = os.getenv("INFERENCE_API_KEY", remote_inference_api_key)
    use_remote_asr = os.getenv("USE_REMOTE_ASR", os.getenv("USE_REMOTE_WHISPER", "false")).lower() == "true"
    use_remote_whisper = use_remote_asr
    use_remote_mms_asr = os.getenv("USE_REMOTE_MMS_ASR", "false").lower() == "true"
    use_remote_vlm = os.getenv("USE_REMOTE_VLM", "false").lower() == "true"
    use_remote_llm = os.getenv("USE_REMOTE_LLM", "false").lower() == "true"
    use_remote_translation = os.getenv("USE_REMOTE_TRANSLATION", "false").lower() == "true"
    use_remote_ocr = os.getenv("USE_REMOTE_OCR", "false").lower() == "true"
    ocr_enabled = os.getenv("OCR_ENABLED", "false").lower() == "true"
    ocr_engine = os.getenv("OCR_ENGINE", "vlm_ocr_translate").strip()
    ocr_language_hint = os.getenv("OCR_LANGUAGE_HINT", "ug").strip()
    ocr_concurrency = int(os.getenv("OCR_CONCURRENCY", "5"))
    ocr_sample_fps = float(os.getenv("OCR_SAMPLE_FPS", "1.0"))
    ocr_hash_threshold = int(os.getenv("OCR_HASH_THRESHOLD", "12"))
    ocr_max_samples = int(os.getenv("OCR_MAX_SAMPLES", "120"))
    ocr_translate = os.getenv("OCR_TRANSLATE", "true").lower() == "true"
    ocr_vl_image_max_side = int(os.getenv("OCR_VL_IMAGE_MAX_SIDE", "1536"))
    ocr_vl_image_quality = int(os.getenv("OCR_VL_IMAGE_QUALITY", "90"))
    ocr_vl_max_tokens = int(os.getenv("OCR_VL_MAX_TOKENS", "1500"))
    ocr_paddle_device = os.getenv("OCR_PADDLE_DEVICE", "gpu:0").strip()
    ocr_paddle_pipeline_version = os.getenv("OCR_PADDLE_PIPELINE_VERSION", "v1.6").strip()
    ocr_paddle_prompt_label = os.getenv("OCR_PADDLE_PROMPT_LABEL", "ocr").strip()
    ocr_paddle_prompt_preset = os.getenv("OCR_PADDLE_PROMPT_PRESET", "uyghur-strong-en").strip()
    ocr_paddle_prompt_compose = os.getenv("OCR_PADDLE_PROMPT_COMPOSE", "replace").strip()
    ocr_paddle_max_new_tokens = int(os.getenv("OCR_PADDLE_MAX_NEW_TOKENS", "1024"))
    ocr_paddle_temperature = float(os.getenv("OCR_PADDLE_TEMPERATURE", "0"))
    ocr_paddle_repetition_penalty = float(os.getenv("OCR_PADDLE_REPETITION_PENALTY", "1.05"))
    paddleocr_api_job_url = os.getenv(
        "PADDLEOCR_API_JOB_URL",
        "https://paddleocr.aistudio-app.com/api/v2/ocr/jobs",
    ).rstrip("/")
    paddleocr_api_token = os.getenv("PADDLEOCR_API_TOKEN", "")
    paddleocr_api_model = os.getenv("PADDLEOCR_API_MODEL", "PaddleOCR-VL-1.6").strip()
    paddleocr_api_poll_seconds = float(os.getenv("PADDLEOCR_API_POLL_SECONDS", "5"))
    paddleocr_api_timeout_seconds = float(os.getenv("PADDLEOCR_API_TIMEOUT_SECONDS", "300"))
    paddleocr_api_request_retries = int(os.getenv("PADDLEOCR_API_REQUEST_RETRIES", "3"))
    paddleocr_api_retry_backoff_seconds = float(os.getenv("PADDLEOCR_API_RETRY_BACKOFF_SECONDS", "1"))
    ocr_prompt_max_chars = int(os.getenv("OCR_PROMPT_MAX_CHARS", "4000"))
    fusion_max_comments = int(os.getenv("FUSION_MAX_COMMENTS", "50"))
    fusion_comment_max_chars = int(os.getenv("FUSION_COMMENT_MAX_CHARS", "240"))
    fusion_prompt_max_chars = int(os.getenv("FUSION_PROMPT_MAX_CHARS", "24000"))
    fusion_max_ocr_states = int(os.getenv("FUSION_MAX_OCR_STATES", "24"))
    fusion_ocr_text_max_chars = int(os.getenv("FUSION_OCR_TEXT_MAX_CHARS", "180"))
    fusion_frame_summary_max_chars = int(os.getenv("FUSION_FRAME_SUMMARY_MAX_CHARS", "180"))
    fusion_frame_max_risk_items = int(os.getenv("FUSION_FRAME_MAX_RISK_ITEMS", "3"))
    fusion_frame_max_ocr_items = int(os.getenv("FUSION_FRAME_MAX_OCR_ITEMS", "2"))
    fusion_frame_evidence_max_chars = int(os.getenv("FUSION_FRAME_EVIDENCE_MAX_CHARS", "8000"))
    auto_analyze_crawled_content = os.getenv("AUTO_ANALYZE_CRAWLED_CONTENT", "true").lower() == "true"
    analysis_media_scope = os.getenv("ANALYSIS_MEDIA_SCOPE", "all").strip().lower()
    stream_crawl_analysis = os.getenv("STREAM_CRAWL_ANALYSIS", "true").lower() == "true"
    # Separate deployments retain the one-post demo unless explicitly configured.
    m3_posts_per_keyword = min(
        5,
        max(1, int(os.getenv("M3_POSTS_PER_KEYWORD", "1"))),
    )
    m3_analyze_limit = max(1, int(os.getenv("M3_ANALYZE_LIMIT", "1")))
    m3_comments_per_post = min(
        1000,
        max(0, int(os.getenv("M3_COMMENTS_PER_POST", "300"))),
    )
    crawler_max_concurrency = int(os.getenv("CRAWLER_MAX_CONCURRENCY", "3"))
    crawler_sleep_seconds = float(os.getenv("CRAWLER_SLEEP_SECONDS", "6"))
    crawler_login_timeout_seconds = max(
        60,
        int(os.getenv("CRAWLER_LOGIN_TIMEOUT_SECONDS", "180")),
    )
    crawler_login_headed = os.getenv("CRAWLER_LOGIN_HEADED", "false").lower() == "true"
    crawler_login_interactive = os.getenv("CRAWLER_LOGIN_INTERACTIVE", "false").lower() == "true"
    crawler_login_interactive_timeout_seconds = max(180, min(1800, int(os.getenv("CRAWLER_LOGIN_INTERACTIVE_TIMEOUT_SECONDS", "600"))))
    crawler_login_browser_version = os.getenv("CRAWLER_LOGIN_BROWSER_VERSION", "").strip()
    crawler_auth_encryption_key = os.getenv("CRAWLER_AUTH_ENCRYPTION_KEY", "").strip()
    crawler_auth_key_file = Path(
        os.getenv("CRAWLER_AUTH_KEY_FILE", str(data_dir / "crawler_auth.key"))
    ).expanduser()
    batch_ingestion_enabled = os.getenv("BATCH_INGESTION_ENABLED", "true").lower() == "true"
    batch_size = int(os.getenv("BATCH_SIZE", "20"))
    batch_flush_seconds = float(os.getenv("BATCH_FLUSH_SECONDS", "30"))
    _cors_allow_origins_value = os.getenv(
        "CORS_ALLOW_ORIGINS",
        "" if app_auth_mode == "required" else "*",
    )
    cors_allow_origins = parse_cors_allow_origins(
        _cors_allow_origins_value,
        auth_mode=app_auth_mode,
    )

    # Keep the dependency relocatable. Production deployments must set
    # MEDIACRAWLER_DIR; the repository-local default is only a safe placeholder.
    media_crawler_dir = Path(
        os.getenv("MEDIACRAWLER_DIR", str(ROOT / "external" / "MediaCrawler"))
    ).expanduser()
    task_resource_lock_dir = Path(os.getenv("TASK_RESOURCE_LOCK_DIR", str(data_dir / "resource-locks"))).expanduser().resolve()
    task_execution_capacity = max(1, int(os.getenv("TASK_EXECUTION_CAPACITY", "2")))
    task_analysis_capacity = max(1, int(os.getenv("TASK_ANALYSIS_CAPACITY", "2")))
    task_worker_processes = max(1, int(os.getenv("TASK_WORKER_PROCESSES", "2")))
    request_scheduler_db = Path(os.getenv("REQUEST_SCHEDULER_DB", str(data_dir / "request_scheduler.sqlite3"))).expanduser().resolve()
    crawler_content_pacing_enabled = os.getenv("CRAWLER_CONTENT_PACING_ENABLED", "true").lower() != "false"
    request_min_interval = float(os.getenv("REQUEST_MIN_INTERVAL", "2"))
    requests_per_minute = int(os.getenv("REQUESTS_PER_MINUTE", "30"))
    request_concurrency = int(os.getenv("REQUEST_CONCURRENCY", "1"))
    media_request_interval = float(os.getenv("MEDIA_REQUEST_INTERVAL", "5"))
    request_cooldown_seconds = float(os.getenv("REQUEST_COOLDOWN_SECONDS", "300"))
    # Login and crawling must resolve exactly the same persistent account root.
    crawler_browser_profile_root = Path(os.getenv(
        "CRAWLER_BROWSER_PROFILE_ROOT", str(data_dir / "crawler_browser_profiles")
    )).expanduser().resolve()
    _media_crawler_python_default = (
        media_crawler_dir / ".venv" / "Scripts" / "python.exe"
        if os.name == "nt"
        else media_crawler_dir / ".venv" / "bin" / "python"
    )
    _crawler_login_python_value = os.getenv("CRAWLER_LOGIN_PYTHON", "").strip()
    crawler_login_python = Path(
        _crawler_login_python_value or _media_crawler_python_default
    ).expanduser()
    video_to_txt_dir = Path(
        os.getenv("VIDEO_TO_TXT_DIR", str(ROOT / "external" / "video-to-txt"))
    ).expanduser()

    # Demo defaults: keep cost and runtime small.
    max_images_per_note = int(os.getenv("MAX_IMAGES_PER_NOTE", "4"))
    max_video_frames = int(os.getenv("MAX_VIDEO_FRAMES", "36"))
    video_review_max_frames = int(os.getenv("VIDEO_REVIEW_MAX_FRAMES", "32"))
    video_ocr_max_frames = int(os.getenv("VIDEO_OCR_MAX_FRAMES", "16"))
    video_review_sheet_frames = int(os.getenv("VIDEO_REVIEW_SHEET_FRAMES", "16"))
    video_review_concurrency = int(os.getenv("VIDEO_REVIEW_CONCURRENCY", "2"))
    video_review_asr_overlap_seconds = float(os.getenv("VIDEO_REVIEW_ASR_OVERLAP_SECONDS", "4"))
    video_review_asr_chunks = int(os.getenv("VIDEO_REVIEW_ASR_CHUNKS", "4"))
    video_scene_threshold = float(os.getenv("VIDEO_SCENE_THRESHOLD", "0.30"))
    video_fps_floor_seconds = float(os.getenv("VIDEO_FPS_FLOOR_SECONDS", os.getenv("VIDEO_FPS_FLOOR", "1.0")))
    video_dedup_threshold = float(os.getenv("VIDEO_DEDUP_THRESHOLD", "8"))
    video_dedup_window = int(os.getenv("VIDEO_DEDUP_WINDOW", "4"))
    video_moment_sheet_frames = int(os.getenv("VIDEO_MOMENT_SHEET_FRAMES", "9"))
    video_moment_concurrency = int(os.getenv("VIDEO_MOMENT_CONCURRENCY", "1"))
    precise_window_seconds = float(os.getenv("PRECISE_WINDOW_SECONDS", "2.0"))
    precise_sheet_frames = int(os.getenv("PRECISE_SHEET_FRAMES", "9"))
    max_precise_sheets_per_moment = int(os.getenv("MAX_PRECISE_SHEETS_PER_MOMENT", "2"))
    max_precise_sheets_per_video = int(os.getenv("MAX_PRECISE_SHEETS_PER_VIDEO", "6"))
    # Flash-spike detection scans adjacent frames for short insertions.
    video_spike_threshold = float(os.getenv("VIDEO_SPIKE_THRESHOLD", "25"))
    video_spike_max_len = int(os.getenv("VIDEO_SPIKE_MAX_LEN", "3"))
    video_spike_max_frames = int(os.getenv("VIDEO_SPIKE_MAX_FRAMES", "8"))
    video_short_segment_max_seconds = float(os.getenv("VIDEO_SHORT_SEGMENT_MAX_SECONDS", "1.5"))
    video_short_segment_max_frames = int(os.getenv("VIDEO_SHORT_SEGMENT_MAX_FRAMES", "0"))
    video_diff_pair_min_seconds = float(os.getenv("VIDEO_DIFF_PAIR_MIN_SECONDS", "0.1"))
    video_diff_peak_top_k = int(os.getenv("VIDEO_DIFF_PEAK_TOP_K", "0"))
    pyscenedetect_enabled = os.getenv("PYSCENEDETECT_ENABLED", "true").lower() == "true"
    pyscenedetect_threshold = float(os.getenv("PYSCENEDETECT_THRESHOLD", "15"))
    pyscenedetect_min_scene_len = int(os.getenv("PYSCENEDETECT_MIN_SCENE_LEN", "1"))
    transcript_max_chars = int(os.getenv("TRANSCRIPT_MAX_CHARS", "8000"))
    vl_image_max_side = int(os.getenv("VL_IMAGE_MAX_SIDE", "1024"))
    vl_image_quality = int(os.getenv("VL_IMAGE_QUALITY", "75"))
    asr_engine = os.getenv("ASR_ENGINE", "whisper").strip().lower()
    asr_language = os.getenv("ASR_LANGUAGE", "zh").strip()
    asr_device = os.getenv("ASR_DEVICE", os.getenv("WHISPER_DEVICE", "cuda"))
    asr_compute_type = os.getenv("ASR_COMPUTE_TYPE", os.getenv("WHISPER_COMPUTE_TYPE", "float16"))
    whisper_model = os.getenv("WHISPER_MODEL", "base")
    whisper_device = asr_device
    whisper_compute_type = asr_compute_type
    whisper_cpu_fallback = os.getenv("WHISPER_CPU_FALLBACK", "true").lower() == "true"
    dolphin_model = os.getenv("DOLPHIN_MODEL", "small")
    dolphin_model_dir = os.getenv("DOLPHIN_MODEL_DIR", "")
    dolphin_lang_sym = os.getenv("DOLPHIN_LANG_SYM", asr_language or "auto")
    dolphin_region_sym = os.getenv("DOLPHIN_REGION_SYM", "CN")
    dolphin_audio_loader = os.getenv("DOLPHIN_AUDIO_LOADER", "soundfile")
    dolphin_word_timestamp = os.getenv("DOLPHIN_WORD_TIMESTAMP", "true").lower() == "true"
    dolphin_predict_time = os.getenv(
        "DOLPHIN_PREDICT_TIME",
        "true" if dolphin_word_timestamp else "false",
    ).lower() == "true"
    mms_model = os.getenv("MMS_MODEL", "facebook/mms-1b-all")
    mms_target_lang = os.getenv("MMS_TARGET_LANG", "uig-script_arabic")
    mms_device = os.getenv("MMS_DEVICE", asr_device)
    mms_dtype = os.getenv("MMS_DTYPE", "float16")
    mms_chunk_length_s = float(os.getenv("MMS_CHUNK_LENGTH_S", "20"))
    mms_stride_length_s = float(os.getenv("MMS_STRIDE_LENGTH_S", "2"))
    mms_batch_size = int(os.getenv("MMS_BATCH_SIZE", "4"))
    mms_return_timestamps = os.getenv("MMS_RETURN_TIMESTAMPS", "false").lower() == "true"
    mms_hf_endpoint = os.getenv("MMS_HF_ENDPOINT", "")
    mms_local_files_only = os.getenv("MMS_LOCAL_FILES_ONLY", "false").lower() == "true"
    asr_translate_engine = os.getenv("ASR_TRANSLATE_ENGINE", "qwen_text").strip().lower()
    asr_segment_max_gap_seconds = float(os.getenv("ASR_SEGMENT_MAX_GAP_SECONDS", "0.8"))
    asr_segment_max_seconds = float(os.getenv("ASR_SEGMENT_MAX_SECONDS", "8"))
    asr_segment_max_tokens = int(os.getenv("ASR_SEGMENT_MAX_TOKENS", "18"))
    hymt_model = os.getenv("HYMT_MODEL", "/mnt/workspace/models/HY-MT1.5-1.8B")
    hymt_dtype = os.getenv("HYMT_DTYPE", "bfloat16")
    hymt_device_map = os.getenv("HYMT_DEVICE_MAP", "auto")
    hymt_target_language = os.getenv("HYMT_TARGET_LANGUAGE", "中文")
    hymt_max_new_tokens = int(os.getenv("HYMT_MAX_NEW_TOKENS", "256"))
    hymt_temperature = float(os.getenv("HYMT_TEMPERATURE", "0"))
    hymt_top_p = float(os.getenv("HYMT_TOP_P", "0.6"))
    hymt_top_k = int(os.getenv("HYMT_TOP_K", "20"))
    hymt_repetition_penalty = float(os.getenv("HYMT_REPETITION_PENALTY", "1.15"))
    translation_enabled = os.getenv("TRANSLATION_ENABLED", "true").lower() == "true"
    translation_arabic_ratio_threshold = float(os.getenv("TRANSLATION_ARABIC_RATIO_THRESHOLD", "0.25"))
    comment_audit_batch_size = int(os.getenv("COMMENT_AUDIT_BATCH_SIZE", "20"))
    comment_audit_concurrency = int(os.getenv("COMMENT_AUDIT_CONCURRENCY", "4"))
    comment_audit_max_tokens = int(os.getenv("COMMENT_AUDIT_MAX_TOKENS", "6000"))
    comment_audit_prompt_max_chars = int(
        os.getenv("COMMENT_AUDIT_PROMPT_MAX_CHARS", "16000")
    )
    frame_audit_prompt_max_chars = int(
        os.getenv("FRAME_AUDIT_PROMPT_MAX_CHARS", "16000")
    )
    comment_fusion_top_k = int(os.getenv("COMMENT_FUSION_TOP_K", "20"))
    ffmpeg_path = os.getenv("FFMPEG_PATH", "").strip()


settings = Settings()
settings.data_dir.mkdir(parents=True, exist_ok=True)
settings.outputs_dir.mkdir(parents=True, exist_ok=True)
