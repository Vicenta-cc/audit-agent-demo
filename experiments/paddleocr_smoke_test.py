from __future__ import annotations

import argparse
import glob
import json
import sys
import time
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any


IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".bmp", ".webp", ".tif", ".tiff"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run a small PaddleOCR smoke test on one image, a directory, or a glob pattern."
    )
    parser.add_argument("input", help="Image path, image directory, or glob pattern, for example images/*.jpg")
    parser.add_argument("--lang", default="ug", help="PaddleOCR language code. Use ug for Uyghur.")
    parser.add_argument("--device", default="gpu:0", help="PaddleOCR device, for example gpu:0 or cpu")
    parser.add_argument("--output-dir", type=Path, default=Path("outputs/paddleocr_tests"))
    parser.add_argument("--use-textline-orientation", action="store_true")
    parser.add_argument("--use-doc-orientation-classify", action="store_true")
    parser.add_argument("--use-doc-unwarping", action="store_true")
    parser.add_argument("--legacy-api", action="store_true", help="Force PaddleOCR 2.x style ocr.ocr(...) API")
    return parser.parse_args()


def resolve_inputs(pattern: str) -> list[Path]:
    path = Path(pattern).expanduser()
    if path.exists() and path.is_file():
        return [path.resolve()]
    if path.exists() and path.is_dir():
        return sorted(p.resolve() for p in path.iterdir() if p.suffix.lower() in IMAGE_SUFFIXES)
    return sorted(Path(p).resolve() for p in glob.glob(pattern) if Path(p).suffix.lower() in IMAGE_SUFFIXES)


def import_paddleocr():
    try:
        from paddleocr import PaddleOCR
    except Exception as exc:
        install_hint = """
Missing PaddleOCR dependencies.

Suggested server install:
  cd /mnt/workspace/paddleocr-test
  python3 -m venv .venv-paddleocr
  . .venv-paddleocr/bin/activate
  python -m pip install -U pip

  # CPU:
  python -m pip install paddlepaddle -i https://www.paddlepaddle.org.cn/packages/stable/cpu/

  # Or GPU, pick the CUDA index that matches your server. Example for CUDA 11.8:
  python -m pip install paddlepaddle-gpu -i https://www.paddlepaddle.org.cn/packages/stable/cu118/

  python -m pip install paddleocr

Then run:
  python paddleocr_smoke_test.py 'images/*' --lang ug --device gpu:0
"""
        raise SystemExit(f"{install_hint}\nOriginal import error: {exc}") from exc
    return PaddleOCR


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


def build_ocr(PaddleOCR: Any, args: argparse.Namespace) -> Any:
    kwargs = {
        "lang": args.lang,
        "use_doc_orientation_classify": args.use_doc_orientation_classify,
        "use_doc_unwarping": args.use_doc_unwarping,
        "use_textline_orientation": args.use_textline_orientation,
        "device": args.device,
    }
    try:
        return PaddleOCR(**kwargs)
    except TypeError:
        kwargs.pop("device", None)
        try:
            return PaddleOCR(**kwargs)
        except TypeError:
            return PaddleOCR(use_angle_cls=args.use_textline_orientation, lang=args.lang)


def predict_one(ocr: Any, image_path: Path, legacy_api: bool) -> Any:
    if not legacy_api and hasattr(ocr, "predict"):
        return ocr.predict(str(image_path))
    return ocr.ocr(str(image_path), cls=True)


def save_visuals(raw_result: Any, output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    if not isinstance(raw_result, list):
        raw_items = [raw_result]
    else:
        raw_items = raw_result
    for item in raw_items:
        if hasattr(item, "save_to_img"):
            try:
                item.save_to_img(str(output_dir))
            except Exception:
                pass
        if hasattr(item, "save_to_json"):
            try:
                item.save_to_json(str(output_dir))
            except Exception:
                pass


def extract_text_lines(payload: Any) -> list[dict[str, Any]]:
    lines: list[dict[str, Any]] = []

    def from_mapping(mapping: dict[str, Any]) -> bool:
        res = mapping.get("res", mapping)
        texts = res.get("rec_texts")
        scores = res.get("rec_scores", [])
        boxes = res.get("rec_boxes", res.get("rec_polys", []))
        if not isinstance(texts, list):
            return False
        for index, text in enumerate(texts):
            lines.append(
                {
                    "index": index,
                    "text": text,
                    "score": scores[index] if index < len(scores) else None,
                    "box": boxes[index] if index < len(boxes) else None,
                }
            )
        return True

    if isinstance(payload, dict):
        from_mapping(payload)
        return lines
    if isinstance(payload, list):
        for item in payload:
            if isinstance(item, dict) and from_mapping(item):
                continue
            # PaddleOCR 2.x shape: [[box, [text, score]], ...]
            if isinstance(item, list):
                for index, line in enumerate(item):
                    if (
                        isinstance(line, list)
                        and len(line) >= 2
                        and isinstance(line[1], (list, tuple))
                        and len(line[1]) >= 2
                    ):
                        lines.append(
                            {
                                "index": index,
                                "text": line[1][0],
                                "score": line[1][1],
                                "box": line[0],
                            }
                        )
        return lines
    return lines


def main() -> None:
    args = parse_args()
    images = resolve_inputs(args.input)
    if not images:
        raise SystemExit(f"No image files found: {args.input}")

    run_dir = (args.output_dir / time.strftime("%Y%m%d_%H%M%S")).resolve()
    run_dir.mkdir(parents=True, exist_ok=True)

    PaddleOCR = import_paddleocr()
    print(f"[1/3] loading PaddleOCR lang={args.lang} device={args.device}")
    ocr = build_ocr(PaddleOCR, args)

    report: dict[str, Any] = {
        "lang": args.lang,
        "device": args.device,
        "images": [],
    }
    all_text_blocks: list[str] = []
    print(f"[2/3] processing {len(images)} image(s)")

    for image_path in images:
        started = time.perf_counter()
        raw = predict_one(ocr, image_path, args.legacy_api)
        elapsed = time.perf_counter() - started
        payload = serializable(raw)
        text_lines = extract_text_lines(payload)

        image_dir = run_dir / image_path.stem
        image_dir.mkdir(parents=True, exist_ok=True)
        save_visuals(raw, image_dir)
        (image_dir / "raw.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        text = "\n".join(line["text"] for line in text_lines)
        (image_dir / "text.txt").write_text(text, encoding="utf-8")

        print("")
        print(f"=== {image_path.name} elapsed={elapsed:.2f}s lines={len(text_lines)} ===")
        print(text)
        all_text_blocks.append(f"## {image_path.name}\n{text}")
        report["images"].append(
            {
                "path": str(image_path),
                "elapsed_seconds": elapsed,
                "line_count": len(text_lines),
                "text": text,
                "lines": text_lines,
                "raw_json": str(image_dir / "raw.json"),
                "text_path": str(image_dir / "text.txt"),
            }
        )

    (run_dir / "result.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    (run_dir / "texts.txt").write_text("\n\n".join(all_text_blocks), encoding="utf-8")
    print("")
    print(f"[3/3] saved: {run_dir}")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
