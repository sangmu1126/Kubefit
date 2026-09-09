import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from safety import ChangeLoadError, SubprocessChangeK6Executor
from tests.test_benchmark import phase

CHANGE_ID = "change-0123456789abcdef0123456789abcdef"


def _summary(change_id: str = CHANGE_ID, variant: str = "before") -> dict[str, object]:
    return {
        "schema_version": 1,
        "profile_version": "kubefit-load-v1",
        "change_id": change_id,
        "variant": variant,
        "dropped_iterations": 0,
        "steady": phase(300, 100, 110),
        "spike": phase(750, 200, 220),
        "recovery": phase(300, 120, 130),
    }


def _raw() -> bytes:
    rows = [
        {
            "type": "Point",
            "metric": "kubefit_recovery_start",
            "data": {
                "time": "2026-09-09T00:02:00Z",
                "value": 1,
                "tags": {"kubefit_phase": "recovery"},
            },
        }
    ]
    rows.extend(
        {
            "type": "Point",
            "metric": "http_req_duration",
            "data": {
                "time": f"2026-09-09T00:02:0{second // 10}.{second % 10}Z",
                "value": 100,
                "tags": {"kubefit_phase": "recovery"},
            },
        }
        for second in range(20)
    )
    return ("\n".join(json.dumps(row) for row in rows) + "\n").encode()


def _executor(tmp_path: Path, *, summary: dict[str, object] | None = None):
    script = tmp_path / "profile.js"
    script.write_text("// fixed profile")
    calls: list[tuple[list[str], int]] = []

    def runner(command, timeout):
        calls.append((list(command), timeout))
        environment = {
            item.split("=", 1)[0]: item.split("=", 1)[1]
            for index, item in enumerate(command)
            if index > 0 and command[index - 1] == "-e"
        }
        Path(environment["KUBEFIT_SUMMARY_PATH"]).write_text(
            json.dumps(summary or _summary())
        )
        raw_argument = command[command.index("--out") + 1]
        Path(raw_argument.removeprefix("json=")).write_bytes(_raw())
        return ""

    moments = iter(
        [
            datetime(2026, 9, 9, tzinfo=UTC),
            datetime(2026, 9, 9, tzinfo=UTC) + timedelta(seconds=160),
        ]
    )
    return (
        SubprocessChangeK6Executor(
            "http://127.0.0.1:8080/",
            script,
            runner=runner,
            clock=lambda: next(moments),
            timeout_seconds=200,
        ),
        calls,
    )


def test_runs_fixed_profile_with_change_identity_and_hashes(tmp_path: Path) -> None:
    executor, calls = _executor(tmp_path)

    result = executor.run(CHANGE_ID, "before")

    command, timeout = calls[0]
    assert f"KUBEFIT_CHANGE_ID={CHANGE_ID}" in command
    assert not any("KUBEFIT_PROPOSAL_ID" in item for item in command)
    assert timeout == 200
    assert result.summary.change_id == CHANGE_ID
    assert result.traffic_spike_recovered is True
    assert result.traffic_spike_recovery_seconds == 5
    assert len(result.summary_sha256) == len(result.raw_sha256) == 64


def test_rejects_output_bound_to_another_change(tmp_path: Path) -> None:
    executor, _ = _executor(
        tmp_path,
        summary=_summary("change-fedcba9876543210fedcba9876543210"),
    )

    with pytest.raises(ChangeLoadError, match="identity does not match"):
        executor.run(CHANGE_ID, "before")


def test_rejects_malformed_change_before_starting_k6(tmp_path: Path) -> None:
    executor, calls = _executor(tmp_path)

    with pytest.raises(ChangeLoadError, match=r"change-\*"):
        executor.run("change-not-a-digest", "before")

    assert calls == []


def test_rejects_symlinked_load_profile(tmp_path: Path) -> None:
    source = tmp_path / "source.js"
    source.write_text("// profile")
    link = tmp_path / "link.js"
    link.symlink_to(source)

    with pytest.raises(ValueError, match="non-symlinked"):
        SubprocessChangeK6Executor("http://127.0.0.1:8080", link)


def test_shared_k6_profile_supports_exactly_one_typed_identity() -> None:
    profile = (
        Path(__file__).parents[1] / "benchmarks" / "k6" / "resource_profile.js"
    ).read_text()

    assert "exactly one of KUBEFIT_PROPOSAL_ID or KUBEFIT_CHANGE_ID" in profile
    assert "proposal_id: proposalId" in profile
    assert "change_id: changeId" in profile
