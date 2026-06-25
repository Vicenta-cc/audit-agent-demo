import os
from pathlib import Path

from dotenv import load_dotenv


ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / ".env")


class Settings:
    root_dir = ROOT
    data_dir = ROOT / "data"
    outputs_dir = ROOT / "outputs"

    dashscope_api_key = os.getenv("DASHSCOPE_API_KEY", "")
    dashscope_base_url = os.getenv(
        "DASHSCOPE_BASE_URL",
        "https://dashscope.aliyuncs.com/compatible-mode/v1",
    ).rstrip("/")
    qwen_text_model = os.getenv("QWEN_TEXT_MODEL", "qwen3.6-27b")
    qwen_vl_model = os.getenv("QWEN_VL_MODEL", "qwen3.6-27b")
    qwen_use_response_format = os.getenv("QWEN_USE_RESPONSE_FORMAT", "false").lower() == "true"
    qwen_max_tokens = int(os.getenv("QWEN_MAX_TOKENS", "1024"))
    request_timeout = int(os.getenv("REQUEST_TIMEOUT", "180"))
    cors_allow_origins = [
        origin.strip()
        for origin in os.getenv("CORS_ALLOW_ORIGINS", "*").split(",")
        if origin.strip()
    ]

    media_crawler_dir = Path(os.getenv("MEDIACRAWLER_DIR", r"D:\good-agent\MediaCrawler"))
    video_to_txt_dir = Path(os.getenv("VIDEO_TO_TXT_DIR", r"D:\good-agent\video-to-txt"))

    # Demo defaults: keep cost and runtime small.
    max_images_per_note = int(os.getenv("MAX_IMAGES_PER_NOTE", "4"))
    max_video_frames = int(os.getenv("MAX_VIDEO_FRAMES", "6"))
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
    whisper_model = os.getenv("WHISPER_MODEL", "base")
    whisper_device = os.getenv("WHISPER_DEVICE", "cuda")
    whisper_compute_type = os.getenv("WHISPER_COMPUTE_TYPE", "float16")
    whisper_cpu_fallback = os.getenv("WHISPER_CPU_FALLBACK", "true").lower() == "true"


settings = Settings()
settings.data_dir.mkdir(parents=True, exist_ok=True)
settings.outputs_dir.mkdir(parents=True, exist_ok=True)
