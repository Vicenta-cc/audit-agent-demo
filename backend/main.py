from pathlib import Path
from typing import Optional

from fastapi import BackgroundTasks, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .audit_agent.config import settings
from .audit_agent.crawler_adapter import MediaCrawlerAdapter
from .audit_agent.job_store import job_store
from .audit_agent.pipeline import AuditPipeline


ROOT = Path(__file__).resolve().parents[1]
FRONTEND_DIR = ROOT / "frontend"

app = FastAPI(title="XHS Audit Agent Demo")
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_allow_origins,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")


class CrawlRequest(BaseModel):
    platform: str = "xhs"
    keyword: str = "宗教"
    start_page: int = 1
    max_notes: int = 20
    max_comments: int = 20
    max_concurrency: int = 1
    get_sub_comment: bool = False
    analyze_limit: int = 10
    run_crawler: bool = True
    source_output_id: Optional[str] = None
    analysis_batch_size: int = 5


@app.get("/")
def index():
    return FileResponse(FRONTEND_DIR / "index.html")


@app.get("/api/config")
def get_config():
    return {
        "supported_platforms": ["xhs"],
        "default_keyword": "宗教",
        "has_api_key": bool(settings.dashscope_api_key),
        "model_base_url": settings.dashscope_base_url,
        "qwen_text_model": settings.qwen_text_model,
        "qwen_vl_model": settings.qwen_vl_model,
        "qwen_use_response_format": settings.qwen_use_response_format,
        "vl_image_max_side": settings.vl_image_max_side,
        "vl_image_quality": settings.vl_image_quality,
        "whisper_model": settings.whisper_model,
        "whisper_device": settings.whisper_device,
        "whisper_compute_type": settings.whisper_compute_type,
        "whisper_cpu_fallback": settings.whisper_cpu_fallback,
        "media_crawler_dir": str(settings.media_crawler_dir),
        "outputs_dir": str(settings.outputs_dir),
    }


@app.get("/api/outputs")
def list_outputs():
    return {"outputs": MediaCrawlerAdapter().list_existing_outputs()}


@app.get("/api/health/gpu")
def gpu_health():
    return {
        "qwen": {
            "base_url": settings.dashscope_base_url,
            "text_model": settings.qwen_text_model,
            "vl_model": settings.qwen_vl_model,
            "use_response_format": settings.qwen_use_response_format,
            "image_max_side": settings.vl_image_max_side,
            "image_quality": settings.vl_image_quality,
        },
        "whisper": {
            "model": settings.whisper_model,
            "device": settings.whisper_device,
            "compute_type": settings.whisper_compute_type,
            "cpu_fallback": settings.whisper_cpu_fallback,
        },
    }


@app.post("/api/jobs")
def create_job(request: CrawlRequest, background_tasks: BackgroundTasks):
    if request.platform != "xhs":
        raise HTTPException(status_code=400, detail="MVP only supports xhs")
    if not request.run_crawler and not request.source_output_id:
        raise HTTPException(status_code=400, detail="source_output_id is required when run_crawler is false")

    job = job_store.create(
        platform=request.platform,
        keyword=request.keyword,
        start_page=request.start_page,
        max_notes=request.max_notes,
        max_comments=request.max_comments,
        max_concurrency=request.max_concurrency,
        get_sub_comment=request.get_sub_comment,
        analyze_limit=request.analyze_limit,
        run_crawler=request.run_crawler,
        source_output_id=request.source_output_id,
        analysis_batch_size=request.analysis_batch_size,
    )
    pipeline = AuditPipeline(job_id=job["id"])
    background_tasks.add_task(pipeline.run, request)
    return job


@app.post("/api/local-video-jobs")
async def create_local_video_job(
    background_tasks: BackgroundTasks,
    video: UploadFile = File(...),
    title: str = Form(""),
    desc: str = Form(""),
):
    suffix = Path(video.filename or "video.mp4").suffix.lower()
    if suffix not in {".mp4", ".mov", ".m4v", ".avi", ".mkv", ".webm"}:
        raise HTTPException(status_code=400, detail="Unsupported video file type")

    job = job_store.create(
        platform="local",
        keyword=title or video.filename or "local video",
        run_crawler=False,
        source_output_id=None,
        analyze_limit=1,
        analysis_batch_size=1,
        input_type="local_video",
        input_filename=video.filename,
    )
    upload_dir = settings.outputs_dir / job["id"] / "uploads"
    upload_dir.mkdir(parents=True, exist_ok=True)
    video_path = upload_dir / f"input{suffix}"
    with video_path.open("wb") as f:
        while chunk := await video.read(1024 * 1024):
            f.write(chunk)

    pipeline = AuditPipeline(job_id=job["id"])
    background_tasks.add_task(pipeline.run_local_video, video_path, title, desc)
    return job


@app.get("/api/jobs")
def list_jobs():
    return job_store.list()


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str):
    job = job_store.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@app.get("/api/jobs/{job_id}/items")
def get_job_items(job_id: str):
    job = job_store.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return {"items": job.get("items", [])}
