from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def test_read_only_reporting_imports_do_not_load_langgraph() -> None:
    script = r'''
import builtins
import sys

original_import = builtins.__import__

def guarded_import(name, *args, **kwargs):
    if name == "langgraph" or name.startswith("langgraph."):
        raise AssertionError(f"unexpected langgraph import: {name}")
    return original_import(name, *args, **kwargs)

builtins.__import__ = guarded_import
from backend.reporting import ReportStore
from backend.reporting.account_overview import public_account_overview_projection

assert ReportStore.__name__ == "ReportStore"
assert callable(public_account_overview_projection)
assert "backend.reporting.graph" not in sys.modules
assert "backend.reporting.r2_graph" not in sys.modules
assert "backend.reporting.r3_graph" not in sys.modules
assert "backend.reporting.r31_graph" not in sys.modules
assert not any(name == "langgraph" or name.startswith("langgraph.") for name in sys.modules)
'''
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=REPOSITORY_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr


def test_graph_exports_resolve_original_modules_lazily(monkeypatch) -> None:
    import backend.reporting as reporting

    expected = {
        "ReportGenerationGraph": "backend.reporting.graph",
        "RiskFindingReportGraph": "backend.reporting.r2_graph",
        "AccountEntryReportGraph": "backend.reporting.r3_graph",
        "AccountOverviewReportGraph": "backend.reporting.r31_graph",
    }
    imports: list[str] = []

    def fake_import(module_name: str) -> SimpleNamespace:
        imports.append(module_name)
        export_name = next(
            name for name, expected_module in expected.items() if expected_module == module_name
        )
        return SimpleNamespace(**{export_name: f"resolved:{export_name}"})

    monkeypatch.setattr(reporting, "import_module", fake_import)
    for export_name, module_name in expected.items():
        reporting.__dict__.pop(export_name, None)
        assert getattr(reporting, export_name) == f"resolved:{export_name}"
        assert imports[-1] == module_name

    for export_name in expected:
        reporting.__dict__.pop(export_name, None)
    namespace: dict[str, object] = {}
    exec(
        "from backend.reporting import " + ", ".join(expected),
        namespace,
    )
    assert {
        name: namespace[name] for name in expected
    } == {name: f"resolved:{name}" for name in expected}


def test_accessing_graph_export_still_loads_graph_dependencies() -> None:
    script = r'''
import builtins

class GraphDependencyRequested(Exception):
    pass

original_import = builtins.__import__

def guarded_import(name, *args, **kwargs):
    if name == "langgraph" or name.startswith("langgraph."):
        raise GraphDependencyRequested(name)
    return original_import(name, *args, **kwargs)

builtins.__import__ = guarded_import
import backend.reporting as reporting

try:
    reporting.ReportGenerationGraph
except GraphDependencyRequested as exc:
    assert str(exc).startswith("langgraph")
else:
    raise AssertionError("Graph export did not load its original dependencies")
'''
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=REPOSITORY_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr


def test_unknown_reporting_export_still_raises_attribute_error() -> None:
    import backend.reporting as reporting

    try:
        getattr(reporting, "UnknownReportGraph")
    except AttributeError as exc:
        assert "UnknownReportGraph" in str(exc)
    else:
        raise AssertionError("unknown export unexpectedly resolved")
