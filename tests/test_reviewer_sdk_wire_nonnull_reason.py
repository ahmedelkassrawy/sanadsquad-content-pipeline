"""The SDK reviewer schema makes the optional reason non-nullable."""

import pytest
from content_pipeline.agents import Reviewer
from content_pipeline.model_client import ModelFailure
from test_reviewer_sdk_wire import ROOT, review_request, sdk_client


@pytest.mark.parametrize("mode", ["supported", "empty-false-reason", "empty-true-reason"])
async def test_public_sdk_optional_reason_is_nonnullable_and_preserves_empty_policy(
    tmp_path, monkeypatch, mode
):
    request = review_request()
    client, captures = sdk_client(tmp_path, monkeypatch, request, mode)
    if mode == "empty-true-reason":
        with pytest.raises(ModelFailure, match="failed_output"):
            await Reviewer(ROOT, "marketing-post", client)(request)
        assert len(captures) == 2 and client.ledger.totals()["tokens"] == 100
    else:
        result = await Reviewer(ROOT, "marketing-post", client)(request)
        assert result.accepted is True and result.hard_fail == []
        assert len(captures) == 1 and client.ledger.totals()["tokens"] == 50
    if mode == "supported":
        reasons = captures[0]["tools"][0]["function"]["parameters"]["properties"]["reasons"]
        field = reasons["properties"]["off_brand"]
        assert field.get("type") == "string", field
        assert "anyOf" not in field and "oneOf" not in field
        assert field["default"] == "" and "off_brand" not in reasons["required"]
