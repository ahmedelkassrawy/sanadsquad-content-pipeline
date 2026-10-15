import base64
import io
import json
import shutil
from types import SimpleNamespace

import httpx
import pytest
from PIL import Image, ImageChops
from test_pipeline import ROOT, OfflineModels, run, search_factory
from test_visual_fallback import VisualModels

from content_pipeline.models import ResolvedSlot
from content_pipeline.pipeline import Pipeline, run_pipeline

TOKEN = "synthetic-cloudflare-token-must-not-leak"


def image_bytes(format="PNG"):
    """Build a plain source image for checking code-drawn overlays."""
    stream = io.BytesIO()
    Image.new("RGB", (800, 800), (50, 80, 90)).save(stream, format=format)
    return stream.getvalue()


def mock_cloudflare(monkeypatch, handler, credentials=True):
    """Route all image HTTP requests through a deterministic transport."""
    if credentials:
        monkeypatch.setenv("CLOUDFLARE_ACCOUNT_ID", "account123")
        monkeypatch.setenv("CLOUDFLARE_API_TOKEN", TOKEN)
    else:
        monkeypatch.delenv("CLOUDFLARE_ACCOUNT_ID", raising=False)
        monkeypatch.delenv("CLOUDFLARE_API_TOKEN", raising=False)

    client = httpx.AsyncClient
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: client(transport=httpx.MockTransport(handler), **kwargs),
    )


async def run_image(tmp_path):
    summary = await run(
        tmp_path,
        OfflineModels(),
        [ResolvedSlot(slot_id="a", type="tech-thread", topic_id="KB-01")],
    )
    record = json.loads((tmp_path / "run/visuals.json").read_text())[0]
    return summary, record


@pytest.mark.parametrize("format", ["PNG", "JPEG"])
async def test_cloudflare_image_ships_with_code_overlay(tmp_path, monkeypatch, format):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(
            200, json={"result": {"image": base64.b64encode(image_bytes(format)).decode()}}
        )

    mock_cloudflare(monkeypatch, handler)
    summary, record = await run_image(tmp_path)
    assert record["result"]["backend"] == "cloudflare_flux"
    assert record["result"]["fallback_reason"] is None
    assert summary.external_usage.cloudflare_images == 1
    assert summary.external_usage.image_calls == 1
    assert summary.external_usage.pillow_cards == 0
    assert len(requests) == 1
    request = requests[0]
    assert str(request.url) == (
        "https://api.cloudflare.com/client/v4/accounts/account123/ai/run/"
        "@cf/black-forest-labs/flux-1-schnell"
    )
    assert request.headers["authorization"] == "Bearer " + TOKEN
    payload = json.loads(request.content)
    assert payload["steps"] == 4
    assert "no text" in payload["prompt"].lower()
    assert "Card" in payload["prompt"]
    assert "#14213d" in payload["prompt"]
    assert request.extensions["timeout"]["read"] == 60
    with Image.open(tmp_path / "run" / record["result"]["path"]) as image:
        assert image.format == "PNG"
        assert image.size == (1200, 675)
        plain = Image.new("RGB", image.size, image.getpixel((1199, 674)))
        assert ImageChops.difference(image, plain).crop((80, 110, 1120, 500)).getbbox()
        assert (255, 255, 255) in image.crop((80, 110, 1120, 500)).get_flattened_data()


async def test_missing_credentials_skips_http_without_fallback(tmp_path, monkeypatch):
    requests = []
    mock_cloudflare(monkeypatch, lambda request: requests.append(request), credentials=False)
    summary, record = await run_image(tmp_path)
    assert requests == []
    assert record["result"]["backend"] == "pillow_card"
    assert record["result"]["fallback_reason"] is None
    assert summary.external_usage.cloudflare_images == 0
    assert summary.external_usage.pillow_cards == 1


@pytest.mark.parametrize(
    ("failure", "detail"),
    [
        (401, ["http_error", "http_status:401"]),
        (500, ["http_error", "http_status:500"]),
        ("timeout", ["timeout"]),
        ("base64", ["invalid_base64", "http_status:200"]),
        ("image", ["invalid_image", "http_status:200"]),
        ("response", ["invalid_response", "http_status:200"]),
        ("connection", ["request_error"]),
    ],
)
async def test_cloudflare_failure_falls_back_without_secret(
    tmp_path, monkeypatch, caplog, failure, detail
):
    caplog.set_level("DEBUG")

    def handler(request):
        if failure == "timeout":
            raise httpx.ReadTimeout(TOKEN, request=request)
        if failure == "connection":
            raise httpx.ConnectError(TOKEN, request=request)
        if isinstance(failure, int):
            return httpx.Response(failure, text=TOKEN)
        if failure == "response":
            return httpx.Response(200, json={"errors": [TOKEN]})
        encoded = TOKEN if failure == "base64" else base64.b64encode(TOKEN.encode()).decode()
        return httpx.Response(200, json={"result": {"image": encoded}})

    mock_cloudflare(monkeypatch, handler)
    summary, record = await run_image(tmp_path)
    assert record["result"]["backend"] == "pillow_card"
    assert record["result"]["fallback_reason"] == "cloudflare_failed"
    assert record["result"]["fallback_detail"] == detail
    assert summary.external_usage.cloudflare_images == 1
    assert summary.external_usage.pillow_cards == 1
    assert summary.external_usage.image_calls == 1
    assert TOKEN not in caplog.text
    for path in (tmp_path / "run").rglob("*"):
        if path.is_file():
            assert TOKEN.encode() not in path.read_bytes(), path


async def test_configured_pillow_first_skips_cloudflare(tmp_path, monkeypatch):
    requests = []
    mock_cloudflare(monkeypatch, lambda request: requests.append(request))
    root = tmp_path / "root"
    for folder in ["inputs", "post_types", "agents", "skills", "config"]:
        shutil.copytree(ROOT / folder, root / folder)
    (root / "config/visual.yaml").write_text(
        "backends: [pillow_card, cloudflare_flux]\n", encoding="utf-8"
    )
    summary = await run_pipeline(
        root,
        [ResolvedSlot(slot_id="a", type="tech-thread", topic_id="KB-01")],
        tmp_path / "run",
        model_factory=OfflineModels().factory,
        search_factory=search_factory,
    )
    record = json.loads((tmp_path / "run/visuals.json").read_text())[0]
    assert record["result"]["backend"] == "pillow_card"
    assert record["result"]["fallback_reason"] is None
    assert summary.external_usage.cloudflare_images == 0
    assert requests == []


async def test_saved_cloudflare_png_is_validated_on_resume(tmp_path, monkeypatch):
    mock_cloudflare(
        monkeypatch,
        lambda request: httpx.Response(
            200, json={"result": {"image": base64.b64encode(image_bytes()).decode()}}
        ),
    )
    _, record = await run_image(tmp_path)
    assert record["result"]["backend"] == "cloudflare_flux"
    folder = tmp_path / "run"
    audit = json.loads((folder / "drafts.json").read_text())[0]["audit"]
    pack = next(
        row["pack"] for row in json.loads((folder / "research_log.json").read_text()) if row["pack"]
    )
    pipeline = Pipeline(SimpleNamespace(root=folder / "snapshot", run_dir=folder), [])
    raw = {
        "slot": {"slot_id": "a", "type": "tech-thread", "topic_id": "KB-01"},
        "attempt": 1,
        "pack": pack,
        "audit": audit,
    }
    resumed = await pipeline.visual(raw)
    assert resumed["outcome"] == "shipped"
    assert resumed["image"]["backend"] == "cloudflare_flux"
    Image.new("RGB", (1, 1)).save(folder / record["result"]["path"])
    with pytest.raises(ValueError, match="invalid saved card"):
        await pipeline.visual(raw)


async def test_cloudflare_failure_keeps_visual_fallback_detail(tmp_path, monkeypatch):
    mock_cloudflare(monkeypatch, lambda request: httpx.Response(500, text=TOKEN))
    await run(
        tmp_path,
        VisualModels(["Invented claim", "Another invented claim"]),
        [ResolvedSlot(slot_id="a", type="tech-thread", topic_id="KB-01")],
    )
    record = json.loads((tmp_path / "run/visuals.json").read_text())[0]
    assert record["result"]["backend"] == "pillow_card"
    assert record["result"]["fallback_reason"] == "cloudflare_failed"
    assert record["result"]["fallback_detail"] == [
        "ungrounded_overlay",
        "http_error",
        "http_status:500",
    ]
