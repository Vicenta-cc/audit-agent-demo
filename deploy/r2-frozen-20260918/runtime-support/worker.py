"""Run the investigation worker inside the same pinned candidate environment."""
import serve  # noqa: F401,E402 - configures the isolated environment before backend imports

from backend.investigation_creation.worker import build_worker


if __name__ == "__main__":
    build_worker().run_forever(poll_seconds=1)
