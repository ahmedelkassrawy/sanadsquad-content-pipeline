import hashlib
import ipaddress
import json
import re
import socket
from pathlib import Path
from urllib.parse import urlparse, urljoin
from rank_bm25 import BM25Okapi
from .models import SourceRecord, ResolvedSlot
from .config import load_type, local_file
import yaml


def words(text):
    return re.findall(r"\w+", text.lower())


class KnowledgeBase:
    def __init__(self, path: Path):
        data = json.loads(path.read_text(encoding="utf-8"))
        self.entries = data["entries"]
        ids = [e["id"] for e in self.entries]
        if len(set(ids)) != len(ids):
            raise ValueError("duplicate KB ids")
        for entry in self.entries:
            for field in ("id", "name", "severity", "category", "description", "root_cause", "aliases", "references"):
                if not entry.get(field):
                    raise ValueError("incomplete KB entry: " + field)
        self.index = BM25Okapi([words(" ".join([e["name"], e["category"], e["description"], *e["aliases"]])) for e in self.entries])

    def search(self, query: str, top_k: int = 5):
        scores = self.index.get_scores(words(query))
        ranked = sorted(enumerate(self.entries), key=lambda pair: (-scores[pair[0]], pair[0]))
        return [e for _, e in ranked[:top_k]]

    def source(self, topic_id):
        entry = next(e for e in self.entries if e["id"] == topic_id)
        return SourceRecord(source_id=entry["id"], kind="kb", title=entry["name"], url=entry["references"][0], evidence_text=entry["description"] + "\nRoot cause: " + entry["root_cause"])


def assign_topics(root: Path, slots: list[ResolvedSlot], proposals: dict[str, str], kb: KnowledgeBase, history: list[list[str]] | None = None):
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
    for slot in slots:
        if slot.focus:
            continue
        spec = load_type(root, slot.type)
        folder = root / "post_types" / slot.type
        if spec.research.topic_source == "kb":
            pool = [e["id"] for e in kb.search(local_file(folder, spec.research.hints).read_text(encoding="utf-8"), len(kb.entries))]
        else:
            pool = [a["id"] for a in yaml.safe_load(local_file(folder, spec.research.angles).read_text(encoding="utf-8"))["angles"]]
        candidate = proposals.get(slot.slot_id)
        available = [id for id in pool if id not in used and id not in historical]
        if candidate not in available:
            if not available:
                available = [id for id in pool if id not in used]
                available.sort(key=lambda id: max((i for i, run in enumerate(history) if id in run), default=-1))
                warnings.append("history_exhausted:" + slot.slot_id)
            if not available:
                raise ValueError("topic pool exhausted within week")
            candidate = available[0]
        result[slot.slot_id] = candidate
        used.add(candidate)
    return result, warnings


def public_url(url: str, resolver=socket.getaddrinfo) -> str:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("public HTTP(S) URL required")
    if parsed.hostname.lower() in {"localhost", "localhost.localdomain"}:
        raise ValueError("private target")
    addresses = resolver(parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80), type=socket.SOCK_STREAM)
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
            connection.request("GET", (parsed.path or "/") + ("?" + parsed.query if parsed.query else ""), headers={"Host": parsed.netloc, "User-Agent": "ContentPipeline/0.1"})
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


class WebSearch:
    def __init__(self, tavily=None, ddgs=None, fetch=fetch_page, check_url=public_url, evidence_limit=12000):
        self.tavily = tavily
        self.ddgs = ddgs
        self.fetch = fetch
        self.check_url = check_url
        self.evidence_limit = evidence_limit
        self.calls = 0
        self.tavily_credits = 0

    def search(self, query: str):
        if self.calls >= 4:
            raise ValueError("research search-step limit")
        self.calls += 1
        hits = []
        provider = "ddgs"
        if self.tavily:
            try:
                self.tavily_credits += 1
                hits = self.tavily.search(query=query, include_raw_content=True, max_results=5).get("results", [])
                provider = "tavily"
            except Exception:
                hits = []
        if not hits and self.ddgs:
            provider = "ddgs"
            hits = list(self.ddgs.text(query, max_results=5))
        records = []
        for hit in hits:
            url = hit.get("url") or hit.get("href")
            if not url:
                continue
            try:
                self.check_url(url)
            except (ValueError, OSError):
                continue
            raw = hit.get("raw_content") if provider == "tavily" else None
            status = "ok"
            evidence = raw
            if not evidence:
                try:
                    evidence = self.fetch(url)
                    if not evidence:
                        status = "fetch_failed"
                except Exception:
                    status = "fetch_failed"
                    evidence = ""
            records.append(SourceRecord(source_id="WEB-" + hashlib.sha256(url.encode()).hexdigest()[:16], kind="web", url=url, title=hit.get("title", url), evidence_text=(evidence or "")[:self.evidence_limit], status=status))
        return list({r.source_id: r for r in records}.values())
