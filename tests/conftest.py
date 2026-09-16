"""Isolate application globals before any test module imports a default store."""
import os
from pathlib import Path
from tempfile import TemporaryDirectory


_application_storage = TemporaryDirectory(prefix="audit-pytest-storage-")
_storage_root = Path(_application_storage.name)
for _variable, _directory in {
    "XHS_AUDIT_DATA_DIR": "data",
    "XHS_AUDIT_OUTPUTS_DIR": "outputs",
    "HERMES_HOME": "hermes",
}.items():
    os.environ[_variable] = str(_storage_root / _directory)


import pytest


@pytest.fixture(autouse=True)
def isolated_scheduler_environment(tmp_path, monkeypatch):
    monkeypatch.setenv('MEDIACRAWLER_REQUEST_SCHEDULER_DB', str(tmp_path / 'request_scheduler.sqlite3'))
