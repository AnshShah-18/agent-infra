"""
audit_log.py — Append-only, tamper-evident audit logging for agent-infra.

Every privileged action (agent run triggered, task submitted, credentials
rotated, audit log read, access denied, etc.) is recorded as one JSON line
with who/what/when/outcome, plus a SHA-256 hash chain linking each entry to
the previous one so that any deletion, reordering, or edit of a past entry
is detectable by recomputing the chain (verify_chain()).

This does not require an external database: it's a single append-only file,
which keeps it easy to drop into an existing service and easy to inspect by
hand during an audit ("show me every action agent:critic took last Tuesday").
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from dataclasses import dataclass, asdict
from enum import Enum
from typing import Any, Optional


class Outcome(str, Enum):
    SUCCESS = "success"
    DENIED = "denied"
    ERROR = "error"


GENESIS_HASH = "0" * 64


@dataclass(frozen=True)
class AuditEntry:
    seq: int
    timestamp: float          # unix time, UTC
    actor: str                 # e.g. "user:ansh" or "agent:executor"
    action: str                # e.g. "trigger_agent_run"
    resource: str               # e.g. "agent:summarizer"
    outcome: str
    details: dict
    prev_hash: str
    entry_hash: str = ""


def _hash_entry(seq: int, timestamp: float, actor: str, action: str,
                resource: str, outcome: str, details: dict, prev_hash: str) -> str:
    payload = json.dumps(
        {
            "seq": seq,
            "timestamp": timestamp,
            "actor": actor,
            "action": action,
            "resource": resource,
            "outcome": outcome,
            "details": details,
            "prev_hash": prev_hash,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


class AuditLogger:
    """
    Thread-safe, append-only audit logger backed by a JSON-lines file.

    Each call to log_event() appends exactly one line and never rewrites a
    previous line. verify_chain() recomputes every hash from the stored
    fields and confirms it both matches the stored hash and correctly
    chains to the next entry's prev_hash, so tampering with any single
    field of any past entry is detectable.
    """

    def __init__(self, path: str):
        self._path = path
        self._lock = threading.Lock()
        if not os.path.exists(self._path):
            # touch the file so verify_chain() on an empty log doesn't error
            with open(self._path, "a", encoding="utf-8"):
                pass

    def _last_hash(self) -> str:
        last = GENESIS_HASH
        if os.path.getsize(self._path) == 0:
            return last
        with open(self._path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                last = json.loads(line)["entry_hash"]
        return last

    def log_event(self, actor: str, action: str, resource: str,
                   outcome: Outcome, details: Optional[dict] = None) -> AuditEntry:
        details = details or {}
        with self._lock:
            prev_hash = self._last_hash()
            seq = self._next_seq()
            ts = time.time()
            entry_hash = _hash_entry(seq, ts, actor, action, resource, outcome.value, details, prev_hash)
            entry = AuditEntry(
                seq=seq, timestamp=ts, actor=actor, action=action, resource=resource,
                outcome=outcome.value, details=details, prev_hash=prev_hash, entry_hash=entry_hash,
            )
            with open(self._path, "a", encoding="utf-8") as f:
                f.write(json.dumps(asdict(entry), sort_keys=True))
                f.write("\n")
            return entry

    def _next_seq(self) -> int:
        if os.path.getsize(self._path) == 0:
            return 1
        last_seq = 0
        with open(self._path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                last_seq = json.loads(line)["seq"]
        return last_seq + 1

    def read_all(self) -> list:
        entries = []
        with open(self._path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                d = json.loads(line)
                entries.append(AuditEntry(**d))
        return entries

    def verify_chain(self):
        """
        Returns (True, "") if every entry's stored hash matches a hash
        recomputed from its own fields, and each entry's prev_hash matches
        the previous entry's entry_hash. Returns (False, reason) on the
        first break found.
        """
        expected_prev = GENESIS_HASH
        for entry in self.read_all():
            if entry.prev_hash != expected_prev:
                return False, f"entry {entry.seq}: prev_hash does not match preceding entry"
            recomputed = _hash_entry(
                entry.seq, entry.timestamp, entry.actor, entry.action,
                entry.resource, entry.outcome, entry.details, entry.prev_hash,
            )
            if recomputed != entry.entry_hash:
                return False, f"entry {entry.seq}: entry_hash does not match recomputed hash (tampered)"
            expected_prev = entry.entry_hash
        return True, ""
