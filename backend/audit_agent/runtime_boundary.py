from __future__ import annotations

from pathlib import Path


def runtime_data_directory_error(
    *, data_dir: Path, account_db_path: Path, outputs_dir: Path
) -> str:
    """Return an actionable error when M3 stores do not share one data root."""
    expected_data_dir = data_dir.expanduser().resolve()
    account_root = account_db_path.expanduser().resolve().parent
    expected_outputs_dir = (expected_data_dir / "outputs").resolve()
    actual_outputs_dir = outputs_dir.expanduser().resolve()
    if account_root != expected_data_dir:
        return (
            "M3 data directory mismatch: crawler accounts use "
            f"{account_root}, configured data directory is {expected_data_dir}"
        )
    if actual_outputs_dir != expected_outputs_dir:
        return (
            "M3 outputs directory mismatch: outputs use "
            f"{actual_outputs_dir}, expected {expected_outputs_dir}"
        )
    return ""
