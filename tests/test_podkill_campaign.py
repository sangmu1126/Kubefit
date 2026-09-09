from pathlib import Path

import pytest

from safety import (
    PodKillCampaignError,
    create_podkill_campaign_plan,
    load_podkill_campaign_plan,
    write_podkill_campaign_plan,
)
from tests.test_podkill_artifact import TARGET, prerequisites


def test_publishes_and_reuses_preregistered_campaign_plan(tmp_path: Path) -> None:
    change, pair = prerequisites(tmp_path)

    first = write_podkill_campaign_plan(
        tmp_path / "campaigns",
        change.path,
        pair.path,
        planned_trials=5,
        allowed_failed_trials=0,
        service_recovery_limit_seconds=3,
        replacement_ready_limit_seconds=30,
    )
    second = write_podkill_campaign_plan(
        tmp_path / "campaigns",
        change.path,
        pair.path,
        planned_trials=5,
        allowed_failed_trials=0,
        service_recovery_limit_seconds=3,
        replacement_ready_limit_seconds=30,
    )
    loaded = load_podkill_campaign_plan(first.path)

    assert first.campaign_id.startswith("podkill-campaign-")
    assert first.reused is False
    assert second.campaign_id == first.campaign_id
    assert second.reused is True
    assert loaded.change_id == change.artifact_id
    assert loaded.performance_pair_id == pair.artifact_id
    assert loaded.stopping_rule == "complete_all_planned_trials"


def test_policy_change_produces_distinct_campaign_identity() -> None:
    values = (
        "change-" + "a" * 32,
        "change-performance-pair-" + "b" * 32,
        TARGET,
        5,
        0,
        3.0,
    )

    first = create_podkill_campaign_plan(*values, 30.0)
    second = create_podkill_campaign_plan(*values, 31.0)

    assert first.campaign_id != second.campaign_id


@pytest.mark.parametrize(
    ("planned", "allowed"),
    [(2, 0), (101, 0), (3, 3), (3, 4)],
)
def test_rejects_invalid_campaign_size_or_failure_budget(
    planned: int, allowed: int
) -> None:
    with pytest.raises(PodKillCampaignError, match="policy is invalid"):
        create_podkill_campaign_plan(
            "change-" + "a" * 32,
            "change-performance-pair-" + "b" * 32,
            TARGET,
            planned,
            allowed,
            3,
            30,
        )


@pytest.mark.parametrize("limit", [0, -1, float("inf"), float("nan")])
def test_rejects_non_finite_or_non_positive_recovery_limit(limit: float) -> None:
    with pytest.raises(PodKillCampaignError, match="policy is invalid"):
        create_podkill_campaign_plan(
            "change-" + "a" * 32,
            "change-performance-pair-" + "b" * 32,
            TARGET,
            3,
            0,
            limit,
            30,
        )


def test_rejects_failed_pair_without_creating_campaign_root(tmp_path: Path) -> None:
    change, pair = prerequisites(tmp_path, failed_pair=True)
    output = tmp_path / "campaigns"

    with pytest.raises(PodKillCampaignError, match="prerequisites are invalid"):
        write_podkill_campaign_plan(output, change.path, pair.path, 3, 0, 3, 30)

    assert not output.exists()


def test_rejects_tampered_campaign_report(tmp_path: Path) -> None:
    change, pair = prerequisites(tmp_path)
    artifact = write_podkill_campaign_plan(
        tmp_path / "campaigns", change.path, pair.path, 3, 0, 3, 30
    )
    (artifact.path / "report.md").write_text("changed\n")

    with pytest.raises(PodKillCampaignError, match="report does not replay"):
        load_podkill_campaign_plan(artifact.path)
