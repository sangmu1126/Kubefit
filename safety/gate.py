import html
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict

from benchmarks import CounterbalancedPairAssessment, assess_counterbalanced_pair
from gitops import load_proposal_bundle
from safety.change import DeploymentChange, inspect_deployment_change


class SafetyGateCheck(BaseModel):
    model_config = ConfigDict(frozen=True)

    code: str
    status: Literal["pass", "fail", "invalid"]
    reason: str


class SafetyGateResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    schema_version: Literal[1] = 1
    status: Literal["pass", "fail", "invalid"]
    proposal_id: str
    change: DeploymentChange
    benchmark: CounterbalancedPairAssessment
    checks: list[SafetyGateCheck]


def validate_proposal_change(
    proposal_path: Path,
    base_path: Path,
    candidate_path: Path,
    first_result_path: Path,
    second_result_path: Path,
) -> SafetyGateResult:
    """Bind exact proposal manifests to counterbalanced benchmark evidence."""
    proposal = load_proposal_bundle(proposal_path)
    change = inspect_deployment_change(
        base_path,
        candidate_path,
        namespace=proposal.target.namespace,
        deployment=proposal.target.deployment,
    )
    assessment = assess_counterbalanced_pair(first_result_path, second_result_path)
    proposal_matches = assessment.proposal_id == proposal.artifact_id
    base_matches = _same_content(base_path, proposal.before_source_manifest)
    candidate_matches = _same_content(candidate_path, proposal.after_source_manifest)
    checks = [
        SafetyGateCheck(
            code="change_scope",
            status="pass" if change.status == "supported" else "invalid",
            reason=(
                "the manifest contains only supported Deployment changes"
                if change.status == "supported"
                else f"the Deployment change scope is {change.status}"
            ),
        ),
        SafetyGateCheck(
            code="proposal_binding",
            status="pass" if proposal_matches else "invalid",
            reason=(
                "both benchmark trials reference the supplied proposal"
                if proposal_matches
                else "benchmark trials do not reference the supplied proposal"
            ),
        ),
        SafetyGateCheck(
            code="base_manifest_binding",
            status="pass" if base_matches else "invalid",
            reason=(
                "the base manifest is byte-identical to the proposal input"
                if base_matches
                else "the base manifest does not match the proposal input"
            ),
        ),
        SafetyGateCheck(
            code="candidate_manifest_binding",
            status="pass" if candidate_matches else "invalid",
            reason=(
                "the candidate manifest is byte-identical to the proposal output"
                if candidate_matches
                else "the candidate manifest does not match the proposal output"
            ),
        ),
        SafetyGateCheck(
            code="counterbalanced_benchmark",
            status=assessment.status,
            reason=f"the counterbalanced benchmark verdict is {assessment.status}",
        ),
    ]
    statuses = {check.status for check in checks}
    status: Literal["pass", "fail", "invalid"]
    if "invalid" in statuses:
        status = "invalid"
    elif "fail" in statuses:
        status = "fail"
    else:
        status = "pass"
    return SafetyGateResult(
        status=status,
        proposal_id=proposal.artifact_id,
        change=change,
        benchmark=assessment,
        checks=checks,
    )


def render_validation_summary(result: SafetyGateResult) -> str:
    symbol = {"pass": "✅", "fail": "❌", "invalid": "⚠️"}[result.status]
    lines = [
        "# KubeFit end-to-end safety gate",
        "",
        f"## {symbol} {result.status.upper()}",
        "",
        f"- Proposal: `{result.proposal_id}`",
        f"- Benchmark pair: `{result.benchmark.assessment_id}`",
        f"- Target: `{result.change.namespace}/{result.change.deployment}`",
        "",
        "### Evidence bindings",
        "",
        "| Check | Status | Reason |",
        "|---|---|---|",
    ]
    lines.extend(
        f"| `{_markdown_cell(check.code)}` | {check.status} | "
        f"{_markdown_cell(check.reason)} |"
        for check in result.checks
    )
    lines.extend(
        [
            "",
            "### Supported manifest changes",
            "",
            "| Path | Before | After |",
            "|---|---|---|",
        ]
    )
    lines.extend(
        f"| `{_markdown_cell(change.path)}` | `{_markdown_cell(change.before)}` | "
        f"`{_markdown_cell(change.after)}` |"
        for change in result.change.supported_changes
    )
    if result.change.unsupported_changes:
        lines.extend(["", "### Unsupported paths", ""])
        lines.extend(
            f"- `{_markdown_cell(change.path)}`"
            for change in result.change.unsupported_changes
        )
    return "\n".join(lines) + "\n"


def _same_content(first: Path, second: Path) -> bool:
    return first.read_bytes() == second.read_bytes()


def _markdown_cell(value: object) -> str:
    return (
        html.escape(str(value), quote=True)
        .replace("\\", "\\\\")
        .replace("|", r"\|")
        .replace("`", r"\`")
        .replace("\r", " ")
        .replace("\n", " ")
    )
