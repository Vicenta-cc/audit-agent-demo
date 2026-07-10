from __future__ import annotations

import argparse
import glob
import json
import sys
import time
import types
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any


IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".bmp", ".webp", ".tif", ".tiff"}

UYGHUR_STRONG_PROMPT_EN = (
    "OCR: The image contains Uyghur text written in Arabic script, read from right to left. "
    "Transcribe the visible Uyghur text exactly. Output only the original Uyghur text. "
    "Do not translate, explain, romanize, or normalize it into Arabic, Persian, Urdu, or another language. "
    "Preserve the original content and line breaks."
)

UYGHUR_STRONG_PROMPT_ZH = (
    "OCR: 你是一个专业的维吾尔语 OCR 专家。请对图片中的维吾尔文"
    "（Uyghur，采用阿拉伯字母书写，从右向左阅读）进行精准的文字识别。"
    "请直接输出识别到的维吾尔语文本，不要将其误认为是阿拉伯语或波斯语，"
    "不要解释，不要翻译，严格保持原文内容。"
)

PROMPT_PRESETS = {
    "none": "",
    "hf-ocr": "OCR:",
    "uyghur-strong-en": UYGHUR_STRONG_PROMPT_EN,
    "uyghur-strong-zh": UYGHUR_STRONG_PROMPT_ZH,
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run a small PaddleOCR-VL smoke test on image(s).")
    parser.add_argument("input", help="Image path, image directory, or glob pattern.")
    parser.add_argument("--output-dir", type=Path, default=Path("outputs/paddleocr_vl_tests"))
    parser.add_argument(
        "--lite-output",
        action="store_true",
        help="Save only extracted text and compact metadata; skip large raw result JSON.",
    )
    parser.add_argument(
        "--skip-save-assets",
        action="store_true",
        help="Skip PaddleOCR result object's save_to_json/save_to_markdown/save_to_img helpers.",
    )
    parser.add_argument("--device", default="cpu", help="Device passed to PaddleOCRVL, for example cpu or gpu:0.")
    parser.add_argument(
        "--pipeline-version",
        choices=("v1", "v1.5", "v1.6"),
        default="v1.6",
        help="PaddleOCR-VL pipeline version. v1 maps to the older PaddleOCR-VL pipeline; v1.6 is the current default.",
    )
    parser.add_argument("--vl-rec-model-name", default="", help="Optional PaddleOCR-VL recognition model name.")
    parser.add_argument("--vl-rec-model-dir", default="", help="Optional local PaddleOCR-VL model directory.")
    parser.add_argument("--use-layout-detection", action="store_true", help="Enable layout detection.")
    parser.add_argument("--use-doc-orientation-classify", action="store_true")
    parser.add_argument("--use-doc-unwarping", action="store_true")
    parser.add_argument("--use-chart-recognition", action="store_true")
    parser.add_argument("--use-seal-recognition", action="store_true")
    parser.add_argument("--use-ocr-for-image-block", action="store_true")
    parser.add_argument("--prompt-label", default="", help="Optional prompt label forwarded to PaddleOCRVL.predict.")
    parser.add_argument(
        "--prompt-preset",
        choices=sorted(PROMPT_PRESETS),
        default="none",
        help="Optional VLM query override. hf-ocr mirrors the built-in OCR query; uyghur-strong-* adds a language constraint.",
    )
    parser.add_argument("--custom-prompt", default="", help="Override the internal VLM query with this text.")
    parser.add_argument("--custom-prompt-file", type=Path, default=None, help="Read the VLM query override from a UTF-8 text file.")
    parser.add_argument(
        "--prompt-compose",
        choices=("replace", "prepend", "append"),
        default="replace",
        help="How to combine the custom/preset prompt with PaddleOCR-VL's internal block prompt.",
    )
    parser.add_argument("--max-new-tokens", type=int, default=1024)
    parser.add_argument("--repetition-penalty", type=float, default=None)
    parser.add_argument("--temperature", type=float, default=None)
    parser.add_argument("--top-p", type=float, default=None)
    parser.add_argument("--min-pixels", type=int, default=None)
    parser.add_argument("--max-pixels", type=int, default=None)
    parser.add_argument(
        "--vlm-extra-arg",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help="Extra VLM kwarg forwarded through vlm_extra_args. VALUE is parsed as JSON when possible.",
    )
    return parser.parse_args()


def resolve_inputs(pattern: str) -> list[Path]:
    path = Path(pattern).expanduser()
    if path.exists() and path.is_file():
        return [path.resolve()]
    if path.exists() and path.is_dir():
        return sorted(p.resolve() for p in path.iterdir() if p.suffix.lower() in IMAGE_SUFFIXES)
    return sorted(Path(p).resolve() for p in glob.glob(pattern) if Path(p).suffix.lower() in IMAGE_SUFFIXES)


def import_paddleocr_vl():
    try:
        from paddleocr import PaddleOCRVL
    except Exception as exc:
        install_hint = """
Missing PaddleOCR-VL dependencies.

Suggested local/server install:
  python3 -m venv .venv-paddleocr
  . .venv-paddleocr/bin/activate
  python -m pip install -U pip
  python -m pip install paddlepaddle
  python -m pip install paddleocr

Then run:
  python experiments/paddleocr_vl_smoke_test.py data/ocr_images/image16.jpg --device cpu
"""
        raise SystemExit(f"{install_hint}\nOriginal import error: {exc}") from exc
    return PaddleOCRVL


def parse_jsonish(value: str) -> Any:
    lowered = value.lower()
    if lowered == "none":
        return None
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return value


def parse_vlm_extra_args(items: list[str]) -> dict[str, Any]:
    extra: dict[str, Any] = {}
    for item in items:
        if "=" not in item:
            raise SystemExit(f"--vlm-extra-arg must be KEY=VALUE, got: {item}")
        key, value = item.split("=", 1)
        key = key.strip()
        if not key:
            raise SystemExit(f"--vlm-extra-arg has an empty key: {item}")
        extra[key] = parse_jsonish(value.strip())
    return extra


def resolve_prompt_override(args: argparse.Namespace) -> str:
    prompts = [
        PROMPT_PRESETS[args.prompt_preset],
        args.custom_prompt.strip(),
    ]
    if args.custom_prompt_file is not None:
        prompts.append(args.custom_prompt_file.expanduser().read_text(encoding="utf-8").strip())
    prompts = [prompt for prompt in prompts if prompt]
    if len(prompts) > 1:
        raise SystemExit("Use only one of --prompt-preset, --custom-prompt, or --custom-prompt-file.")
    return prompts[0] if prompts else ""


def compose_prompt(base_prompt: str, override_prompt: str, mode: str) -> str:
    if mode == "replace":
        return override_prompt
    if mode == "prepend":
        return f"{override_prompt}\n{base_prompt}".strip()
    if mode == "append":
        return f"{base_prompt}\n{override_prompt}".strip()
    raise ValueError(f"Unknown prompt compose mode: {mode}")


def install_vlm_prompt_override(pipeline: Any, override_prompt: str, compose_mode: str) -> bool:
    if not override_prompt:
        return False

    inner_pipeline = getattr(pipeline, "paddlex_pipeline", pipeline)
    method_name = "_paddleocr_vl_collect_page_vlm_entries_core"
    original_method = getattr(inner_pipeline, method_name, None)
    if not callable(original_method):
        raise SystemExit(
            "This PaddleOCR-VL version does not expose the internal VLM prompt hook used by "
            "--prompt-preset/--custom-prompt. Try the HF-like mode first: --prompt-label ocr "
            "with layout detection disabled."
        )

    def patched_method(self, page_idx, blocks_for_img, imgs_in_doc_for_img, layout_prep_cfg):
        entries, page_has_spotting, page_drop_figures = original_method(
            page_idx,
            blocks_for_img,
            imgs_in_doc_for_img,
            layout_prep_cfg,
        )
        patched_entries = []
        for entry in entries:
            entry_page_idx, block_idx, block_img, base_prompt, pixel_limits, figure_token_map = entry
            patched_entries.append(
                (
                    entry_page_idx,
                    block_idx,
                    block_img,
                    compose_prompt(base_prompt, override_prompt, compose_mode),
                    pixel_limits,
                    figure_token_map,
                )
            )
        return patched_entries, page_has_spotting, page_drop_figures

    setattr(inner_pipeline, method_name, types.MethodType(patched_method, inner_pipeline))
    return True


def serializable(value: Any) -> Any:
    if is_dataclass(value):
        return serializable(asdict(value))
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, dict):
        return {str(k): serializable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [serializable(item) for item in value]
    if hasattr(value, "tolist"):
        return value.tolist()
    if hasattr(value, "model_dump"):
        return serializable(value.model_dump())
    if hasattr(value, "json"):
        attr = value.json
        try:
            return serializable(attr() if callable(attr) else attr)
        except Exception:
            pass
    if hasattr(value, "__dict__"):
        return serializable(vars(value))
    return str(value)


def extract_text(payload: Any) -> str:
    chunks: list[str] = []

    def walk(value: Any) -> None:
        if value is None:
            return
        if isinstance(value, str):
            stripped = value.strip()
            if stripped:
                chunks.append(stripped)
            return
        if isinstance(value, dict):
            for key in (
                "markdown",
                "text",
                "content",
                "rec_text",
                "rec_texts",
                "description",
                "html",
            ):
                if key in value:
                    walk(value[key])
            for key, item in value.items():
                if key not in {"image", "input_img", "output_img", "page_img"}:
                    if key not in {"markdown", "text", "content", "rec_text", "rec_texts", "description", "html"}:
                        walk(item)
            return
        if isinstance(value, list):
            for item in value:
                walk(item)

    walk(payload)
    seen: set[str] = set()
    deduped: list[str] = []
    for chunk in chunks:
        if chunk not in seen:
            seen.add(chunk)
            deduped.append(chunk)
    return "\n".join(deduped)


def save_result_objects(raw_result: Any, output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    items = raw_result if isinstance(raw_result, list) else [raw_result]
    for item in items:
        for method_name in ("save_to_json", "save_to_markdown", "save_to_img"):
            method = getattr(item, method_name, None)
            if callable(method):
                try:
                    method(str(output_dir))
                except Exception:
                    pass


def build_pipeline(args: argparse.Namespace) -> Any:
    PaddleOCRVL = import_paddleocr_vl()
    kwargs: dict[str, Any] = {
        "pipeline_version": args.pipeline_version,
        "use_doc_orientation_classify": args.use_doc_orientation_classify,
        "use_doc_unwarping": args.use_doc_unwarping,
        "use_layout_detection": args.use_layout_detection,
        "use_chart_recognition": args.use_chart_recognition,
        "use_seal_recognition": args.use_seal_recognition,
        "use_ocr_for_image_block": args.use_ocr_for_image_block,
        "device": args.device,
    }
    if args.vl_rec_model_name:
        kwargs["vl_rec_model_name"] = args.vl_rec_model_name
    if args.vl_rec_model_dir:
        kwargs["vl_rec_model_dir"] = args.vl_rec_model_dir
    try:
        return PaddleOCRVL(**kwargs)
    except TypeError:
        kwargs.pop("device", None)
        return PaddleOCRVL(**kwargs)


def predict_one(pipeline: Any, image_path: Path, args: argparse.Namespace, vlm_extra_args: dict[str, Any]) -> Any:
    kwargs: dict[str, Any] = {
        "use_doc_orientation_classify": args.use_doc_orientation_classify,
        "use_doc_unwarping": args.use_doc_unwarping,
        "use_layout_detection": args.use_layout_detection,
        "use_chart_recognition": args.use_chart_recognition,
        "use_seal_recognition": args.use_seal_recognition,
        "use_ocr_for_image_block": args.use_ocr_for_image_block,
        "max_new_tokens": args.max_new_tokens,
    }
    if args.prompt_label:
        kwargs["prompt_label"] = args.prompt_label
    if args.repetition_penalty is not None:
        kwargs["repetition_penalty"] = args.repetition_penalty
    if args.temperature is not None:
        kwargs["temperature"] = args.temperature
    if args.top_p is not None:
        kwargs["top_p"] = args.top_p
    if args.min_pixels is not None:
        kwargs["min_pixels"] = args.min_pixels
    if args.max_pixels is not None:
        kwargs["max_pixels"] = args.max_pixels
    if vlm_extra_args:
        kwargs["vlm_extra_args"] = vlm_extra_args
    return pipeline.predict(str(image_path), **kwargs)


def main() -> None:
    args = parse_args()
    images = resolve_inputs(args.input)
    if not images:
        raise SystemExit(f"No image files found: {args.input}")
    prompt_override = resolve_prompt_override(args)
    vlm_extra_args = parse_vlm_extra_args(args.vlm_extra_arg)

    run_dir = (args.output_dir / time.strftime("%Y%m%d_%H%M%S")).resolve()
    run_dir.mkdir(parents=True, exist_ok=True)

    print(f"[1/3] loading PaddleOCR-VL device={args.device} layout={args.use_layout_detection}")
    pipeline = build_pipeline(args)
    prompt_override_installed = install_vlm_prompt_override(pipeline, prompt_override, args.prompt_compose)
    report: dict[str, Any] = {
        "images": [],
        "device": args.device,
        "pipeline_version": args.pipeline_version,
        "use_layout_detection": args.use_layout_detection,
        "use_chart_recognition": args.use_chart_recognition,
        "use_seal_recognition": args.use_seal_recognition,
        "prompt_label": args.prompt_label,
        "prompt_preset": args.prompt_preset,
        "prompt_compose": args.prompt_compose,
        "prompt_override": prompt_override,
        "prompt_override_installed": prompt_override_installed,
        "vlm_extra_args": vlm_extra_args,
    }
    if prompt_override_installed:
        print(f"[prompt] overriding internal VLM query via {args.prompt_preset or 'custom'} ({args.prompt_compose})")
        print(prompt_override[:1000])

    print(f"[2/3] processing {len(images)} image(s)")
    for image_path in images:
        image_dir = run_dir / image_path.stem
        image_dir.mkdir(parents=True, exist_ok=True)
        started = time.perf_counter()
        raw = predict_one(pipeline, image_path, args, vlm_extra_args)
        elapsed = time.perf_counter() - started
        if not args.skip_save_assets:
            save_result_objects(raw, image_dir)
        payload = serializable(raw)
        text = extract_text(payload)
        (image_dir / "text.txt").write_text(text, encoding="utf-8")
        raw_json_path = ""
        if not args.lite_output:
            raw_json_path = str(image_dir / "raw.json")
            (image_dir / "raw.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print("")
        print(f"=== {image_path.name} elapsed={elapsed:.2f}s ===")
        print(text[:4000])
        report["images"].append(
            {
                "path": str(image_path),
                "elapsed_seconds": elapsed,
                "text": text,
                "raw_json": raw_json_path,
                "text_path": str(image_dir / "text.txt"),
            }
        )

    (run_dir / "result.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("")
    print(f"[3/3] saved: {run_dir}")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
