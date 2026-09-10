from __future__ import annotations

import argparse
import glob
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any


IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".bmp", ".webp", ".tif", ".tiff"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Tesseract Uyghur OCR smoke tests with simple preprocessing.")
    parser.add_argument("input", help="Image path, image directory, or glob pattern.")
    parser.add_argument("--lang", default="uig", help="Tesseract language code.")
    parser.add_argument("--psm", action="append", type=int, default=[], help="Page segmentation mode. Can repeat.")
    parser.add_argument("--crop", default="", help="Optional crop x,y,w,h. Use this for subtitle regions.")
    parser.add_argument("--auto-bottom-crop", action="store_true", help="Also test the bottom subtitle-like region.")
    parser.add_argument("--bottom-ratio", type=float, default=0.42, help="Bottom crop height ratio when auto-bottom-crop is on.")
    parser.add_argument("--scale", action="append", type=float, default=[], help="Scale factor. Can repeat.")
    parser.add_argument("--output-dir", type=Path, default=Path("outputs/tesseract_uig_tests"))
    parser.add_argument("--tesseract", default="", help="Path to tesseract binary.")
    parser.add_argument("--tessdata-dir", default="", help="Optional TESSDATA_PREFIX/tessdata directory.")
    return parser.parse_args()


def resolve_inputs(pattern: str) -> list[Path]:
    path = Path(pattern).expanduser()
    if path.exists() and path.is_file():
        return [path.resolve()]
    if path.exists() and path.is_dir():
        return sorted(p.resolve() for p in path.iterdir() if p.suffix.lower() in IMAGE_SUFFIXES)
    return sorted(Path(p).resolve() for p in glob.glob(pattern) if Path(p).suffix.lower() in IMAGE_SUFFIXES)


def resolve_tesseract(configured: str) -> str:
    if configured:
        path = Path(configured).expanduser()
        if path.exists():
            return str(path)
        found = shutil.which(configured)
        if found:
            return found
    return shutil.which("tesseract") or ""


def parse_crop(crop: str) -> tuple[int, int, int, int] | None:
    if not crop:
        return None
    parts = [int(item.strip()) for item in crop.split(",")]
    if len(parts) != 4:
        raise SystemExit("--crop must be x,y,w,h")
    return parts[0], parts[1], parts[2], parts[3]


def import_cv2():
    try:
        import cv2
    except Exception as exc:
        raise SystemExit(
            "OpenCV is required for preprocessing. On Debian/Ubuntu: sudo apt-get install -y python3-opencv"
        ) from exc
    return cv2


def make_variants(image_path: Path, work_dir: Path, crop: tuple[int, int, int, int] | None, auto_bottom: bool, bottom_ratio: float, scales: list[float]) -> list[dict[str, Any]]:
    cv2 = import_cv2()
    work_dir.mkdir(parents=True, exist_ok=True)
    image = cv2.imread(str(image_path))
    if image is None:
        raise SystemExit(f"Cannot read image: {image_path}")
    h, w = image.shape[:2]
    regions: list[tuple[str, np.ndarray]] = [("full", image)]
    if crop:
        x, y, cw, ch = crop
        regions.append(("crop", image[max(0, y) : min(h, y + ch), max(0, x) : min(w, x + cw)]))
    if auto_bottom:
        y = int(h * (1.0 - bottom_ratio))
        regions.append((f"bottom_{bottom_ratio:.2f}", image[y:h, :]))

    if not scales:
        scales = [1.0, 2.0, 3.0]

    variants: list[dict[str, Any]] = []
    for region_name, region in regions:
        if region.size == 0:
            continue
        for scale in scales:
            if scale == 1.0:
                resized = region
            else:
                resized = cv2.resize(region, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
            gray = cv2.cvtColor(resized, cv2.COLOR_BGR2GRAY)
            contrast = cv2.convertScaleAbs(gray, alpha=1.6, beta=8)
            _, otsu = cv2.threshold(contrast, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
            adaptive = cv2.adaptiveThreshold(
                contrast,
                255,
                cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                cv2.THRESH_BINARY,
                31,
                9,
            )
            for kind, mat in [
                ("color", resized),
                ("gray_contrast", contrast),
                ("otsu", otsu),
                ("adaptive", adaptive),
            ]:
                name = f"{region_name}_x{scale:g}_{kind}"
                path = work_dir / f"{name}.png"
                if not cv2.imwrite(str(path), mat):
                    raise SystemExit(f"Failed to write preprocessed image: {path}")
                variants.append({"name": name, "path": path, "region": region_name, "scale": scale, "kind": kind})
    return variants


def run_tesseract(tesseract: str, image_path: Path, lang: str, psm: int, tessdata_dir: str) -> dict[str, Any]:
    cmd = [tesseract, str(image_path), "stdout", "-l", lang, "--psm", str(psm), "--oem", "1"]
    if tessdata_dir:
        cmd.extend(["--tessdata-dir", tessdata_dir])
    started = time.perf_counter()
    completed = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    elapsed = time.perf_counter() - started
    return {
        "psm": psm,
        "elapsed_seconds": elapsed,
        "returncode": completed.returncode,
        "text": (completed.stdout or "").strip(),
        "stderr": " ".join((completed.stderr or "").split())[:1200],
    }


def main() -> None:
    args = parse_args()
    images = resolve_inputs(args.input)
    if not images:
        raise SystemExit(f"No image files found: {args.input}")
    tesseract = resolve_tesseract(args.tesseract)
    if not tesseract:
        raise SystemExit(
            "tesseract not found. On Debian/Ubuntu: sudo apt-get install -y tesseract-ocr tesseract-ocr-uig"
        )

    psms = args.psm or [6, 7, 11, 13]
    crop = parse_crop(args.crop)
    run_dir = (args.output_dir / time.strftime("%Y%m%d_%H%M%S")).resolve()
    run_dir.mkdir(parents=True, exist_ok=True)
    report: dict[str, Any] = {
        "tesseract": tesseract,
        "lang": args.lang,
        "psm": psms,
        "images": [],
    }

    print(f"[1/2] tesseract={tesseract} lang={args.lang} psm={psms}")
    for image in images:
        image_dir = run_dir / image.stem
        image_dir.mkdir(parents=True, exist_ok=True)
        variants = make_variants(image, image_dir / "variants", crop, args.auto_bottom_crop, args.bottom_ratio, args.scale)
        image_result = {"path": str(image), "variants": []}
        print(f"[2/2] {image.name}: {len(variants)} variant(s)")
        for variant in variants:
            variant_results = []
            for psm in psms:
                result = run_tesseract(tesseract, variant["path"], args.lang, psm, args.tessdata_dir)
                variant_results.append(result)
                label = f"{variant['name']} psm={psm}"
                print("")
                print(f"=== {label} rc={result['returncode']} elapsed={result['elapsed_seconds']:.2f}s ===")
                print(result["text"])
                if result["stderr"]:
                    print(f"[stderr] {result['stderr']}")
            item = dict(variant)
            item["path"] = str(item["path"])
            item["results"] = variant_results
            image_result["variants"].append(item)
        (image_dir / "result.json").write_text(json.dumps(image_result, ensure_ascii=False, indent=2), encoding="utf-8")
        report["images"].append(image_result)

    (run_dir / "result.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("")
    print(f"Saved: {run_dir}")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
