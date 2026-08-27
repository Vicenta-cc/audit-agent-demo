"""Lazy, version-fenced access to the installed Hermes Agent runtime.

The product imports this module without importing Hermes itself.  This keeps the
FastAPI compatibility surface importable in environments that intentionally do
not install the optional Hermes runtime (for example deterministic unit tests).
"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from importlib import import_module
from importlib.resources import files
import os
from pathlib import Path
import tempfile
from typing import Any, Callable, Iterable

import yaml


EXPECTED_HERMES_VERSION = "0.20.4"
PRODUCT_PROMPT_RESOURCE = "account_activity_prompt.txt"
PRODUCT_PROMPT_SHA256 = (
    "62768caf1e59d4783c8c8ca362e30dd5bbee05f232898631bd7d31512a360c9e"
)
PRODUCT_TOOLSET = "investigation"
PRODUCT_PLUGIN = "xhs-investigation"
PRODUCT_PROVIDER = "alibaba"
PRODUCT_MODEL = "qwen3.7-plus"

_LEGACY_MODE_FLAGS = (
    "HERMES_INVESTIGATION_TASK_MODE",
    "HERMES_INVESTIGATION_REPORT_TASK_MODE",
    "HERMES_INVESTIGATION_REAL_REPORT_MODE",
)


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

    def product_system_prompt(self) -> str:
        """Load and hash-fence the canonical M2.2 product System Prompt."""

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
    def activate_product_mode() -> None:
        """Select the sole product catalog and reject historical optional modes."""

        enabled_legacy = [
            name for name in _LEGACY_MODE_FLAGS if os.environ.get(name) == "1"
        ]
        if enabled_legacy:
            raise HermesRuntimeUnavailable(
                "validation-only Hermes mode cannot be enabled by the product adapter: "
                + ", ".join(enabled_legacy)
            )
        os.environ["HERMES_INVESTIGATION_ACCOUNT_ACTIVITY_MODE"] = "1"
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
        plugins = raw.setdefault("plugins", {})
        if not isinstance(plugins, dict):
            raise HermesRuntimeUnavailable("Hermes plugins config must be a mapping")
        enabled = plugins.setdefault("enabled", [])
        if not isinstance(enabled, list):
            raise HermesRuntimeUnavailable("Hermes plugins.enabled must be a list")
        if PRODUCT_PLUGIN not in enabled:
            enabled.append(PRODUCT_PLUGIN)
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

    def discover_plugins(self, *, force: bool = False) -> None:
        """Load Hermes plugins using its public, idempotent discovery API."""

        self.activate_product_mode()
        self._verify_version()
        plugins = _load_module("hermes_cli.plugins")
        plugins.discover_plugins(force=force)

    def tool_definitions(self, *, enabled_toolsets: list[str] | None = None) -> list[dict[str, Any]]:
        """Return OpenAI-format tool definitions after plugin discovery."""

        self.discover_plugins()
        model_tools = _load_module("model_tools")
        return list(
            model_tools.get_tool_definitions(
                enabled_toolsets=enabled_toolsets,
                quiet_mode=True,
                skip_tool_search_assembly=True,
            )
        )

    def agent_factory(self) -> Callable[..., Any]:
        """Return the public Hermes ``AIAgent`` constructor without instantiation."""

        self.discover_plugins()
        run_agent = _load_module("run_agent")
        return run_agent.AIAgent

    def create_agent(
        self,
        *,
        session_id: str,
        agent_factory: Callable[..., Any] | None = None,
        **overrides: Any,
    ) -> Any:
        """Construct the formal product AIAgent without making a Provider call."""

        constructor = agent_factory or self.agent_factory()
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
        return constructor(**options)

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
        database_hash = sha256(report_path.read_bytes()).hexdigest()
        runtime = import_module("hermes_m0.runtime")
        additional_report_contexts = tuple(additional_report_contexts)
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
                database_hash,
            )
            actual_identity = (
                existing.report_version_id,
                existing.snapshot_hash,
                existing.content_hash,
                existing.database_sha256,
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
