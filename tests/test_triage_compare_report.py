import json
import tempfile
from pathlib import Path

from scripts.triage_compare_report import summarize, load_selections


def test_summarize_by_strategy_and_lift():
    selections = [("triage", "a"), ("triage", "b"), ("rank1", "c"), ("rank1", "d"), ("triage", "e")]
    outcomes = {"a": "reject", "b": "pass", "c": "pass", "d": "reject", "e": "review"}
    report = summarize(selections, outcomes)
    assert report["by_strategy"]["triage"] == {"selected": 3, "reject": 1, "review": 1, "reject_rate": round(1 / 3, 4)}
    assert report["by_strategy"]["rank1"]["reject_rate"] == 0.5
    assert report["lift"] == round((1 / 3) / 0.5, 4)


def test_load_selections_with_rotation():
    """Test that load_selections uses rglob to find candidates.json in nested directories including rotation paths."""
    with tempfile.TemporaryDirectory() as tmpdir:
        outputs_dir = Path(tmpdir)
        job_id = "test-job"

        # Create standard candidates path
        standard_path = outputs_dir / job_id / "crawler" / "candidates" / "01-a"
        standard_path.mkdir(parents=True)
        (standard_path / "candidates.json").write_text(
            json.dumps({"strategy": "rank1", "selected": "content-1"}),
            encoding="utf-8"
        )

        # Create rotation candidates path
        rotation_path = outputs_dir / job_id / "crawler" / "rotation-x" / "candidates" / "02-b"
        rotation_path.mkdir(parents=True)
        (rotation_path / "candidates.json").write_text(
            json.dumps({"strategy": "triage", "selected": "content-2"}),
            encoding="utf-8"
        )

        # Create a file with selected=None that should be skipped
        skip_path = outputs_dir / job_id / "crawler" / "candidates" / "03-c"
        skip_path.mkdir(parents=True)
        (skip_path / "candidates.json").write_text(
            json.dumps({"strategy": "rank1", "selected": None}),
            encoding="utf-8"
        )

        selections = load_selections(outputs_dir, job_id)

        # Should find both the standard and rotation candidates, but skip the one with selected=None
        assert len(selections) == 2
        assert ("rank1", "content-1") in selections
        assert ("triage", "content-2") in selections
