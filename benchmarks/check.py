from pathlib import Path

from benchmarks.pair import CounterbalancedPairAssessment


class StepSummaryError(RuntimeError):
    """Raised when a GitHub Actions step summary cannot be written safely."""


def render_step_summary(assessment: CounterbalancedPairAssessment) -> str:
    """Render a counterbalanced assessment as a GitHub Actions step summary."""
    symbol = {"pass": "✅", "fail": "❌", "invalid": "⚠️"}[assessment.status]
    lines = [
        "# KubeFit PR safety check",
        "",
        f"## {symbol} {assessment.status.upper()}",
        "",
        f"- Assessment: `{assessment.assessment_id}`",
        f"- Proposal: `{assessment.proposal_id or 'unresolved'}`",
        "",
        "### Trials",
        "",
        "| Benchmark | Order | Verdict |",
        "|---|---|---|",
    ]
    lines.extend(
        "| `{}` | `{}` | {} |".format(
            _markdown_cell(trial.benchmark_id),
            _markdown_cell(trial.measurement_order or "unknown"),
            trial.verdict_status,
        )
        for trial in assessment.trials
    )
    lines.extend(["", "### Gate checks", "", "| Check | Status | Reason |", "|---|---|---|"])
    lines.extend(
        f"| `{_markdown_cell(check.code)}` | {check.status} | "
        f"{_markdown_cell(check.reason)} |"
        for check in assessment.checks
    )
    if assessment.failures:
        lines.extend(["", "### Failures", ""])
        lines.extend(f"- {_markdown_cell(reason)}" for reason in assessment.failures)
    if assessment.invalid_reasons:
        lines.extend(["", "### Invalid evidence", ""])
        lines.extend(
            f"- {_markdown_cell(reason)}" for reason in assessment.invalid_reasons
        )
    if assessment.warnings:
        lines.extend(["", "### Limitations", ""])
        lines.extend(f"- {_markdown_cell(warning)}" for warning in assessment.warnings)
    return "\n".join(lines) + "\n"


def append_step_summary(
    path: Path,
    assessment: CounterbalancedPairAssessment,
) -> None:
    """Append a report to an existing GitHub summary file without following symlinks."""
    append_markdown_summary(path, render_step_summary(assessment))


def append_markdown_summary(path: Path, content: str) -> None:
    """Append pre-rendered Markdown under the same path-safety boundary."""
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise StepSummaryError("step summary path must be a regular, non-symlinked file")
    if not path.parent.is_dir():
        raise StepSummaryError("step summary parent must be an existing directory")
    try:
        with path.open("a", encoding="utf-8") as stream:
            stream.write(content)
    except OSError as exc:
        raise StepSummaryError("could not write the GitHub step summary") from exc


def _markdown_cell(value: str) -> str:
    return value.replace("\\", "\\\\").replace("|", r"\|").replace("\r", " ").replace("\n", " ")
