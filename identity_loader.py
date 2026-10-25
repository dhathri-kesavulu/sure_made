"""Build one IdentityGraph from user_identities.csv + user_audit_log.csv.

!! The real column names and audit event types were NOT available when this was written.
!! Everything below marked ASSUMED is a placeholder: open the two CSVs, then edit
!! IdentitiesMap / AuditMap (or pass your own instances). Missing columns raise immediately.
"""
from __future__ import annotations

import csv
from dataclasses import dataclass, field
from pathlib import Path

from .identity_graph import Confidence, IdentityGraph, Kind, parse_ts


@dataclass
class IdentitiesMap:                      # ASSUMED column names
    user_id: str = "user_id"
    email: str = "email"
    valid_from: str | None = None         # set to a column name if the file has one
    valid_to: str | None = None
    extra_identifiers: dict = field(default_factory=dict)   # {"phone": Kind.PHONE, ...}


@dataclass
class AuditMap:                           # ASSUMED column names and event vocabulary
    user_id: str = "user_id"
    timestamp: str = "timestamp"
    event: str = "event_type"
    old_value: str = "old_value"
    new_value: str = "new_value"
    email_change_events: tuple = ("EMAIL_CHANGED",)
    alias_events: tuple = ()              # e.g. ("ACCOUNT_MERGED",) if the dataset has them


def _check(reader: csv.DictReader, needed: list, name: str) -> None:
    missing = [c for c in needed if c and c not in (reader.fieldnames or [])]
    if missing:
        raise KeyError(f"{name}: missing columns {missing}; found {reader.fieldnames}")


def load_identities(g: IdentityGraph, path: Path, m: IdentitiesMap = IdentitiesMap(),
                    assume_utc: bool = False) -> int:
    n = 0
    with open(path, newline="", encoding="utf-8") as f:
        r = csv.DictReader(f)
        _check(r, [m.user_id, m.email, m.valid_from, m.valid_to, *m.extra_identifiers], path.name)
        for i, row in enumerate(r, start=2):          # row 1 is the header
            src = f"{path.name}:row {i}"
            vf = parse_ts(row[m.valid_from], assume_utc) if m.valid_from else None
            vt = parse_ts(row[m.valid_to], assume_utc) if m.valid_to else None
            if row[m.email].strip():
                g.add_ownership(row[m.user_id], Kind.EMAIL, row[m.email], src, vf, vt)
                n += 1
            for col, kind in m.extra_identifiers.items():
                if row[col].strip():
                    g.add_ownership(row[m.user_id], kind, row[col], src, vf, vt)
                    n += 1
    return n


def load_audit_log(g: IdentityGraph, path: Path, m: AuditMap = AuditMap(),
                   assume_utc: bool = False) -> int:
    """Events are applied in timestamp order so email changes chain correctly."""
    with open(path, newline="", encoding="utf-8") as f:
        r = csv.DictReader(f)
        _check(r, [m.user_id, m.timestamp, m.event, m.old_value, m.new_value], path.name)
        rows = [(parse_ts(row[m.timestamp], assume_utc), i, row) for i, row in enumerate(r, start=2)]
    rows.sort(key=lambda t: (t[0], t[1]))
    n = 0
    for ts, i, row in rows:
        src = f"{path.name}:row {i}"
        ev = row[m.event].strip()
        if ev in m.email_change_events:
            g.change_email(row[m.user_id], row[m.old_value], row[m.new_value], ts, src)
            n += 1
        elif ev in m.alias_events:
            g.add_alias(row[m.old_value], row[m.new_value], src, valid_from=ts)
            n += 1
    return n


def build_graph(dataset_dir: Path, ident_map=IdentitiesMap(), audit_map=AuditMap(),
                assume_utc: bool = False, hash_algos=("md5", "sha1", "sha256")) -> IdentityGraph:
    """Build once, reuse for all 518 cases."""
    g = IdentityGraph(hash_algos)
    load_identities(g, dataset_dir / "user_identities.csv", ident_map, assume_utc)
    load_audit_log(g, dataset_dir / "user_audit_log.csv", audit_map, assume_utc)
    g.detect_conflicts()
    return g
