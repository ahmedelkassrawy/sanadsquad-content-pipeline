import ipaddress
import socket
from pathlib import Path
from urllib.parse import urlparse, urljoin
from pydantic import ConfigDict, Field, create_model
from skills.search_kb import KnowledgeBase
from .contracts import TopicChoiceRequest, TopicChoices
from .models import ResolvedSlot
from .post_types import PostTypes


def __getattr__(name):
    """Re-export the web_search skill lazily: it imports this module's fetch helpers."""
    if name == "WebSearch":
        from skills.web_search import WebSearch

        return WebSearch

    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def topic_wire_schema(request: TopicChoiceRequest):
    """Build the structured output schema for one topic batch: one named field per open slot.

    The canonical `TopicChoices` holds `dict[str, str]`, which reaches the provider as an object
    with no properties, and Claude then answers `{}` (measured live: 28 output tokens, no
    picks). Naming each slot gives the model something to fill. Fields stay optional and plain
    strings: an id outside the pool is still a per-slot rejection in `assign_topics`, not a
    failed batch. Aliases keep slot ids intact whatever characters they contain.
    """
    slots = list(request.choices)

    assignments = create_model(
        "TopicAssignments",
        __config__=ConfigDict(extra="ignore"),
        **{f"field_{i}": (str | None, Field(default=None, alias=s)) for i, s in enumerate(slots)},
    )

    reasons = create_model(
        "TopicReasons",
        __config__=ConfigDict(extra="ignore"),
        **{f"field_{i}": (str | None, Field(default=None, alias=s)) for i, s in enumerate(slots)},
    )

    return create_model(
        "TopicChoices",
        __base__=TopicChoices,
        assignments=(assignments, ...),
        reasons=(reasons, Field(default_factory=reasons)),
    )


def canonical_topic_choices(wire) -> TopicChoices:
    """Fold a wire answer into the canonical contract, leaving out slots the model left blank."""
    dump = wire.model_dump(by_alias=True)

    return TopicChoices(
        slot_id=dump["slot_id"],
        attempt=dump["attempt"],
        assignments={slot: id for slot, id in dump["assignments"].items() if id},
        reasons={slot: text for slot, text in dump["reasons"].items() if text},
    )


def recent_topic_ids(history: list[list[str]] | None) -> set[str]:
    """The topic ids from the last four runs, which a new week avoids while it can."""
    return {id for run in (history or [])[-4:] for id in run}


def open_topics(pool: list[str], used: set[str], historical: set[str]) -> list[str]:
    """The ids of a pool that `assign_topics` accepts: not used this week, not in recent runs."""
    return [id for id in pool if id not in used and id not in historical]


def assign_topics(
    root: Path,
    slots: list[ResolvedSlot],
    proposals: dict[str, str],
    kb: KnowledgeBase,
    history: list[list[str]] | None = None,
    *,
    allow_partial=False,
    types: PostTypes | None = None,
):
    types = types or PostTypes(root)
    history = (history or [])[-4:]
    historical = recent_topic_ids(history)
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

        pool = types.topic_pool(slot.type, kb, ranked=True)

        pools[slot.slot_id] = pool

        candidate = proposals.get(slot.slot_id)

        if candidate is None:
            continue

        if candidate in open_topics(pool, used, historical):
            result[slot.slot_id] = candidate

            used.add(candidate)
        else:
            # Outside the pool, used this week or in a recent run: the ranked fallback below
            # picks instead, and the warning keeps the miss visible.
            warnings.append(f"topic_proposal_rejected:{slot.slot_id}:{candidate}")

    for slot in slots:
        if slot.slot_id in result:
            continue

        pool = pools[slot.slot_id]
        available = open_topics(pool, used, historical)

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
