"""Lazy, version-fenced access to the installed Hermes Agent runtime.

The product imports this module without importing Hermes itself.  This keeps the
FastAPI compatibility surface importable in environments that intentionally do
not install the optional Hermes runtime (for example deterministic unit tests).
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from hashlib import sha256
from importlib import import_module
from importlib.resources import files
import os
from pathlib import Path
from threading import RLock
import tempfile
from typing import Any, Callable, Iterable, Iterator

import yaml


EXPECTED_HERMES_VERSION = "0.20.4"
PRODUCT_PROMPT_RESOURCE = "account_activity_prompt.txt"
PRODUCT_PROMPT_SHA256 = (
    "3bd437c4bcd970fbb31737a4ad3059bb0f12395a03af10c50ddf42e696719cd3"
)
PRODUCT_TOOLSET = "investigation"
PRODUCT_PLUGIN = "xhs-investigation"
PRODUCT_PROVIDER = "alibaba"
PRODUCT_MODEL = "qwen3.7-plus"
CANONICAL_API_MAX_RETRIES = 1

_LEGACY_MODE_FLAGS = (
    "HERMES_INVESTIGATION_TASK_MODE",
    "HERMES_INVESTIGATION_REPORT_TASK_MODE",
    "HERMES_INVESTIGATION_REAL_REPORT_MODE",
)
_ACCOUNT_ACTIVITY_MODE_FLAG = "HERMES_INVESTIGATION_ACCOUNT_ACTIVITY_MODE"
_CREATION_MODE_FLAG = "HERMES_INVESTIGATION_CREATION_MODE"
_PRODUCT_MODE_EXECUTION_LOCK = RLock()


def session_runtime_home(root: Path, session_id: str) -> Path:
    """Keep Hermes config/log files private; durable tool ledgers stay put."""
    return root / "sessions" / sha256(session_id.encode("utf-8")).hexdigest()


class HermesRuntimeUnavailable(RuntimeError):
    """Raised when the pinned Hermes runtime is absent or has the wrong version."""


def _load_module(name: str) -> Any:
    try:
        return import_module(name)
    except ImportError as exc:
        raise HermesRuntimeUnavailable(
            "Hermes Agent 0.20.4 is not installed; install requirements-hermes.txt"
        ) from exc


@dataclass(frozen=True)
class HermesRuntimeBinding:
    """Product adapter exposing only stable Hermes public entry points."""

    expected_version: str = EXPECTED_HERMES_VERSION

    def product_system_prompt(self, product_mode: str = "account-activity") -> str:
        """Load and hash-fence the canonical M2.2 product System Prompt."""

        if product_mode == "pass-report":
            from hermes_m0.pass_support import PASS_SYSTEM_PROMPT
            return PASS_SYSTEM_PROMPT
        prompt = (
            files("hermes_m0")
            .joinpath(PRODUCT_PROMPT_RESOURCE)
            .read_text(encoding="utf-8")
        )
        actual_hash = sha256(prompt.encode("utf-8")).hexdigest()
        if actual_hash != PRODUCT_PROMPT_SHA256:
            raise HermesRuntimeUnavailable(
                "canonical Hermes product prompt failed its SHA-256 fence"
            )
        return prompt.strip()

    @staticmethod
    def activate_product_mode(mode: str = "account-activity") -> None:
        """Select the sole product catalog and reject historical optional modes."""

        if mode not in {"account-activity", "creation", "pass-report"}:
            raise HermesRuntimeUnavailable(f"unsupported Hermes product mode: {mode}")
        enabled_legacy = [
            name for name in _LEGACY_MODE_FLAGS if os.environ.get(name) == "1"
        ]
        if mode == "account-activity" and enabled_legacy:
            raise HermesRuntimeUnavailable(
                "validation-only Hermes mode cannot be enabled by the product adapter: "
                + ", ".join(enabled_legacy)
            )
        for name in (
            *_LEGACY_MODE_FLAGS,
            _ACCOUNT_ACTIVITY_MODE_FLAG,
            _CREATION_MODE_FLAG,
        ):
            os.environ[name] = "0"
        selected_flag = (
            _CREATION_MODE_FLAG
            if mode == "creation"
            else _ACCOUNT_ACTIVITY_MODE_FLAG
        )
        os.environ[selected_flag] = "1"
        os.environ["HERMES_INVESTIGATION_PASS_REPORT"] = "1" if mode == "pass-report" else "0"
        os.environ["HERMES_ENABLE_PROJECT_PLUGINS"] = "1"

    def configure_product_home(self, home: Path) -> None:
        """Use an isolated Hermes home and enable only the adapted product plugin."""

        product_home = home.expanduser().resolve()
        product_home.mkdir(parents=True, exist_ok=True)
        os.environ["HERMES_HOME"] = str(product_home)
        config_path = product_home / "config.yaml"
        if config_path.exists():
            raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
            if not isinstance(raw, dict):
                raise HermesRuntimeUnavailable("Hermes product config must be a mapping")
        else:
            raw = {}
        changed = False
        agent_config = raw.setdefault("agent", {})
        if not isinstance(agent_config, dict):
            raise HermesRuntimeUnavailable("Hermes agent config must be a mapping")
        if agent_config.get("api_max_retries") != CANONICAL_API_MAX_RETRIES:
            agent_config["api_max_retries"] = CANONICAL_API_MAX_RETRIES
            changed = True
        plugins = raw.setdefault("plugins", {})
        if not isinstance(plugins, dict):
            raise HermesRuntimeUnavailable("Hermes plugins config must be a mapping")
        enabled = plugins.setdefault("enabled", [])
        if not isinstance(enabled, list):
            raise HermesRuntimeUnavailable("Hermes plugins.enabled must be a list")
        if PRODUCT_PLUGIN not in enabled:
            enabled.append(PRODUCT_PLUGIN)
            changed = True
        if changed:
            fd, temporary_name = tempfile.mkstemp(
                prefix=".config.", suffix=".yaml", dir=product_home
            )
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    yaml.safe_dump(raw, handle, allow_unicode=True, sort_keys=False)
                os.replace(temporary_name, config_path)
            finally:
                if os.path.exists(temporary_name):
                    os.unlink(temporary_name)

    def _verify_version(self) -> Any:
        cli = _load_module("hermes_cli")
        actual = str(getattr(cli, "__version__", ""))
        if actual != self.expected_version:
            raise HermesRuntimeUnavailable(
                f"unsupported Hermes Agent version {actual!r}; "
                f"expected {self.expected_version}"
            )
        return cli

    def discover_plugins(
        self,
        *,
        force: bool = False,
        product_mode: str = "account-activity",
    ) -> None:
        """Load Hermes plugins using its public, idempotent discovery API."""

        with _PRODUCT_MODE_EXECUTION_LOCK:
            self.activate_product_mode(product_mode)
            self._verify_version()
            plugins = _load_module("hermes_cli.plugins")
            plugins.discover_plugins(force=force)

    @contextmanager
    def product_mode_execution(
        self, home: Path, *, product_mode: str
    ) -> Iterator[None]:
        """Pin Hermes' process-global registry to one product mode for a whole Turn."""

        with _PRODUCT_MODE_EXECUTION_LOCK:
            self.configure_product_home(home)
            self.discover_plugins(force=True, product_mode=product_mode)
            if product_mode == "creation":
                from .creation_discovery import creation_discovery_guidance
                with creation_discovery_guidance(_load_module("tools.tool_search")):
                    yield
            else:
                yield

    def tool_definitions(
        self,
        *,
        enabled_toolsets: list[str] | None = None,
        product_mode: str = "account-activity",
    ) -> list[dict[str, Any]]:
        """Return OpenAI-format tool definitions after plugin discovery."""

        self.discover_plugins(product_mode=product_mode)
        model_tools = _load_module("model_tools")
        return list(
            model_tools.get_tool_definitions(
                enabled_toolsets=enabled_toolsets,
                quiet_mode=True,
                skip_tool_search_assembly=True,
            )
        )

    def agent_factory(
        self, *, product_mode: str = "account-activity"
    ) -> Callable[..., Any]:
        """Return the public Hermes ``AIAgent`` constructor without instantiation."""

        self.discover_plugins(product_mode=product_mode)
        run_agent = _load_module("run_agent")
        return run_agent.AIAgent

    def create_agent(
        self,
        *,
        session_id: str,
        agent_factory: Callable[..., Any] | None = None,
        product_mode: str = "account-activity",
        **overrides: Any,
    ) -> Any:
        """Construct the formal product AIAgent without making a Provider call."""

        self.activate_product_mode(product_mode)
        constructor = agent_factory or self.agent_factory(product_mode=product_mode)
        options = {
            "provider": PRODUCT_PROVIDER,
            "model": PRODUCT_MODEL,
            "session_id": session_id,
            "session_db": None,
            "enabled_toolsets": [PRODUCT_TOOLSET],
            "api_mode": "chat_completions",
            "max_iterations": 12,
            "quiet_mode": True,
            "skip_context_files": True,
            "skip_memory": True,
            "skip_background_review": True,
        }
        options.update(overrides)
        agent = constructor(**options)
        if self.expected_version != EXPECTED_HERMES_VERSION:
            self._close_failed_agent(agent)
            raise HermesRuntimeUnavailable(
                "canonical retry fence is only defined for Hermes Agent 0.20.4"
            )
        if not hasattr(agent, "_api_max_retries"):
            self._close_failed_agent(agent)
            raise HermesRuntimeUnavailable(
                "Hermes Agent 0.20.4 does not expose the canonical retry control"
            )
        try:
            agent._api_max_retries = CANONICAL_API_MAX_RETRIES
        except Exception as exc:
            self._close_failed_agent(agent)
            raise HermesRuntimeUnavailable(
                "Hermes Agent 0.20.4 canonical retry control could not be set"
            ) from exc
        if agent._api_max_retries != CANONICAL_API_MAX_RETRIES:
            self._close_failed_agent(agent)
            raise HermesRuntimeUnavailable(
                "Hermes Agent 0.20.4 canonical retry control did not take effect"
            )
        return agent

    @staticmethod
    def _close_failed_agent(agent: Any) -> None:
        close = getattr(agent, "close", None)
        if callable(close):
            close()

    def bind_published_report_session(
        self,
        *,
        session_id: str,
        database_path: Path,
        report_version_id: str,
        content_hash: str,
        snapshot_hash: str,
        ledger_path: Path,
        additional_report_contexts: Iterable[Any] = (),
    ) -> None:
        """Bind canonical read-only M1/M2.2 tools to one published ReportVersion."""

        self.activate_product_mode()
        report_path = database_path.expanduser().resolve(strict=True)
        additional_report_contexts = tuple(additional_report_contexts)
        from .report_snapshot import published_report_snapshot

        report_path, database_hash = published_report_snapshot(
            report_path, ledger_path=ledger_path, session_id=session_id,
            identities=((report_version_id, content_hash, snapshot_hash), *(
                (item.report_version_id, item.content_hash, item.snapshot_hash)
                for item in additional_report_contexts
            )),
        )
        runtime = import_module("hermes_m0.runtime")
        additional_sources = tuple(
            runtime.AuthorizedReportSource(
                database_path=report_path,
                report_version_id=item.report_version_id,
                database_sha256=database_hash,
                content_hash=item.content_hash,
                snapshot_hash=item.snapshot_hash,
            )
            for item in additional_report_contexts
        )
        expected_authorized_sources = (
            (report_version_id, snapshot_hash, content_hash, database_hash),
            *(
                (
                    item.report_version_id,
                    item.snapshot_hash,
                    item.content_hash,
                    database_hash,
                )
                for item in additional_report_contexts
            ),
        )
        existing = runtime.report_runtime_binding_for_session(session_id)
        if existing is not None:
            expected_identity = (
                report_version_id,
                snapshot_hash,
                content_hash,
            )
            actual_identity = (
                existing.report_version_id,
                existing.snapshot_hash,
                existing.content_hash,
            )
            if actual_identity != expected_identity:
                raise RuntimeError(
                    "product Session cannot be rebound to a different ReportVersion "
                    "or FrozenSnapshot"
                )
            if existing.authorized_report_sources == expected_authorized_sources:
                return
            runtime.release_report_task_session(session_id)
        service = runtime.configure_real_report_runtime(
            report_path,
            report_version_id=report_version_id,
            expected_database_sha256=database_hash,
            expected_content_hash=content_hash,
            expected_snapshot_hash=snapshot_hash,
            ledger_path=ledger_path,
            account_corpus_path=import_module(
                "hermes_m0.account_activity_repository"
            ).DEFAULT_ACCOUNT_CORPUS_PATH,
            additional_account_report_sources=additional_sources,
        )
        runtime.bind_report_task_session(session_id, service=service)

    @staticmethod
    def is_published_report_session_bound(session_id: str) -> bool:
        runtime = import_module("hermes_m0.runtime")
        return runtime.report_runtime_binding_for_session(session_id) is not None

    @staticmethod
    def release_published_report_session(session_id: str) -> bool:
        runtime = import_module("hermes_m0.runtime")
        return bool(runtime.release_report_task_session(session_id))
