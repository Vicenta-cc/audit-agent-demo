"""Lazy, version-fenced access to the installed Hermes Agent runtime.

The product imports this module without importing Hermes itself.  This keeps the
FastAPI compatibility surface importable in environments that intentionally do
not install the optional Hermes runtime (for example deterministic unit tests).
"""

from __future__ import annotations

from dataclasses import dataclass
from importlib import import_module
from typing import Any, Callable


EXPECTED_HERMES_VERSION = "0.20.4"


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

    def _verify_version(self) -> Any:
        cli = _load_module("hermes_cli")
        actual = str(getattr(cli, "__version__", ""))
        if actual != self.expected_version:
            raise HermesRuntimeUnavailable(
                f"unsupported Hermes Agent version {actual!r}; "
                f"expected {self.expected_version}"
            )
        return cli

    def discover_plugins(self) -> None:
        """Load Hermes plugins using its public, idempotent discovery API."""

        self._verify_version()
        plugins = _load_module("hermes_cli.plugins")
        plugins.discover_plugins()

    def tool_definitions(self, *, enabled_toolsets: list[str] | None = None) -> list[dict[str, Any]]:
        """Return OpenAI-format tool definitions after plugin discovery."""

        self.discover_plugins()
        model_tools = _load_module("model_tools")
        return list(
            model_tools.get_tool_definitions(
                enabled_toolsets=enabled_toolsets,
                quiet_mode=True,
            )
        )

    def agent_factory(self) -> Callable[..., Any]:
        """Return the public Hermes ``AIAgent`` constructor without instantiation."""

        self.discover_plugins()
        run_agent = _load_module("run_agent")
        return run_agent.AIAgent

