"""Render configured image backends without retaining provider credentials."""

import asyncio
import base64
import binascii
import io
import logging
import os
import re
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


class _CloudflareUrlFilter(logging.Filter):
    """Drop log records that reproduce the Cloudflare account request URL."""

    def filter(self, record):
        return CLOUDFLARE_ACCOUNTS_URL not in record.getMessage()


_HTTP_LOG_FILTER = _CloudflareUrlFilter()


def _hide_cloudflare_url():
    """Attach the Cloudflare URL filter to the shared httpx logger once.

    The filter matches only this backend's account URL, so unrelated httpx
    records and all logger levels stay untouched for the host application.
    """
    logger = logging.getLogger("httpx")

    if _HTTP_LOG_FILTER not in logger.filters:
        logger.addFilter(_HTTP_LOG_FILTER)


async def _cloudflare_image(spec, account_id, token):
    _hide_cloudflare_url()

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


async def render_image(spec, path, backends, credential_root, renderer, image_count):
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
                await asyncio.to_thread(render_card, spec, path, background)
            except (OSError, ValueError, TypeError, IndexError) as error:
                detail = ["render_error", type(error).__name__]
            else:
                return "cloudflare_flux", None, None

    await asyncio.to_thread(renderer, spec, path)

    return "pillow_card", "cloudflare_failed" if detail else None, detail
