"""Keep audited KB protocol names out of writer-visible material."""

import json
import re
from functools import lru_cache
from pathlib import Path
from urllib.parse import unquote, urlsplit

from .models import Draft, ResearchPack


DEFAULT_KB_PATH = Path(__file__).resolve().parents[1] / "inputs" / "kb.json"
DEFAULT_EXTRA_NAMES_PATH = DEFAULT_KB_PATH.with_name("redacted-names.txt")


def name_variants(name: str) -> tuple[str, ...]:
    """Return the full protocol name and its non-generic parenthesis head."""
    name = name.strip()
    head = name.split("(", 1)[0].strip()
    return tuple(dict.fromkeys(part for part in (name, head) if part))


@lru_cache
def load_redacted_names(
    kb_path: Path = DEFAULT_KB_PATH, extra_names_path: Path = DEFAULT_EXTRA_NAMES_PATH
) -> tuple[str, ...]:
    """Load KB protocol variants and operator-supplied names once per file set."""
    data = json.loads(kb_path.read_text(encoding="utf-8"))
    entries = data.get("findings", data.get("entries", []))
    names = [variant for entry in entries for variant in name_variants(entry.get("protocol", ""))]

    if extra_names_path.exists():
        names.extend(
            line.strip()
            for line in extra_names_path.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        )

    return tuple(sorted(dict.fromkeys(names), key=lambda name: (-len(name), name.casefold())))


def _name_pattern(name: str) -> re.Pattern[str]:
    """Match a name through common identifier and URL delimiters.

    The case-insensitive body deliberately stays separate from the trailing camel-case
    boundary: ``swigAta`` is a name-bearing identifier, while ``swigged`` is not.
    """
    words = re.findall(r"[A-Za-z0-9]+", name)
    if not words:
        return re.compile(r"(?!)")
    body = r"[\s_.-]*".join(re.escape(word) for word in words)
    return re.compile(r"(?<![A-Za-z0-9])(?i:" + body + r")(?=$|[^A-Za-z0-9]|[A-Z])")


_URL = re.compile(r"https?://[^\s<>()`]+", re.IGNORECASE)
_GITHUB_PATH = re.compile(
    r"(?:https?://)?(?:www\.)?github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+(?:/[^\s<>()`]*)?",
    re.IGNORECASE,
)
_BASE58 = r"1-9A-HJ-NP-Za-km-z"
_PROGRAM_ID = re.compile(rf"(?<![{_BASE58}])[{_BASE58}]{{32,44}}(?![{_BASE58}])")


def _url_contains_name(url: str, names: tuple[str, ...]) -> bool:
    """Inspect decoded URL host/path, not queries, for a hidden protocol name."""
    parsed = urlsplit(url)
    target = unquote((parsed.hostname or "") + (parsed.path or ""))
    return any(_name_pattern(name).search(target) for name in names)


def _redact_urls(text: str, names: tuple[str, ...]) -> str:
    return _URL.sub(
        lambda match: (
            "<redacted url>" if _url_contains_name(match.group(), names) else match.group()
        ),
        text,
    )


def _name_replacement(match: re.Match[str]) -> str:
    """Keep identifier syntax while replacing only its project-name component."""
    before = match.string[match.start() - 1 : match.start()]
    after = match.string[match.end() : match.end() + 1]
    if before in {"_", "."} or after in {"_", ".", "-"} or after.isupper():
        return "protocol"
    return "the protocol"


def redact_kb_text(text: str, names: tuple[str, ...] | None = None) -> str:
    """Remove KB project identifiers from material intended for a writer."""
    names = load_redacted_names() if names is None else names
    text = _GITHUB_PATH.sub("", text)
    text = _redact_urls(text, names)
    text = _PROGRAM_ID.sub("<program id>", text)
    for name in names:
        text = _name_pattern(name).sub(_name_replacement, text)
    return text


def _redaction_paths(root: Path | None) -> tuple[Path, Path]:
    if root is None:
        return DEFAULT_KB_PATH, DEFAULT_EXTRA_NAMES_PATH
    kb_path = root / "inputs" / "kb.json"
    # AI-policy-only fixtures and pre-redaction run snapshots have no KB copy.  They retain the
    # legacy live policy, while complete current snapshots remain fully self-contained.
    if not kb_path.is_file():
        return DEFAULT_KB_PATH, DEFAULT_EXTRA_NAMES_PATH
    return kb_path, kb_path.with_name("redacted-names.txt")


def kb_protocol_name_violations(text: str, root: Path | None = None) -> list[str]:
    """Return the public violation ids for protocol names in a proposed post."""
    kb_path, extra_names_path = _redaction_paths(root)
    names = load_redacted_names(kb_path, extra_names_path)
    return [
        f"kb_protocol_name:{name}"
        for name in names
        if _name_pattern(name).search(text)
        or any(_url_contains_name(url, (name,)) for url in _URL.findall(text))
    ]


def redact_research_pack(pack: ResearchPack, root: Path | None = None) -> ResearchPack:
    """Sanitize every writer-visible field when a pack includes KB evidence."""
    if not any(source.kind == "kb" for source in pack.sources):
        return pack

    kb_path, extra_names_path = _redaction_paths(root)
    names = load_redacted_names(kb_path, extra_names_path)
    redacted = pack.model_copy(deep=True)
    redacted.topic_name = redact_kb_text(redacted.topic_name, names)
    for source in redacted.sources:
        source.title = redact_kb_text(source.title, names)
        source.evidence_text = redact_kb_text(source.evidence_text, names)
        if source.url:
            source.url = redact_kb_text(source.url, names)
    return redacted


def redact_writer_material(
    pack: ResearchPack,
    feedback: list[str],
    previous_draft: Draft | None,
    root: Path | None = None,
) -> tuple[ResearchPack, list[str], Draft | None]:
    """Return the complete retry payload without KB protocol names or identifiers."""
    if not any(source.kind == "kb" for source in pack.sources):
        return pack, feedback, previous_draft

    kb_path, extra_names_path = _redaction_paths(root)
    names = load_redacted_names(kb_path, extra_names_path)
    prior = previous_draft.model_copy(deep=True) if previous_draft else None
    if prior:
        for tweet in prior.tweets:
            tweet.text = redact_kb_text(tweet.text, names)
    return (
        redact_research_pack(pack, root),
        [redact_kb_text(item, names) for item in feedback],
        prior,
    )
