from pathlib import Path

import pytest

from safety import (
    PodKillCampaignEvidenceError,
    load_podkill_campaign_evidence,
    write_podkill_campaign_evidence,
)
from tests.test_podkill_campaign_assessment import campaign_inputs, publish_trials


def test_persists_self_contained_pass_campaign_and_reuses_it(tmp_path: Path) -> None:
    change, pair, campaign = campaign_inputs(tmp_path)
    trials = publish_trials(tmp_path, change, pair)

    first = write_podkill_campaign_evidence(
        tmp_path / "evidence", campaign.path, [trial.path for trial in trials]
    )
    second = write_podkill_campaign_evidence(
        tmp_path / "evidence",
        campaign.path,
        [trial.path for trial in reversed(trials)],
    )
    loaded = load_podkill_campaign_evidence(first.path)

    assert first.status == "pass"
    assert first.reused is False
    assert second.artifact_id == first.artifact_id
    assert second.reused is True
    assert loaded.assessment.status == "pass"
    assert len(loaded.trials) == 3
    assert loaded.plan.campaign_id == campaign.campaign_id


def test_persists_complete_failed_campaign_without_hiding_failure(
    tmp_path: Path,
) -> None:
    change, pair, campaign = campaign_inputs(tmp_path)
    trials = publish_trials(tmp_path, change, pair, (False, True, False))

    artifact = write_podkill_campaign_evidence(
        tmp_path / "evidence", campaign.path, [trial.path for trial in trials]
    )

    assert artifact.status == "fail"
    assert load_podkill_campaign_evidence(artifact.path).assessment.failures


def test_refuses_to_publish_incomplete_campaign(tmp_path: Path) -> None:
    change, pair, campaign = campaign_inputs(tmp_path)
    trials = publish_trials(tmp_path, change, pair)
    output = tmp_path / "evidence"

    with pytest.raises(PodKillCampaignEvidenceError, match="got incomplete"):
        write_podkill_campaign_evidence(
            output, campaign.path, [trial.path for trial in trials[:2]]
        )

    assert not output.exists()


def test_rejects_tampered_embedded_trial(tmp_path: Path) -> None:
    change, pair, campaign = campaign_inputs(tmp_path)
    trials = publish_trials(tmp_path, change, pair)
    artifact = write_podkill_campaign_evidence(
        tmp_path / "evidence", campaign.path, [trial.path for trial in trials]
    )
    target = artifact.path / "trials" / trials[0].artifact_id / "result.json"
    target.write_bytes(target.read_bytes() + b"\n")

    with pytest.raises(PodKillCampaignEvidenceError, match="size changed"):
        load_podkill_campaign_evidence(artifact.path)
