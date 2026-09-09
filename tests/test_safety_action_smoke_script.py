import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "deploy" / "local" / "verify-safety-action.sh"


def test_safety_action_smoke_script_has_valid_bash_syntax() -> None:
    subprocess.run(["bash", "-n", str(SCRIPT)], check=True)


def test_safety_action_smoke_script_proves_pass_and_fail_closed_paths() -> None:
    source = SCRIPT.read_text()

    assert "--volume \"${repository_root}:/github/workspace:ro\"" in source
    assert "tampered-candidate.yaml" in source
    assert "invalid_exit" in source
    assert "-ne 2" in source
    assert "## ✅ PASS" in source
    assert "## ⚠️ INVALID" in source
    assert source.count("docker run \\") == 2
