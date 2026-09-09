from pathlib import Path

import pytest

from safety import ChangeBundleError, load_change_bundle, write_change_bundle
from tests.test_deployment_change import BASE


def _inputs(tmp_path: Path) -> tuple[Path, Path]:
    base = tmp_path / "base.yaml"
    candidate = tmp_path / "candidate.yaml"
    base.write_text(BASE)
    candidate.write_text(BASE.replace("example/api:v1", "example/api:v2"))
    return base, candidate


def test_writes_and_replays_a_content_addressed_change_bundle(tmp_path: Path) -> None:
    base, candidate = _inputs(tmp_path)

    artifact = write_change_bundle(
        tmp_path / "changes",
        base,
        candidate,
        namespace="demo",
        deployment="api",
    )
    loaded = load_change_bundle(artifact.path)

    assert artifact.artifact_id.startswith("change-")
    assert artifact.reused is False
    assert loaded.artifact_id == artifact.artifact_id
    assert loaded.change.status == "supported"
    assert loaded.base_manifest.read_bytes() == base.read_bytes()
    assert loaded.candidate_manifest.read_bytes() == candidate.read_bytes()


def test_reuses_identical_change_content(tmp_path: Path) -> None:
    base, candidate = _inputs(tmp_path)
    output = tmp_path / "changes"

    first = write_change_bundle(
        output, base, candidate, namespace="demo", deployment="api"
    )
    second = write_change_bundle(
        output, base, candidate, namespace="demo", deployment="api"
    )

    assert second.artifact_id == first.artifact_id
    assert second.reused is True


def test_rejects_tampered_bundle_payload(tmp_path: Path) -> None:
    base, candidate = _inputs(tmp_path)
    artifact = write_change_bundle(
        tmp_path / "changes",
        base,
        candidate,
        namespace="demo",
        deployment="api",
    )
    artifact_candidate = artifact.path / "manifests/candidate.yaml"
    artifact_candidate.chmod(0o700)
    artifact_candidate.write_text(BASE)

    with pytest.raises(ChangeBundleError, match="payload size changed|payload digest changed"):
        load_change_bundle(artifact.path)


def test_rejects_unchanged_input_instead_of_publishing_evidence(tmp_path: Path) -> None:
    base = tmp_path / "base.yaml"
    candidate = tmp_path / "candidate.yaml"
    base.write_text(BASE)
    candidate.write_text(BASE)

    with pytest.raises(ChangeBundleError, match="unchanged"):
        write_change_bundle(
            tmp_path / "changes",
            base,
            candidate,
            namespace="demo",
            deployment="api",
        )
