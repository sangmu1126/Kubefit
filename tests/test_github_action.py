from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def test_action_exposes_only_the_required_evidence_paths() -> None:
    metadata = yaml.safe_load((ROOT / "action.yml").read_text())

    assert metadata["runs"]["using"] == "docker"
    assert metadata["runs"]["image"] == "Dockerfile.action"
    assert "entrypoint" not in metadata["runs"]
    assert set(metadata["inputs"]) == {
        "proposal",
        "base",
        "candidate",
        "first-result",
        "second-result",
    }
    assert all(item["required"] is True for item in metadata["inputs"].values())


def test_action_passes_inputs_as_exec_arguments_without_a_shell() -> None:
    metadata = yaml.safe_load((ROOT / "action.yml").read_text())

    assert metadata["runs"]["args"] == [
        "validate",
        "--proposal",
        "${{ inputs.proposal }}",
        "--base",
        "${{ inputs.base }}",
        "--candidate",
        "${{ inputs.candidate }}",
        "--first",
        "${{ inputs.first-result }}",
        "--second",
        "${{ inputs.second-result }}",
    ]


def test_production_and_action_images_contain_the_safety_package() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text()
    action_dockerfile = (ROOT / "Dockerfile.action").read_text()

    assert "COPY safety ./safety" in dockerfile
    assert "COPY safety ./safety" in action_dockerfile
    assert 'ENTRYPOINT ["kubefit"]' in action_dockerfile
    assert "dashboard" not in action_dockerfile
