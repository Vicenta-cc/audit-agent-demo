from __future__ import annotations

import argparse
import json
import math
import re
import time
from pathlib import Path
from typing import Any


DEFAULT_MODEL = "tencent/HY-MT1.5-1.8B-GPTQ-Int4"
ARABIC_SCRIPT_RE = re.compile(r"[\u0600-\u06ff\u0750-\u077f\u08a0-\u08ff]")
CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")
LATIN_RE = re.compile(r"[A-Za-z]")
WEIRD_MARKERS = (
    "<source>",
    "</source>",
    "<target>",
    "</target>",
    "Translate the following",
    "without additional explanation",
    "将以下文本翻译",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Translate OCR/ASR text with HY-MT and estimate Chinese fluency heuristically."
    )
    parser.add_argument("input", type=Path, help="A text file, OCR result.json, or an output directory containing text.txt.")
    parser.add_argument("--output-dir", type=Path, default=Path("outputs/hymt_translation_tests"))
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--target-language", default="中文")
    parser.add_argument(
        "--prompt-style",
        choices=("ocr-robust", "official", "literal"),
        default="ocr-robust",
        help="Prompt style. official mirrors the HY-MT model card; ocr-robust is better for OCR-noisy Uyghur text.",
    )
    parser.add_argument("--device-map", default="auto")
    parser.add_argument(
        "--dtype",
        choices=("auto", "float16", "bfloat16", "float32"),
        default="auto",
        help="torch_dtype passed to from_pretrained. Use auto for GPTQ/int4 unless you have a reason to override.",
    )
    parser.add_argument("--trust-remote-code", action="store_true")
    parser.add_argument("--max-new-tokens", type=int, default=512)
    parser.add_argument("--num-samples", type=int, default=1, help="Generate N translations per input to estimate pass rate.")
    parser.add_argument("--seed", type=int, default=20260702)
    parser.add_argument("--temperature", type=float, default=0.7)
    parser.add_argument("--top-p", type=float, default=0.6)
    parser.add_argument("--top-k", type=int, default=20)
    parser.add_argument("--repetition-penalty", type=float, default=1.05)
    parser.add_argument("--max-input-chars", type=int, default=4000)
    parser.add_argument("--min-fluency-score", type=float, default=70.0)
    parser.add_argument("--hf-endpoint", default="", help="Optional Hugging Face endpoint, for example https://hf-mirror.com.")
    return parser.parse_args()


def read_json_texts(path: Path) -> list[dict[str, str]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    entries: list[dict[str, str]] = []

    if isinstance(data, dict) and isinstance(data.get("images"), list):
        for idx, item in enumerate(data["images"]):
            if not isinstance(item, dict):
                continue
            text = str(item.get("text") or "").strip()
            if not text:
                text_path = item.get("text_path")
                if text_path and Path(text_path).exists():
                    text = Path(text_path).read_text(encoding="utf-8").strip()
            if text:
                name = Path(str(item.get("path") or f"image_{idx:03d}")).stem
                entries.append({"id": name, "source_path": str(path), "source_text": text})
        return entries

    text = extract_text_from_payload(data).strip()
    if text:
        entries.append({"id": path.stem, "source_path": str(path), "source_text": text})
    return entries


def extract_text_from_payload(value: Any) -> str:
    chunks: list[str] = []

    def walk(item: Any) -> None:
        if item is None:
            return
        if isinstance(item, str):
            stripped = item.strip()
            if stripped:
                chunks.append(stripped)
            return
        if isinstance(item, dict):
            for key in ("markdown", "text", "content", "rec_text", "rec_texts", "description", "block_content"):
                if key in item:
                    walk(item[key])
            return
        if isinstance(item, list):
            for child in item:
                walk(child)

    walk(value)
    deduped: list[str] = []
    seen: set[str] = set()
    for chunk in chunks:
        if chunk not in seen:
            deduped.append(chunk)
            seen.add(chunk)
    return "\n".join(deduped)


def read_inputs(input_path: Path) -> list[dict[str, str]]:
    path = input_path.expanduser()
    if path.is_file() and path.suffix.lower() == ".json":
        return read_json_texts(path)
    if path.is_file():
        text = path.read_text(encoding="utf-8").strip()
        return [{"id": path.stem, "source_path": str(path), "source_text": text}] if text else []
    if path.is_dir():
        entries: list[dict[str, str]] = []
        text_files = sorted(path.rglob("text.txt"))
        for text_path in text_files:
            if "hymt_translation_tests" in text_path.parts:
                continue
            text = text_path.read_text(encoding="utf-8").strip()
            if text:
                entries.append(
                    {
                        "id": text_path.parent.name,
                        "source_path": str(text_path),
                        "source_text": text,
                    }
                )
        if entries:
            return entries
        result_json = path / "result.json"
        if result_json.exists():
            return read_json_texts(result_json)
    return []


def build_prompt(source_text: str, target_language: str, style: str) -> str:
    if style == "official":
        return f"将以下文本翻译为{target_language}，注意只需要输出翻译后的结果，不要额外解释：\n\n{source_text}"
    if style == "literal":
        return (
            f"请将以下维吾尔语文本忠实翻译为{target_language}。"
            f"只输出译文，不要解释，不要补充原文没有的信息：\n\n{source_text}"
        )
    if style == "ocr-robust":
        return (
            f"请将以下维吾尔语 OCR 识别文本翻译为通顺自然的{target_language}。"
            "如果原文存在少量 OCR 错字，请根据上下文尽量还原含义；"
            "只输出译文，不要解释，不要输出原文：\n\n"
            f"{source_text}"
        )
    raise ValueError(f"Unknown prompt style: {style}")


def torch_dtype_from_name(name: str):
    if name == "auto":
        return "auto"
    import torch

    return {
        "float16": torch.float16,
        "bfloat16": torch.bfloat16,
        "float32": torch.float32,
    }[name]


def load_model(args: argparse.Namespace):
    import os

    if args.hf_endpoint:
        os.environ["HF_ENDPOINT"] = args.hf_endpoint

    from transformers import AutoModelForCausalLM, AutoTokenizer

    tokenizer = AutoTokenizer.from_pretrained(args.model, trust_remote_code=args.trust_remote_code)
    model = AutoModelForCausalLM.from_pretrained(
        args.model,
        device_map=args.device_map,
        torch_dtype=torch_dtype_from_name(args.dtype),
        trust_remote_code=args.trust_remote_code,
    )
    model.eval()
    return tokenizer, model


def translate_once(tokenizer: Any, model: Any, prompt: str, args: argparse.Namespace, sample_idx: int) -> str:
    import torch

    if args.seed is not None:
        torch.manual_seed(args.seed + sample_idx)
        if torch.cuda.is_available():
            torch.cuda.manual_seed_all(args.seed + sample_idx)

    messages = [{"role": "user", "content": prompt}]
    input_ids = tokenizer.apply_chat_template(
        messages,
        tokenize=True,
        add_generation_prompt=False,
        return_tensors="pt",
    )
    input_ids = input_ids.to(model.device)
    do_sample = args.temperature > 0
    generate_kwargs: dict[str, Any] = {
        "max_new_tokens": args.max_new_tokens,
        "repetition_penalty": args.repetition_penalty,
        "do_sample": do_sample,
        "eos_token_id": getattr(tokenizer, "eos_token_id", None),
        "pad_token_id": getattr(tokenizer, "pad_token_id", None),
    }
    if do_sample:
        generate_kwargs.update({"temperature": args.temperature, "top_p": args.top_p, "top_k": args.top_k})
    with torch.inference_mode():
        outputs = model.generate(input_ids, **generate_kwargs)
    new_tokens = outputs[0][input_ids.shape[-1] :]
    return tokenizer.decode(new_tokens, skip_special_tokens=True).strip()


def fluency_metrics(text: str, source_text: str) -> dict[str, Any]:
    visible = [ch for ch in text if not ch.isspace()]
    total = len(visible)
    if total == 0:
        return {
            "score": 0.0,
            "cjk_ratio": 0.0,
            "source_script_ratio": 0.0,
            "latin_ratio": 0.0,
            "length_ratio": 0.0,
            "markers": [],
        }

    cjk_count = len(CJK_RE.findall(text))
    source_script_count = len(ARABIC_SCRIPT_RE.findall(text))
    latin_count = len(LATIN_RE.findall(text))
    marker_hits = [marker for marker in WEIRD_MARKERS if marker in text]
    length_ratio = len(text.strip()) / max(1, len(source_text.strip()))

    cjk_ratio = cjk_count / total
    source_script_ratio = source_script_count / total
    latin_ratio = latin_count / total
    score = 100.0
    score -= max(0.0, 0.65 - cjk_ratio) * 90.0
    score -= min(45.0, source_script_ratio * 180.0)
    score -= min(20.0, latin_ratio * 60.0)
    score -= len(marker_hits) * 12.0
    if len(text.strip()) < 2:
        score -= 80.0
    if length_ratio < 0.08:
        score -= 25.0
    if length_ratio > 4.0:
        score -= min(25.0, (length_ratio - 4.0) * 5.0)
    score = max(0.0, min(100.0, score))
    return {
        "score": round(score, 2),
        "cjk_ratio": round(cjk_ratio, 4),
        "source_script_ratio": round(source_script_ratio, 4),
        "latin_ratio": round(latin_ratio, 4),
        "length_ratio": round(length_ratio, 4),
        "markers": marker_hits,
    }


def safe_name(value: str) -> str:
    cleaned = re.sub(r"[^0-9A-Za-z_.-]+", "_", value).strip("._")
    return cleaned or "sample"


def main() -> None:
    args = parse_args()
    inputs = read_inputs(args.input)
    if not inputs:
        raise SystemExit(f"No text found in {args.input}")

    run_dir = (args.output_dir / time.strftime("%Y%m%d_%H%M%S")).resolve()
    run_dir.mkdir(parents=True, exist_ok=True)

    print(f"[1/3] loading HY-MT model: {args.model}")
    tokenizer, model = load_model(args)

    report: dict[str, Any] = {
        "model": args.model,
        "target_language": args.target_language,
        "prompt_style": args.prompt_style,
        "num_samples": args.num_samples,
        "min_fluency_score": args.min_fluency_score,
        "items": [],
    }

    print(f"[2/3] translating {len(inputs)} input(s)")
    for item_idx, item in enumerate(inputs):
        source_text = item["source_text"][: args.max_input_chars].strip()
        prompt = build_prompt(source_text, args.target_language, args.prompt_style)
        item_dir = run_dir / safe_name(item["id"])
        item_dir.mkdir(parents=True, exist_ok=True)
        (item_dir / "source.txt").write_text(source_text, encoding="utf-8")
        (item_dir / "prompt.txt").write_text(prompt, encoding="utf-8")

        samples = []
        started = time.perf_counter()
        for sample_idx in range(args.num_samples):
            translation = translate_once(tokenizer, model, prompt, args, item_idx * 1000 + sample_idx)
            metrics = fluency_metrics(translation, source_text)
            passed = metrics["score"] >= args.min_fluency_score
            sample_name = f"translation_{sample_idx + 1:02d}.txt"
            (item_dir / sample_name).write_text(translation, encoding="utf-8")
            samples.append(
                {
                    "sample_idx": sample_idx + 1,
                    "translation": translation,
                    "fluency": metrics,
                    "passed": passed,
                    "translation_path": str(item_dir / sample_name),
                }
            )
            print("")
            print(f"=== {item['id']} sample={sample_idx + 1}/{args.num_samples} score={metrics['score']} pass={passed} ===")
            print(translation[:2000])
        elapsed = time.perf_counter() - started
        pass_rate = sum(1 for sample in samples if sample["passed"]) / max(1, len(samples))
        report["items"].append(
            {
                "id": item["id"],
                "source_path": item["source_path"],
                "source_text": source_text,
                "elapsed_seconds": elapsed,
                "pass_rate": round(pass_rate, 4),
                "samples": samples,
            }
        )

    pass_rates = [item["pass_rate"] for item in report["items"]]
    report["average_pass_rate"] = round(sum(pass_rates) / max(1, len(pass_rates)), 4)
    report["average_pass_rate_percent"] = round(report["average_pass_rate"] * 100.0, 2)
    (run_dir / "result.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print("")
    print(f"[3/3] saved: {run_dir}")
    print(f"average pass rate: {report['average_pass_rate_percent']}%")


if __name__ == "__main__":
    main()
