from types import SimpleNamespace
import pytest
from content_pipeline import live_proof
from content_pipeline.models import Review


@pytest.mark.parametrize("flags,expected",[([],False),(["--allow-unpriced"],True)])
def test_live_harness_passes_unpriced_policy_to_both_settings(tmp_path,monkeypatch,flags,expected):
    settings_seen={}

    def client(root,role,ledger,usage,*,settings,prices):
        settings_seen["reviewer"]=settings
        return object()

    class Reviewer:
        def __init__(self,*args): pass

        async def __call__(self,request):
            supported=request.slot_id=="supported"
            return Review(slot_id=request.slot_id,scores={"grounding":5 if supported else 1},reasons=[],hard_fail=[] if supported else ["unsupported_claim"],accepted=supported)

    async def pipeline(root,slots,output,settings):
        settings_seen["sample"]=settings
        return SimpleNamespace(shipped=6,model_dump=lambda **kwargs:{"shipped":6})

    monkeypatch.setattr(live_proof,"configured_client",client)
    monkeypatch.setattr(live_proof,"Reviewer",Reviewer)
    monkeypatch.setattr(live_proof,"Prices",lambda **kwargs:SimpleNamespace(close=lambda:None))
    monkeypatch.setattr(live_proof,"run_pipeline",pipeline)
    assert live_proof.main(["--live","--output",str(tmp_path/"proof"),*flags])==0
    assert set(settings_seen)=={"reviewer","sample"}
    assert settings_seen["reviewer"].allow_unpriced is expected
    assert settings_seen["sample"].allow_unpriced is expected
