"""Cloudflare FLUX image backend: overlay, fallbacks, validation and secret-free logs."""

import asyncio
import base64
import io
import json
import logging
import shutil
from types import SimpleNamespace

import httpcore
import httpx
import pytest
from PIL import Image, ImageChops
from test_pipeline_end_to_end import ROOT, OfflineModels, run, search_factory
from test_visual_overlay_fallback import VisualModels

from content_pipeline.image_backends import MAX_IMAGE_SIDE, MAX_RESPONSE_BYTES
from content_pipeline.models import ResolvedSlot
from content_pipeline.pipeline import Pipeline, run_pipeline

TOKEN = "synthetic-cloudflare-token-must-not-leak"
ACCOUNT_ID = "0f1e2d3c4b5a69788796a5b4c3d2e1f0"


def image_bytes(format="PNG", size=(800, 800)):
    """Build a plain source image for checking code-drawn overlays."""
    stream = io.BytesIO()
    Image.new("RGB", size, (50, 80, 90)).save(stream, format=format)
    return stream.getvalue()


def mock_cloudflare(monkeypatch, handler, credentials=True):
    """Route all image HTTP requests through a deterministic transport."""
    if credentials:
        monkeypatch.setenv("CLOUDFLARE_ACCOUNT_ID", ACCOUNT_ID)
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


class WireCapture(logging.Handler):
    """Collect formatted records from one logger, independent of capture state."""

    def __init__(self):
        super().__init__(logging.DEBUG)
        self.messages = []

    def emit(self, record):
        self.messages.append(self.format(record))


def mock_wire(monkeypatch, backend, token=TOKEN):
    """Route image requests through a real httpcore pool fed by a mock backend.

    Only the socket layer is faked: h11, the httpcore connection pool and the
    httpcore trace loggers all run for real, so DEBUG traces behave exactly as
    they would against Cloudflare.
    """
    monkeypatch.setenv("CLOUDFLARE_ACCOUNT_ID", ACCOUNT_ID)
    monkeypatch.setenv("CLOUDFLARE_API_TOKEN", token)
    client_class = httpx.AsyncClient

    def client(**kwargs):
        transport = httpx.AsyncHTTPTransport()
        transport._pool = httpcore.AsyncConnectionPool(network_backend=backend)
        return client_class(transport=transport, trust_env=False, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", client)
    return client_class


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
        "https://api.cloudflare.com/client/v4/accounts/" + ACCOUNT_ID + "/ai/run/"
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
        assert image.getpixel((1199, 674)) == (50, 80, 90)
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
    assert ACCOUNT_ID not in caplog.text
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


async def test_request_url_and_token_never_reach_logs(tmp_path, monkeypatch, caplog):
    caplog.set_level("DEBUG")
    http_loggers = [logging.getLogger("httpx"), logging.getLogger("httpcore")]
    previous_levels = [logger.level for logger in http_loggers]

    for logger in http_loggers:
        logger.setLevel(logging.DEBUG)

    def handler(request):
        return httpx.Response(
            200, json={"result": {"image": base64.b64encode(image_bytes()).decode()}}
        )

    try:
        mock_cloudflare(monkeypatch, handler)
        _, record = await run_image(tmp_path)
        assert record["result"]["backend"] == "cloudflare_flux"
        assert ACCOUNT_ID not in caplog.text
        assert TOKEN not in caplog.text
        assert [logger.level for logger in http_loggers] == [logging.DEBUG, logging.DEBUG]
    finally:
        for logger, level in zip(http_loggers, previous_levels):
            logger.setLevel(level)


async def test_cloudflare_url_filter_keeps_unrelated_logs_and_levels(tmp_path, monkeypatch, caplog):
    caplog.set_level("DEBUG")
    http_loggers = [logging.getLogger("httpx"), logging.getLogger("httpcore")]
    previous_levels = [logger.level for logger in http_loggers]

    for logger in http_loggers:
        logger.setLevel(logging.DEBUG)

    entered = asyncio.Event()
    release = asyncio.Event()

    async def cloudflare(request):
        entered.set()
        await release.wait()
        return httpx.Response(
            200, json={"result": {"image": base64.b64encode(image_bytes()).decode()}}
        )

    def unrelated(request):
        return httpx.Response(200, json={"ok": True})

    client_class = httpx.AsyncClient

    async def call_unrelated(moment):
        transport = httpx.MockTransport(unrelated)
        async with client_class(transport=transport) as client:
            return await client.get(f"https://model-provider.invalid/{moment}")

    async def call_during():
        await entered.wait()
        await call_unrelated("during")
        release.set()

    try:
        mock_cloudflare(monkeypatch, cloudflare)
        await call_unrelated("before")
        during = asyncio.create_task(call_during())
        _, record = await asyncio.wait_for(run_image(tmp_path), 30)
        await asyncio.wait_for(during, 30)
        await call_unrelated("after")

        assert record["result"]["backend"] == "cloudflare_flux"
        for moment in ["before", "during", "after"]:
            assert f"HTTP Request: GET https://model-provider.invalid/{moment}" in caplog.text
        assert ACCOUNT_ID not in caplog.text
        assert TOKEN not in caplog.text
        assert [logger.level for logger in http_loggers] == [logging.DEBUG, logging.DEBUG]
    finally:
        for logger, level in zip(http_loggers, previous_levels):
            logger.setLevel(level)


@pytest.mark.parametrize(
    ("case", "wire", "token", "detail"),
    [
        (
            "normal",
            b"HTTP/1.1 401 Unauthorized\r\nContent-Length: 0\r\n\r\n",
            TOKEN,
            ["http_error", "http_status:401"],
        ),
        ("token_with_newline", b"", TOKEN + "\ncontinuation", ["request_error"]),
        (
            "reflected_location",
            b"HTTP/1.1 302 Found\r\nLocation: /client/v4/accounts/"
            + ACCOUNT_ID.encode()
            + b"/next\r\nContent-Length: 0\r\n\r\n",
            TOKEN,
            ["http_error", "http_status:302"],
        ),
        (
            "status_echoes_token",
            b"HTTP/1.1 " + TOKEN.encode() + b" invalid\r\n\r\n",
            TOKEN,
            ["request_error"],
        ),
    ],
)
async def test_cloudflare_secrets_never_reach_httpcore_handlers(
    tmp_path, monkeypatch, caplog, case, wire, token, detail
):
    caplog.set_level("DEBUG")
    http_loggers = [
        logging.getLogger(name)
        for name in ["httpx", "httpcore", "httpcore.http11", "httpcore.connection"]
    ]
    previous_levels = [logger.level for logger in http_loggers]

    for logger in http_loggers:
        logger.setLevel(logging.DEBUG)

    direct = WireCapture()
    logging.getLogger("httpcore.http11").addHandler(direct)

    try:
        mock_wire(monkeypatch, httpcore.AsyncMockBackend([wire]), token=token)
        _, record = await run_image(tmp_path)

        assert record["result"]["backend"] == "pillow_card"
        assert record["result"]["fallback_detail"] == detail
        assert direct.messages, "the real httpcore trace path must log"

        rendered = caplog.text + "\n" + "\n".join(direct.messages)
        secrets = [TOKEN, ACCOUNT_ID, "https://api.cloudflare.com/client/v4/accounts/"]
        if token != TOKEN:
            secrets.append(ascii(token)[1:-1])
        for secret in secrets:
            assert secret not in rendered, (case, secret)
    finally:
        logging.getLogger("httpcore.http11").removeHandler(direct)
        for logger, level in zip(http_loggers, previous_levels):
            logger.setLevel(level)


async def test_unrelated_httpcore_traces_survive_cloudflare_call(tmp_path, monkeypatch, caplog):
    caplog.set_level("DEBUG")
    http_loggers = [
        logging.getLogger(name)
        for name in ["httpx", "httpcore", "httpcore.http11", "httpcore.connection"]
    ]
    previous_levels = [logger.level for logger in http_loggers]

    for logger in http_loggers:
        logger.setLevel(logging.DEBUG)

    entered = asyncio.Event()
    release = asyncio.Event()

    class GatedBackend(httpcore.AsyncMockBackend):
        """Hold the Cloudflare connection open until the unrelated call logged."""

        async def connect_tcp(self, *args, **kwargs):
            entered.set()
            await release.wait()
            return await super().connect_tcp(*args, **kwargs)

    reflected = (
        b"HTTP/1.1 302 Found\r\nLocation: /client/v4/accounts/"
        + ACCOUNT_ID.encode()
        + b"/next\r\nContent-Length: 0\r\n\r\n"
    )

    try:
        client_class = mock_wire(monkeypatch, GatedBackend([reflected]))

        async def unrelated():
            try:
                await entered.wait()
                transport = httpx.AsyncHTTPTransport()
                transport._pool = httpcore.AsyncConnectionPool(
                    network_backend=httpcore.AsyncMockBackend(
                        [b"HTTP/1.1 200 OK\r\nContent-Length: 0\r\n\r\n"]
                    )
                )
                async with client_class(transport=transport, trust_env=False) as client:
                    await client.get("https://model-provider.invalid/concurrent")
            finally:
                release.set()

        during = asyncio.create_task(unrelated())
        _, record = await asyncio.wait_for(run_image(tmp_path), 30)
        await asyncio.wait_for(during, 30)

        assert record["result"]["backend"] == "pillow_card"
        assert record["result"]["fallback_detail"] == ["http_error", "http_status:302"]
        assert "HTTP Request: GET https://model-provider.invalid/concurrent" in caplog.text
        assert "send_request_headers.started request=<Request [b'GET']>" in caplog.text
        assert TOKEN not in caplog.text
        assert ACCOUNT_ID not in caplog.text
        assert [logger.level for logger in http_loggers] == [
            logging.DEBUG,
            logging.DEBUG,
            logging.DEBUG,
            logging.DEBUG,
        ]
    finally:
        for logger, level in zip(http_loggers, previous_levels):
            logger.setLevel(level)


async def test_oversized_response_body_falls_back_to_card(tmp_path, monkeypatch):
    def handler(request):
        return httpx.Response(200, content=b"x" * (MAX_RESPONSE_BYTES + 1))

    mock_cloudflare(monkeypatch, handler)
    summary, record = await run_image(tmp_path)
    assert record["result"]["backend"] == "pillow_card"
    assert record["result"]["fallback_reason"] == "cloudflare_failed"
    assert record["result"]["fallback_detail"] == ["response_too_large", "http_status:200"]
    assert summary.external_usage.cloudflare_images == 1
    assert summary.external_usage.image_calls == 1
    assert summary.external_usage.pillow_cards == 1


@pytest.mark.parametrize(
    ("size", "backend", "detail"),
    [
        ((MAX_IMAGE_SIDE + 1, 64), "pillow_card", ["image_too_large", "http_status:200"]),
        ((MAX_IMAGE_SIDE, 64), "cloudflare_flux", None),
    ],
)
async def test_oversized_image_falls_back_to_card(tmp_path, monkeypatch, size, backend, detail):
    def handler(request):
        return httpx.Response(
            200, json={"result": {"image": base64.b64encode(image_bytes(size=size)).decode()}}
        )

    mock_cloudflare(monkeypatch, handler)
    _, record = await run_image(tmp_path)
    assert record["result"]["backend"] == backend
    assert record["result"]["fallback_detail"] == detail


async def test_invalid_account_id_makes_no_request_and_falls_back(tmp_path, monkeypatch):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(
            200, json={"result": {"image": base64.b64encode(image_bytes()).decode()}}
        )

    mock_cloudflare(monkeypatch, handler)
    monkeypatch.setenv("CLOUDFLARE_ACCOUNT_ID", "Account123")
    summary, record = await run_image(tmp_path)
    assert requests == []
    assert record["result"]["backend"] == "pillow_card"
    assert record["result"]["fallback_reason"] == "cloudflare_failed"
    assert record["result"]["fallback_detail"] == ["invalid_account_id"]
    assert summary.external_usage.cloudflare_images == 0
    assert summary.external_usage.pillow_cards == 1
    assert "Account123" not in json.dumps(record)
    for path in (tmp_path / "run").rglob("*"):
        if path.is_file():
            assert b"Account123" not in path.read_bytes(), path


async def test_malformed_visual_config_fails_preflight(tmp_path):
    root = tmp_path / "root"
    for folder in ["inputs", "post_types", "agents", "skills", "config"]:
        shutil.copytree(ROOT / folder, root / folder)
    (root / "config/visual.yaml").write_text("backends: [cloudflare-flux]\n", encoding="utf-8")
    with pytest.raises(ValueError, match="invalid visual backends"):
        await run_pipeline(
            root,
            [ResolvedSlot(slot_id="a", type="tech-thread", topic_id="KB-01")],
            tmp_path / "run",
            model_factory=OfflineModels().factory,
            search_factory=search_factory,
        )
