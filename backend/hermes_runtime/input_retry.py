"""Bounded retries of an unchanged Qwen request rejected at input inspection.

Retry the provider call only, never the Agent turn or a business tool. Other
errors, refusals and partially delivered responses retain their existing path.
"""
from functools import wraps
import json
import logging
import time


logger = logging.getLogger(__name__)
MAX_INPUT_ATTEMPTS = 5  # Includes the first request.


def is_input_inspection_rejected(exc):
    if getattr(exc, "status_code", None) not in (None, 400):
        return False
    body = getattr(exc, "body", None)
    if isinstance(body, str):
        try:
            body = json.loads(body.removeprefix("data:").strip())
        except ValueError:
            return False
    if not isinstance(body, dict):
        return False
    error = body.get("error", body)
    if not isinstance(error, dict):
        return False
    code = str(error.get("code", "")).lower().replace("_", "")
    message = str(error.get("message", "")).lower()
    return code == "datainspectionfailed" and "input" in message and "output" not in message


def retry_input_request(call, *, cancelled=lambda: False):
    for attempt in range(1, MAX_INPUT_ATTEMPTS + 1):
        try:
            result = call()
        except Exception as exc:
            if not is_input_inspection_rejected(exc):
                raise
            logger.warning(
                "qwen_input_inspection attempt=%s/%s provider_request_id=%s",
                attempt, MAX_INPUT_ATTEMPTS, getattr(exc, "request_id", None),
            )
            if attempt == MAX_INPUT_ATTEMPTS or cancelled():
                raise
            time.sleep(0.5)
            if cancelled():
                raise
        else:
            if attempt > 1:
                logger.info("qwen_input_inspection recovered_on_attempt=%s", attempt)
            return result


def install_creation_input_retries(agent):
    """Instance-only adapter for the version-fenced Hermes creation Agent."""
    if getattr(agent, "_creation_input_retries_installed", False):
        return

    def wrap(original):
        @wraps(original)
        def request(*args, **kwargs):
            # A streaming transport may fall back to the non-streaming method.
            # Both share one budget; nesting must not multiply five into 25.
            if getattr(agent, "_creation_input_retry_active", False):
                return original(*args, **kwargs)
            agent._creation_input_retry_active = True
            try:
                return retry_input_request(
                    lambda: original(*args, **kwargs),
                    cancelled=lambda: bool(
                        getattr(agent, "_interrupt_requested", False)
                        or getattr(agent, "_current_streamed_assistant_text", "")
                    ),
                )
            finally:
                agent._creation_input_retry_active = False
        return request

    for name in ("_interruptible_streaming_api_call", "_interruptible_api_call"):
        original = getattr(agent, name, None)
        if callable(original):
            setattr(agent, name, wrap(original))
    agent._creation_input_retries_installed = True
