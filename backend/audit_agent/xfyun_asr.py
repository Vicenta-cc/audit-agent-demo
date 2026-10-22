"""XFYUN Spark multilingual streaming ASR client."""

from __future__ import annotations

import asyncio
import base64
from collections import Counter
from email.utils import formatdate
import hashlib
import hmac
import json
from pathlib import Path
import ssl
import time
from urllib.parse import urlencode, urlsplit
import wave

import certifi

from .asr_chunks import transcribe_in_chunks
from .config import settings


class XfyunAPIError(RuntimeError):
    """A terminal response from XFYUN rather than a transport failure."""


class XfyunASRClient:
    def __init__(self) -> None:
        self.app_id = settings.xfyun_app_id
        self.api_key = settings.xfyun_api_key
        self.api_secret = settings.xfyun_api_secret
        self.ws_url = settings.xfyun_ws_url
        self.proxy_url = settings.xfyun_proxy_url or None
        self.request_retries = settings.remote_asr_request_retries
        self.retry_backoff_seconds = settings.remote_asr_retry_backoff_seconds

    @property
    def enabled(self) -> bool:
        return bool(self.app_id and self.api_key and self.api_secret and self.ws_url)

    def transcribe(self, audio_path: Path) -> dict:
        if not self.enabled:
            raise RuntimeError("XFYUN ASR credentials or endpoint are incomplete")
        return transcribe_in_chunks(
            audio_path,
            self._transcribe_chunk_with_retry,
            max_seconds=min(60, settings.asr_chunk_seconds),
            overlap_seconds=0,
        )

    def _transcribe_chunk_with_retry(self, audio_path: Path) -> dict:
        import websockets

        attempts = self.request_retries + 1
        for attempt in range(attempts):
            try:
                return asyncio.run(self._transcribe_chunk(audio_path))
            except XfyunAPIError:
                raise
            except (OSError, TimeoutError, websockets.exceptions.WebSocketException):
                if attempt + 1 >= attempts:
                    raise
                delay = self.retry_backoff_seconds * (2**attempt)
                if delay > 0:
                    time.sleep(delay)
        raise RuntimeError("XFYUN ASR retry loop exhausted")

    async def _transcribe_chunk(self, audio_path: Path) -> dict:
        with wave.open(str(audio_path), "rb") as source:
            if (
                source.getnchannels() != 1
                or source.getsampwidth() != 2
                or source.getframerate() != 16000
            ):
                raise RuntimeError(
                    "XFYUN ASR input must be 16 kHz, 16-bit, mono PCM WAV"
                )
            pcm = source.readframes(source.getnframes())

        duration = len(pcm) / 32000
        if duration > 60.01:
            raise RuntimeError("XFYUN ASR chunk exceeds the 60 second service limit")
        if not pcm:
            return self._result("", duration, [], "", [])

        payloads, sid = await self._stream_pcm(pcm)
        text, words, language_items = self._merge_payloads(payloads)
        return self._result(text, duration, words, sid, payloads, language_items)

    async def _stream_pcm(self, pcm: bytes) -> tuple[list[dict], str]:
        import websockets

        payloads: list[dict] = []
        sid = ""
        ssl_context = ssl.create_default_context(cafile=certifi.where())
        async with websockets.connect(
            self._signed_url(),
            proxy=self.proxy_url,
            open_timeout=20,
            close_timeout=10,
            ping_interval=None,
            max_size=8 * 1024 * 1024,
            ssl=ssl_context,
        ) as socket:

            async def receive() -> None:
                nonlocal sid
                while True:
                    raw = await asyncio.wait_for(socket.recv(), timeout=30)
                    message = json.loads(raw)
                    header = message.get("header") or {}
                    sid = str(header.get("sid") or sid)
                    code = int(header.get("code") or 0)
                    if code != 0:
                        raise XfyunAPIError(
                            f"XFYUN code={code} message={header.get('message', '')}"
                        )
                    encoded = (
                        ((message.get("payload") or {}).get("result") or {}).get("text")
                    )
                    if encoded:
                        decoded = json.loads(base64.b64decode(encoded).decode("utf-8"))
                        ret = int(decoded.get("ret", 0) or 0)
                        if ret != 0:
                            raise XfyunAPIError(f"XFYUN result ret={ret}")
                        payloads.append(decoded)
                    if int(header.get("status", 0)) == 2:
                        return

            receiver = asyncio.create_task(receive())
            try:
                frame_size = 1280
                frames = [
                    pcm[position : position + frame_size]
                    for position in range(0, len(pcm), frame_size)
                ]
                if len(frames) == 1:
                    frames.append(b"")
                for sequence, frame in enumerate(frames, start=1):
                    if receiver.done():
                        await receiver
                    status = (
                        0
                        if sequence == 1
                        else (2 if sequence == len(frames) else 1)
                    )
                    await socket.send(
                        json.dumps(
                            self._request_payload(sequence, status, frame),
                            ensure_ascii=False,
                        )
                    )
                    if status != 2:
                        await asyncio.sleep(0.04)
                await asyncio.wait_for(receiver, timeout=45)
            finally:
                if not receiver.done():
                    receiver.cancel()
                    await asyncio.gather(receiver, return_exceptions=True)
        return payloads, sid

    def _signed_url(self) -> str:
        parsed = urlsplit(self.ws_url)
        if (
            parsed.scheme != "wss"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            raise RuntimeError("XFYUN_WS_URL must be a credential-free wss URL")
        host = parsed.hostname
        if parsed.port:
            host = f"{host}:{parsed.port}"
        path = parsed.path or "/"
        date = formatdate(timeval=None, localtime=False, usegmt=True)
        origin = f"host: {host}\ndate: {date}\nGET {path} HTTP/1.1"
        digest = hmac.new(
            self.api_secret.encode(), origin.encode(), hashlib.sha256
        ).digest()
        signature = base64.b64encode(digest).decode()
        authorization_origin = (
            f'api_key="{self.api_key}", algorithm="hmac-sha256", '
            f'headers="host date request-line", signature="{signature}"'
        )
        authorization = base64.b64encode(authorization_origin.encode()).decode()
        return self.ws_url + "?" + urlencode(
            {"authorization": authorization, "date": date, "host": host}
        )

    def _request_payload(self, sequence: int, status: int, frame: bytes) -> dict:
        request = {
            "header": {"app_id": self.app_id, "status": status},
            "payload": {
                "audio": {
                    "encoding": "raw",
                    "sample_rate": 16000,
                    "channels": 1,
                    "bit_depth": 16,
                    "seq": sequence,
                    "status": status,
                    "audio": base64.b64encode(frame).decode(),
                }
            },
        }
        if status == 0:
            request["parameter"] = {
                "iat": {
                    "domain": "slm",
                    "language": "mul_cn",
                    "accent": "mandarin",
                    "eos": 6000,
                    "vinfo": 1,
                    "result": {
                        "encoding": "utf8",
                        "compress": "raw",
                        "format": "json",
                    },
                }
            }
        return request

    @staticmethod
    def _payload_text(payload: dict) -> str:
        parts = []
        for word_segment in payload.get("ws") or []:
            candidates = word_segment.get("cw") or []
            if candidates:
                parts.append(str(candidates[0].get("w") or ""))
        return "".join(parts)

    @classmethod
    def _merge_payloads(
        cls, payloads: list[dict]
    ) -> tuple[str, list[dict], list[dict]]:
        pieces: dict[int, str] = {}
        words_by_piece: dict[int, list[dict]] = {}
        for payload in payloads:
            sequence = int(payload.get("sn", len(pieces)))
            if payload.get("pgs") == "rpl":
                replacement_range = payload.get("rg") or []
                if len(replacement_range) == 2:
                    for index in range(
                        int(replacement_range[0]), int(replacement_range[1]) + 1
                    ):
                        pieces.pop(index, None)
                        words_by_piece.pop(index, None)
            pieces[sequence] = cls._payload_text(payload)
            current_words = []
            for word_segment in payload.get("ws") or []:
                candidates = word_segment.get("cw") or []
                if not candidates:
                    continue
                candidate = candidates[0]
                current_words.append(
                    {
                        "text": str(candidate.get("w") or ""),
                        "language": candidate.get("lg"),
                        "score": candidate.get("sc"),
                        "bg_frames": word_segment.get("bg"),
                    }
                )
            words_by_piece[sequence] = current_words
        ordered = sorted(pieces)
        words = [item for index in ordered for item in words_by_piece.get(index, [])]
        language_items = [
            item for item in words if item.get("language") or item.get("score") is not None
        ]
        return "".join(pieces[index] for index in ordered), words, language_items

    @staticmethod
    def _result(
        text: str,
        duration: float,
        words: list[dict],
        sid: str,
        payloads: list[dict],
        language_items: list[dict] | None = None,
    ) -> dict:
        language_items = language_items or []
        counts = Counter(
            str(item.get("language"))
            for item in language_items
            if item.get("language")
        )
        language = counts.most_common(1)[0][0] if counts else ""
        segments = []
        if text.strip():
            segments.append(
                {
                    "start": 0.0,
                    "end": duration,
                    "text": text,
                    "language": language,
                }
            )
        return {
            "provider": "xfyun_spark_mul_cn_iat",
            "asr_engine": "xfyun",
            "model": "slm/mul_cn",
            "language": language,
            "language_requested": "auto",
            "text": text,
            "segments": segments,
            "device": "cloud",
            "raw": {
                "sid": sid,
                "result_count": len(payloads),
                "words": words,
                "language_items": language_items,
                "payloads": payloads,
            },
        }
