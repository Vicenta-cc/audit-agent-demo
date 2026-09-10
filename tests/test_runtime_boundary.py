from pathlib import Path

from backend.audit_agent.runtime_boundary import runtime_data_directory_error


def test_m3_data_directory_mismatch_is_explicit(tmp_path: Path):
    configured = tmp_path / "m3-data"
    legacy = tmp_path / "legacy-8000-data"
    error = runtime_data_directory_error(
        data_dir=configured,
        account_db_path=legacy / "audit_index.sqlite3",
        outputs_dir=configured / "outputs",
    )
    assert "M3 data directory mismatch" in error
    assert str(legacy.resolve()) in error


def test_m3_data_directory_boundary_accepts_one_stable_root(tmp_path: Path):
    configured = tmp_path / "m3-data"
    assert runtime_data_directory_error(
        data_dir=configured,
        account_db_path=configured / "audit_index.sqlite3",
        outputs_dir=configured / "outputs",
    ) == ""
