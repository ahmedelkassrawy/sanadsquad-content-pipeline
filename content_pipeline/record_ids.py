"""One home for the stable `slot_id/node/attempt/call_n` id of records and model calls."""

from collections.abc import Iterable
from dataclasses import dataclass

SEPARATOR = "/"


@dataclass(frozen=True)
class RecordId:
    """The four parts of one record or call id."""

    slot_id: str
    node: str
    attempt: int
    call_n: int

    def __str__(self) -> str:
        return SEPARATOR.join((self.slot_id, self.node, str(self.attempt), str(self.call_n)))


def _checked(*parts: str) -> tuple[str, ...]:
    """Refuse parts that would break the id's four-part shape."""
    if any(SEPARATOR in part or not part for part in parts):
        raise ValueError("record id parts must be nonempty and slash-free")

    return parts


def record_id(slot_id: str, node: str, attempt: int, call_n: int = 1) -> str:
    """Build the stable id of one trail record."""
    return SEPARATOR.join(_checked(slot_id, node, str(attempt), str(call_n)))


def call_id(slot_id: str, node: str, attempt: int, call_n: int = 1) -> str:
    """Build the stable id of one model call; a call and its record share one format."""
    return record_id(slot_id, node, attempt, call_n)


def parse_record_id(value: str) -> RecordId:
    """Read one id back into its four parts and refuse a malformed string."""
    parts = value.split(SEPARATOR)

    if len(parts) != 4:
        raise ValueError("malformed record id")

    slot_id, node, attempt, call_n = parts

    if not slot_id or not node or not attempt.isdecimal() or not call_n.isdecimal():
        raise ValueError("malformed record id")

    return RecordId(slot_id, node, int(attempt), int(call_n))


def _attempt_prefix(slot_id: str, node: str, attempt: int) -> str:
    """The `slot_id/node/attempt/` head shared by every call id of one attempt."""
    return SEPARATOR.join(_checked(slot_id, node, str(attempt))) + SEPARATOR


def next_call_n(keys: Iterable[str], slot_id: str, node: str, attempt: int) -> int:
    """Return the first unused call number, counting the ids already in a ledger."""
    prefix = _attempt_prefix(slot_id, node, attempt)

    used = [
        int(key[len(prefix) :])
        for key in keys
        if key.startswith(prefix) and key[len(prefix) :].isdecimal()
    ]

    return max(used, default=0) + 1
