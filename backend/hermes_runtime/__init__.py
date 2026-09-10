"""Product-facing boundary for the canonical Hermes Agent runtime."""

from .adapter import HermesRuntimeBinding, HermesRuntimeUnavailable

__all__ = ["HermesRuntimeBinding", "HermesRuntimeUnavailable"]
