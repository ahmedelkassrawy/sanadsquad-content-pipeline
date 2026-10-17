"""The live proof harness does nothing without its flag and rejects invalid spend caps."""

import pytest
from content_pipeline import live_proof


def test_live_harness_without_flag_makes_no_client_or_output(tmp_path, monkeypatch, capsys):
    def forbidden(*args, **kwargs):
        pytest.fail("ungated live initialization")

    monkeypatch.setattr(live_proof, "configured_client", forbidden)
    monkeypatch.setattr(live_proof, "run_pipeline", forbidden)
    monkeypatch.setattr(live_proof, "Prices", forbidden)
    output = tmp_path / "proof"
    assert live_proof.main(["--output", str(output)]) == 0
    assert "NOT_RUN: explicit --live" in capsys.readouterr().out
    assert not output.exists()


@pytest.mark.parametrize(
    "flag,value",
    [("--cost-budget", "1.01"), ("--grounding-cost-budget", "0.11"), ("--token-budget", "0")],
)
def test_live_harness_rejects_invalid_spend_caps_before_initialization(
    tmp_path, monkeypatch, flag, value
):
    def forbidden(*args, **kwargs):
        pytest.fail("invalid cap initialized live execution")

    monkeypatch.setattr(live_proof, "execute", forbidden)
    with pytest.raises(SystemExit) as caught:
        live_proof.main(["--live", "--output", str(tmp_path / "proof"), flag, value])
    assert caught.value.code == 2
    assert not (tmp_path / "proof").exists()
