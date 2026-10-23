"""Keep audited KB protocol names out of writer-visible material."""

import json
import re
from functools import lru_cache
from pathlib import Path


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
    names = [variant for entry in entries for variant in name_variants(entry["protocol"])]

    if extra_names_path.exists():
        names.extend(
            line.strip()
            for line in extra_names_path.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        )

    return tuple(sorted(dict.fromkeys(names), key=lambda name: (-len(name), name.casefold())))


def _name_pattern(name: str) -> re.Pattern[str]:
    return re.compile(r"(?<!\w)" + re.escape(name) + r"(?!\w)", re.IGNORECASE)


def redact_kb_text(text: str, names: tuple[str, ...] | None = None) -> str:
    """Replace complete protocol-name variants while leaving generic component words alone."""
    for name in names or load_redacted_names():
        text = _name_pattern(name).sub("the protocol", text)
    return text


def _redaction_paths(root: Path | None) -> tuple[Path, Path]:
    if root is None:
        return DEFAULT_KB_PATH, DEFAULT_EXTRA_NAMES_PATH
    kb_path = root / "inputs" / "kb.json"
    return kb_path, kb_path.with_name("redacted-names.txt")


def kb_protocol_name_violations(text: str, root: Path | None = None) -> list[str]:
    """Return the public violation ids for protocol names in a proposed post."""
    kb_path, extra_names_path = _redaction_paths(root)
    return [
        f"kb_protocol_name:{name}"
        for name in load_redacted_names(kb_path, extra_names_path)
        if _name_pattern(name).search(text)
    ]
