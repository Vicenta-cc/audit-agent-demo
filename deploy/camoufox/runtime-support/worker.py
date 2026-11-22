"""Pinned investigation worker using the same release manifest as the API."""

from serve import configure

if __name__ == "__main__":
    configure()
    from backend.investigation_creation.worker import main

    main()
