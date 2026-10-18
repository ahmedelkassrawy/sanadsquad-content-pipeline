"""Render configured image backends without retaining provider credentials."""

import asyncio
import inspect
import base64
import binascii
import io
import logging
import os
import re
import traceback
from urllib.parse import quote

import httpx
from dotenv import dotenv_values
from PIL import Image, ImageOps

from .images import CARD_SIZE, render_card

ACCOUNT_ID_PATTERN = re.compile(r"[0-9a-f]{32}")
CLOUDFLARE_ACCOUNTS_URL = "https://api.cloudflare.com/client/v4/accounts/"
MAX_RESPONSE_BYTES = 10 * 1024 * 1024
MAX_IMAGE_SIDE = 4096


def image_prompt(spec):
    """Build a background-only image prompt from the approved visual spec."""
    return (
        f"Concept: {spec.concept}\nScene: {spec.scene}\n"
        f"Palette: {', '.join(spec.palette)}\n"
        "Draw no text, letters, numbers, labels, logos or watermarks. "
        "Leave space for a title and severity badge that will be drawn separately."
    )


class _CloudflareSecretFilter(logging.Filter):
    """Drop log records that reproduce Cloudflare credentials or the account URL.

    httpcore pre-formats its trace records with repr(), so each secret is also
    matched in its escaped form: a token containing a newline still matches the
    backslash-n rendering that h11 error text uses.
    """

    def __init__(self):
        super().__init__()
        self._forms = frozenset()

    def watch(self, *secrets):
        """Remember every form of a secret that must never reach a handler."""
        forms = set()
        for secret in secrets:
            if secret:
                forms.add(secret)
                forms.add(ascii(secret)[1:-1])
        self._forms |= forms

    def filter(self, record):
        text = _record_text(record)
        return not any(form in text for form in self._forms)


def _record_text(record):
    """Render a record the way a formatter would, including exception text."""
    text = record.getMessage()
    exc_text = record.exc_text
    if exc_text is None and record.exc_info:
        exc_text = "".join(traceback.format_exception(*record.exc_info))
    if exc_text:
        text = text + "\n" + exc_text
    return text


_HTTP_LOG_FILTER = _CloudflareSecretFilter()


def _http_logger_names():
    """List the httpx and httpcore loggers that can emit secret-bearing records."""
    return [
        "httpx",
        "httpcore",
        *[
            name
            for name in logging.Logger.manager.loggerDict
            if name.startswith(("httpx.", "httpcore."))
        ],
    ]


def _hide_cloudflare_secrets(account_id, token):
    """Keep the account, token and account URL out of library log records.

    The filter is attached to the emitting loggers themselves, because a filter
    on a parent logger never sees records created on a child such as
    httpcore.http11. It is never removed, so concurrent requests cannot race a
    suppression window, and it drops only records that reproduce these secrets,
    so unrelated HTTP logging keeps working at every level.
    """
    _HTTP_LOG_FILTER.watch(account_id, token, CLOUDFLARE_ACCOUNTS_URL)

    for name in _http_logger_names():
        logger = logging.getLogger(name)
        if _HTTP_LOG_FILTER not in logger.filters:
            logger.addFilter(_HTTP_LOG_FILTER)


async def _cloudflare_image(spec, account_id, token):
    _hide_cloudflare_secrets(account_id, token)

    url = (
        CLOUDFLARE_ACCOUNTS_URL
        + quote(account_id, safe="")
        + "/ai/run/@cf/black-forest-labs/flux-1-schnell"
    )

    try:
        async with httpx.AsyncClient(timeout=60, follow_redirects=False) as client:
            response = await client.post(
                url,
                headers={"Authorization": "Bearer " + token},
                json={"prompt": image_prompt(spec), "steps": 4},
            )
    except httpx.TimeoutException:
        return None, ["timeout"]
    except (httpx.HTTPError, OSError, ValueError):
        return None, ["request_error"]

    status = [f"http_status:{response.status_code}"]

    if not 200 <= response.status_code < 300:
        return None, ["http_error", *status]

    if len(response.content) > MAX_RESPONSE_BYTES:
        return None, ["response_too_large", *status]

    try:
        payload = response.json()
        encoded = payload["result"]["image"]

        if payload.get("success") is False or not isinstance(encoded, str):
            return None, ["invalid_response", *status]
    except (ValueError, KeyError, TypeError):
        return None, ["invalid_response", *status]

    try:
        data = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError):
        return None, ["invalid_base64", *status]

    try:
        with Image.open(io.BytesIO(data)) as image:
            if max(image.size) > MAX_IMAGE_SIDE:
                return None, ["image_too_large", *status]

            image.load()
            background = ImageOps.fit(image.convert("RGB"), CARD_SIZE)
    except (OSError, ValueError, Image.DecompressionBombError):
        return None, ["invalid_image", *status]

    return background, None


async def render_image(spec, path, backends, credential_root, renderer, image_count, style=None):
    """Render the first available backend, falling back with safe failure codes."""
    detail = None

    for backend in backends:
        if backend == "pillow_card":
            break

        env = {**dotenv_values(credential_root / ".env"), **os.environ}
        account_id = (env.get("CLOUDFLARE_ACCOUNT_ID") or "").strip()
        token = (env.get("CLOUDFLARE_API_TOKEN") or "").strip()

        if not account_id or not token:
            continue

        if not ACCOUNT_ID_PATTERN.fullmatch(account_id):
            detail = ["invalid_account_id"]
            continue

        await image_count("cloudflare_images")

        background, detail = await _cloudflare_image(spec, account_id, token)

        if background is not None:
            try:
                await asyncio.to_thread(render_card, spec, path, background, style)
            except (OSError, ValueError, TypeError, IndexError) as error:
                detail = ["render_error", type(error).__name__]
            else:
                return "cloudflare_flux", None, None

    # A custom renderer (tests, other brands) may not take a style; the default one does.
    if style is not None and "style" in inspect.signature(renderer).parameters:
        await asyncio.to_thread(renderer, spec, path, style=style)
    else:
        await asyncio.to_thread(renderer, spec, path)

    return "pillow_card", "cloudflare_failed" if detail else None, detail
