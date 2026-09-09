from pathlib import Path

from gitops import load_proposal_bundle
from safety import render_validation_summary, validate_proposal_change
from tests.test_benchmark_pair import published_pair


def test_passes_only_when_manifests_proposal_and_pair_are_bound(tmp_path: Path) -> None:
    proposal_artifact, results = published_pair(tmp_path)
    proposal = load_proposal_bundle(proposal_artifact.path)

    gate = validate_proposal_change(
        proposal.path,
        proposal.before_source_manifest,
        proposal.after_source_manifest,
        results[0].path,
        results[1].path,
    )

    assert gate.status == "pass"
    assert gate.proposal_id == proposal.artifact_id
    assert gate.benchmark.status == "pass"
    assert gate.change.status == "supported"
    assert {check.status for check in gate.checks} == {"pass"}


def test_invalidates_semantically_equal_but_unbound_candidate(tmp_path: Path) -> None:
    proposal_artifact, results = published_pair(tmp_path)
    proposal = load_proposal_bundle(proposal_artifact.path)
    candidate = tmp_path / "candidate.yaml"
    candidate.write_bytes(proposal.after_source_manifest.read_bytes() + b"\n")

    gate = validate_proposal_change(
        proposal.path,
        proposal.before_source_manifest,
        candidate,
        results[0].path,
        results[1].path,
    )

    assert gate.change.status == "supported"
    assert gate.status == "invalid"
    assert next(
        check.status
        for check in gate.checks
        if check.code == "candidate_manifest_binding"
    ) == "invalid"


def test_renders_end_to_end_evidence_bindings(tmp_path: Path) -> None:
    proposal_artifact, results = published_pair(tmp_path)
    proposal = load_proposal_bundle(proposal_artifact.path)
    gate = validate_proposal_change(
        proposal.path,
        proposal.before_source_manifest,
        proposal.after_source_manifest,
        results[0].path,
        results[1].path,
    )

    summary = render_validation_summary(gate)

    assert "# KubeFit end-to-end safety gate" in summary
    assert "## ✅ PASS" in summary
    assert "candidate_manifest_binding" in summary
    assert gate.benchmark.assessment_id in summary


def test_summary_escapes_manifest_values_as_untrusted_markdown(tmp_path: Path) -> None:
    proposal_artifact, results = published_pair(tmp_path)
    proposal = load_proposal_bundle(proposal_artifact.path)
    gate = validate_proposal_change(
        proposal.path,
        proposal.before_source_manifest,
        proposal.after_source_manifest,
        results[0].path,
        results[1].path,
    )
    changed = gate.change.supported_changes[0].model_copy(
        update={"after": "</td>|`injected`"}
    )
    unsafe_gate = gate.model_copy(
        update={
            "change": gate.change.model_copy(
                update={"supported_changes": [changed]}
            )
        }
    )

    summary = render_validation_summary(unsafe_gate)

    assert "</td>" not in summary
    assert "&lt;/td&gt;" in summary
    assert r"\|\`injected\`" in summary
