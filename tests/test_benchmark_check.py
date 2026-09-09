from pathlib import Path

import pytest

from benchmarks import StepSummaryError, append_step_summary, render_step_summary
from tests.test_benchmark_pair import published_pair


def test_renders_a_github_step_summary_for_a_passing_pair(tmp_path: Path) -> None:
    from benchmarks import assess_counterbalanced_pair

    proposal, artifacts = published_pair(tmp_path)
    assessment = assess_counterbalanced_pair(artifacts[0].path, artifacts[1].path)

    summary = render_step_summary(assessment)

    assert "# KubeFit PR safety check" in summary
    assert "## ✅ PASS" in summary
    assert proposal.artifact_id in summary
    assert "opposite_orders" in summary


def test_appends_without_overwriting_an_existing_step_summary(tmp_path: Path) -> None:
    from benchmarks import assess_counterbalanced_pair

    _, artifacts = published_pair(tmp_path)
    assessment = assess_counterbalanced_pair(artifacts[0].path, artifacts[1].path)
    destination = tmp_path / "summary.md"
    destination.write_text("existing job output\n")

    append_step_summary(destination, assessment)

    assert destination.read_text().startswith("existing job output\n")
    assert "KubeFit PR safety check" in destination.read_text()


def test_rejects_a_symlinked_step_summary(tmp_path: Path) -> None:
    from benchmarks import assess_counterbalanced_pair

    _, artifacts = published_pair(tmp_path)
    assessment = assess_counterbalanced_pair(artifacts[0].path, artifacts[1].path)
    target = tmp_path / "target.md"
    target.write_text("do not change")
    link = tmp_path / "summary.md"
    link.symlink_to(target)

    with pytest.raises(StepSummaryError, match="non-symlinked"):
        append_step_summary(link, assessment)

    assert target.read_text() == "do not change"


def test_allows_a_parent_path_that_resolves_through_a_directory_symlink(
    tmp_path: Path,
) -> None:
    from benchmarks import assess_counterbalanced_pair

    _, artifacts = published_pair(tmp_path)
    assessment = assess_counterbalanced_pair(artifacts[0].path, artifacts[1].path)
    real_parent = tmp_path / "real"
    real_parent.mkdir()
    linked_parent = tmp_path / "linked"
    linked_parent.symlink_to(real_parent, target_is_directory=True)

    append_step_summary(linked_parent / "summary.md", assessment)

    assert "KubeFit PR safety check" in (real_parent / "summary.md").read_text()
