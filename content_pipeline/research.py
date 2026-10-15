import ipaddress
import socket
from pathlib import Path
from urllib.parse import urlparse, urljoin
from skills.search_kb import KnowledgeBase
from .models import ResolvedSlot
from .config import load_type, load_angles, local_file


def __getattr__(name):
    """Re-export the web_search skill lazily: it imports this module's fetch helpers."""
    if name == "WebSearch":
        from skills.web_search import WebSearch

        return WebSearch

    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def assign_topics(
    root: Path,
    slots: list[ResolvedSlot],
    proposals: dict[str, str],
    kb: KnowledgeBase,
    history: list[list[str]] | None = None,
    *,
    allow_partial=False,
):
    history = (history or [])[-4:]
    historical = {id for run in history for id in run}
    used = set()
    result = {}
    warnings = []

    for slot in slots:
        if slot.focus:
            if slot.topic_id:
                if slot.topic_id in used:
                    warnings.append("explicit_duplicate:" + slot.topic_id)

                used.add(slot.topic_id)

            result[slot.slot_id] = slot.topic_id or slot.focus

    pools = {}

    for slot in slots:
        if slot.focus:
            continue

        spec = load_type(root, slot.type)
        folder = root / "post_types" / slot.type

        if spec.research.topic_source == "kb":
            pool = [
                e["id"]
                for e in kb.search(
                    local_file(folder, spec.research.hints).read_text(encoding="utf-8"),
                    len(kb.entries),
                )
            ]
        else:
            pool = [angle.id for angle in load_angles(root, spec)]

        pools[slot.slot_id] = pool

        candidate = proposals.get(slot.slot_id)

        if candidate in pool and candidate not in used and candidate not in historical:
            result[slot.slot_id] = candidate

            used.add(candidate)

    for slot in slots:
        if slot.slot_id in result:
            continue

        pool = pools[slot.slot_id]
        available = [id for id in pool if id not in used and id not in historical]

        if not available:
            available = [id for id in pool if id not in used]

            available.sort(
                key=lambda id: max((i for i, run in enumerate(history) if id in run), default=-1)
            )

            if available:
                warnings.append("history_exhausted:" + slot.slot_id)

        if not available:
            if allow_partial:
                warnings.append("topic_pool_exhausted:" + slot.slot_id)

                continue

            raise ValueError("topic pool exhausted within week")

        candidate = available[0]
        result[slot.slot_id] = candidate

        used.add(candidate)

    return result, warnings


def public_url(url: str, resolver=socket.getaddrinfo) -> str:
    parsed = urlparse(url)

    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
    ):
        raise ValueError("public HTTP(S) URL required")

    if parsed.hostname.lower() in {"localhost", "localhost.localdomain"}:
        raise ValueError("private target")

    addresses = resolver(
        parsed.hostname,
        parsed.port or (443 if parsed.scheme == "https" else 80),
        type=socket.SOCK_STREAM,
    )

    if not addresses or any(not ipaddress.ip_address(item[4][0]).is_global for item in addresses):
        raise ValueError("private target")

    return url


def fetch_page(url: str) -> str:
    # Each redirect is checked; a public DNS answer is pinned for the request.
    import http.client

    import ssl

    import trafilatura

    for _ in range(6):
        public_url(url)

        parsed = urlparse(url)
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        addresses = socket.getaddrinfo(parsed.hostname, port, type=socket.SOCK_STREAM)

        if any(not ipaddress.ip_address(a[4][0]).is_global for a in addresses):
            raise ValueError("private target")

        connection = http.client.HTTPConnection(parsed.hostname, port, timeout=15)
        sock = socket.create_connection((addresses[0][4][0], port), timeout=15)

        if parsed.scheme == "https":
            sock = ssl.create_default_context().wrap_socket(sock, server_hostname=parsed.hostname)

        connection.sock = sock

        try:
            connection.request(
                "GET",
                (parsed.path or "/") + ("?" + parsed.query if parsed.query else ""),
                headers={"Host": parsed.netloc, "User-Agent": "ContentPipeline/0.1"},
            )

            response = connection.getresponse()

            if response.status in {301, 302, 303, 307, 308}:
                url = urljoin(url, response.getheader("Location") or "")

                continue

            if response.status != 200:
                raise ValueError("page fetch failed")

            body = response.read(2_000_001)

            if len(body) > 2_000_000:
                raise ValueError("page too large")

            return trafilatura.extract(body.decode("utf-8", errors="replace")) or ""
        finally:
            connection.close()

    raise ValueError("too many redirects")
