"""Temporal, evidence-backed identity graph (stdlib only, adjacency lists).

Design rules (all enforced in code, all covered by tests):
  * An edge exists only with provenance (source string such as "user_audit_log.csv:row 17").
  * Only CONFIRMED edges are traversed. UNVERIFIED edges are recorded as leads, never followed.
  * Every edge has a half-open validity interval [start, end); None = unbounded.
  * Exclusive identifiers (email, phone) may belong to one user at a time. Overlapping
    ownership by two users is a CONFLICT: both edges are quarantined and not traversed.
  * User IDs are stable anchors. Identifier-to-identifier hops must stay temporally
    consistent (interval intersection must be non-empty), which blocks cross-user merges
    caused by identifier reuse.
  * No fuzzy matching. Emails are only trimmed and lowercased, nothing else.
"""
from __future__ import annotations

import hashlib
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Iterable, Optional

Interval = tuple  # (start: datetime | None, end: datetime | None)
UNBOUNDED: Interval = (None, None)


class Kind(str, Enum):
    USER_ID = "USER_ID"
    EMAIL = "EMAIL"
    EMAIL_HASH = "EMAIL_HASH"
    PHONE = "PHONE"
    OTHER = "OTHER"


EXCLUSIVE_KINDS = {Kind.EMAIL, Kind.PHONE}


class Confidence(str, Enum):
    CONFIRMED = "CONFIRMED"
    UNVERIFIED = "UNVERIFIED"


class Relation(str, Enum):
    OWNS = "OWNS"          # user -> identifier
    HASH_OF = "HASH_OF"    # hash -> email (deterministic, derived)
    ALIAS = "ALIAS"        # user <-> user (explicit merge evidence only)


Node = tuple  # (Kind, normalized value)


# ---------- time helpers ----------
def parse_ts(value, assume_utc: bool = False) -> Optional[datetime]:
    """Parse ISO-8601 into an aware UTC datetime. Naive values are rejected unless
    assume_utc=True, so a wrong timezone convention can never pass silently."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        s = str(value).strip().replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        if not assume_utc:
            raise ValueError(f"Naive timestamp {value!r}; set assume_utc=True only if the dataset says UTC")
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def intersect(a: Interval, b: Interval) -> Optional[Interval]:
    start = a[0] if b[0] is None else b[0] if a[0] is None else max(a[0], b[0])
    end = a[1] if b[1] is None else b[1] if a[1] is None else min(a[1], b[1])
    if start is not None and end is not None and start >= end:
        return None
    return (start, end)


def contains(iv: Interval, t: datetime) -> bool:
    return (iv[0] is None or iv[0] <= t) and (iv[1] is None or t < iv[1])


def normalize(kind: Kind, value: str) -> str:
    v = str(value).strip()
    if kind in (Kind.EMAIL, Kind.EMAIL_HASH):
        v = v.lower()
    elif kind == Kind.PHONE:
        v = "".join(ch for ch in v if ch.isdigit() or ch == "+")
    return v


def node(kind: Kind, value: str) -> Node:
    return (kind, normalize(kind, value))


# ---------- data model ----------
@dataclass
class Edge:
    edge_id: int
    a: Node
    b: Node
    relation: Relation
    interval: Interval
    confidence: Confidence
    sources: list = field(default_factory=list)

    def other(self, n: Node) -> Node:
        return self.b if n == self.a else self.a


@dataclass
class Discovery:
    node: Node
    anchor_user: Optional[str]   # last stable user the path passed through
    interval: Interval           # period during which this link is valid
    path: tuple                  # edge_ids walked, for provenance


@dataclass
class Conflict:
    identifier: Node
    edge_ids: tuple
    users: tuple
    overlap: Interval


@dataclass
class TraversalResult:
    seed: Node
    window: Interval
    discoveries: list
    skipped: list     # (edge_id, reason) for UNVERIFIED / QUARANTINED / out-of-window edges
    conflicts: list

    def identifiers_for(self, user_id: str, kinds: Optional[Iterable[Kind]] = None) -> dict:
        """identifier node -> list of validity intervals, for one user."""
        out = defaultdict(list)
        for d in self.discoveries:
            if d.anchor_user == user_id and d.node[0] != Kind.USER_ID:
                if kinds is None or d.node[0] in set(kinds):
                    out[d.node].append(d.interval)
        return dict(out)

    @property
    def users(self) -> set:
        return {d.node[1] for d in self.discoveries if d.node[0] == Kind.USER_ID}

    @property
    def is_ambiguous(self) -> bool:
        return bool(self.conflicts) or any(r in ("QUARANTINED", "UNVERIFIED") for _, r in self.skipped)


class IdentityGraph:
    def __init__(self, hash_algos: tuple = ("md5", "sha1", "sha256")):
        self.hash_algos = hash_algos
        self._edges: dict = {}
        self._adj: dict = defaultdict(list)       # node -> [edge_id]
        self._next_id = 1
        self._dirty = True
        self._quarantined: set = set()
        self._conflicts: list = []

    # ----- building -----
    def _add_edge(self, a, b, relation, interval, confidence, source) -> Edge:
        for eid in self._adj[a]:  # merge exact duplicates, keep all provenance
            e = self._edges[eid]
            if e.other(a) == b and e.relation == relation and e.interval == interval \
                    and e.confidence == confidence:
                if source and source not in e.sources:
                    e.sources.append(source)
                return e
        e = Edge(self._next_id, a, b, relation, interval, confidence, [source] if source else [])
        self._next_id += 1
        self._edges[e.edge_id] = e
        self._adj[a].append(e.edge_id)
        self._adj[b].append(e.edge_id)
        self._dirty = True
        return e

    def add_ownership(self, user_id: str, kind: Kind, value: str, source: str,
                      valid_from=None, valid_to=None,
                      confidence: Confidence = Confidence.CONFIRMED) -> Edge:
        if not source:
            raise ValueError("Every identity edge needs provenance (source)")
        if kind == Kind.USER_ID:
            raise ValueError("Use add_alias for user-to-user links")
        u, ident = node(Kind.USER_ID, user_id), node(kind, value)
        e = self._add_edge(u, ident, Relation.OWNS, (valid_from, valid_to), confidence, source)
        if kind == Kind.EMAIL:
            self._add_hashes(ident, source)
        return e

    def _add_hashes(self, email_node: Node, source: str) -> None:
        for algo in self.hash_algos:
            h = hashlib.new(algo, email_node[1].encode("utf-8")).hexdigest()
            self._add_edge(node(Kind.EMAIL_HASH, h), email_node, Relation.HASH_OF, UNBOUNDED,
                           Confidence.CONFIRMED, f"derived:{algo}({source})")

    def add_alias(self, user_a: str, user_b: str, source: str, valid_from=None, valid_to=None,
                  confidence: Confidence = Confidence.CONFIRMED) -> Edge:
        if not source:
            raise ValueError("Every identity edge needs provenance (source)")
        return self._add_edge(node(Kind.USER_ID, user_a), node(Kind.USER_ID, user_b),
                              Relation.ALIAS, (valid_from, valid_to), confidence, source)

    def change_email(self, user_id: str, old: str, new: str, at: datetime, source: str) -> None:
        """Close the old email's ownership at `at` and open the new one from `at`."""
        u, old_n = node(Kind.USER_ID, user_id), node(Kind.EMAIL, old)
        closed = False
        for eid in self._adj[u]:
            e = self._edges[eid]
            if e.relation == Relation.OWNS and e.other(u) == old_n and e.interval[1] is None \
                    and e.confidence == Confidence.CONFIRMED:
                e.interval = (e.interval[0], at)
                e.sources.append(source)
                closed = True
        if not closed:  # start of ownership unknown, but it ended at `at`
            self.add_ownership(user_id, Kind.EMAIL, old, source, None, at)
        self.add_ownership(user_id, Kind.EMAIL, new, source, at, None)
        self._dirty = True

    # ----- conflicts -----
    def detect_conflicts(self) -> list:
        if not self._dirty:
            return self._conflicts
        self._conflicts, self._quarantined = [], set()
        for n, eids in self._adj.items():
            if n[0] not in EXCLUSIVE_KINDS:
                continue
            owns = [self._edges[i] for i in eids if self._edges[i].relation == Relation.OWNS
                    and self._edges[i].confidence == Confidence.CONFIRMED]
            for i in range(len(owns)):
                for j in range(i + 1, len(owns)):
                    ea, eb = owns[i], owns[j]
                    ua, ub = ea.other(n)[1], eb.other(n)[1]
                    if ua == ub:
                        continue
                    ov = intersect(ea.interval, eb.interval)
                    if ov is not None:
                        self._conflicts.append(Conflict(n, (ea.edge_id, eb.edge_id), (ua, ub), ov))
                        self._quarantined |= {ea.edge_id, eb.edge_id}
        self._dirty = False
        return self._conflicts

    # ----- traversal -----
    def traverse(self, seed: Node, window: Interval = UNBOUNDED, at: Optional[datetime] = None
                 ) -> TraversalResult:
        """BFS from `seed`. `at` narrows the window to a single instant (e.g. audit_at or an
        evidence timestamp); `window` is the period of interest."""
        conflicts = self.detect_conflicts()
        if at is not None:
            window = (at, at)  # point-in-time: only edges whose interval contains `at`
        skipped, found = [], []
        seen = set()
        q = deque([(seed, window, seed[1] if seed[0] == Kind.USER_ID else None, ())])
        while q:
            n, carried, anchor, path = q.popleft()
            key = (n, carried, anchor)
            if key in seen:
                continue
            seen.add(key)
            found.append(Discovery(n, anchor, carried, path))
            for eid in self._adj.get(n, ()):
                e = self._edges[eid]
                if eid in path:
                    continue
                if e.confidence != Confidence.CONFIRMED:
                    skipped.append((eid, "UNVERIFIED"))
                    continue
                if eid in self._quarantined:
                    skipped.append((eid, "QUARANTINED"))
                    continue
                nxt = e.other(n)
                new_iv = self._clip(carried, e.interval, window)
                if new_iv is None:
                    skipped.append((eid, "OUT_OF_WINDOW"))
                    continue
                if nxt[0] == Kind.USER_ID and e.relation == Relation.OWNS:
                    # arriving at a stable user anchor: reset to the query window
                    q.append((nxt, window, nxt[1], path + (eid,)))
                    # record the user hit, and credit the identifier we came from to that user
                    found.append(Discovery(nxt, nxt[1], new_iv, path + (eid,)))
                    if n[0] != Kind.USER_ID:
                        found.append(Discovery(n, nxt[1], new_iv, path + (eid,)))
                elif nxt[0] == Kind.USER_ID:  # ALIAS hop
                    q.append((nxt, new_iv, nxt[1], path + (eid,)))
                else:
                    q.append((nxt, new_iv, anchor if anchor else
                              (n[1] if n[0] == Kind.USER_ID else None), path + (eid,)))
        return TraversalResult(seed, window, _dedupe(found), _dedupe_skips(skipped), conflicts)

    @staticmethod
    def _clip(carried: Interval, edge_iv: Interval, window: Interval) -> Optional[Interval]:
        if carried == (window[0], window[1]) and window[0] is not None and window[0] == window[1]:
            # point-in-time query: edge must contain the instant
            return carried if contains(edge_iv, window[0]) else None
        return intersect(carried, edge_iv)

    # ----- convenience -----
    def resolve_owners(self, kind: Kind, value: str, at: Optional[datetime] = None) -> dict:
        """Which users held this identifier (at an instant, or ever)? user -> [intervals].
        Direct, non-transitive lookup; conflicting edges are excluded and reported."""
        self.detect_conflicts()
        n = node(kind, value)
        out = defaultdict(list)
        for eid in self._adj.get(n, ()):
            e = self._edges[eid]
            if e.relation != Relation.OWNS or e.confidence != Confidence.CONFIRMED \
                    or eid in self._quarantined:
                continue
            if at is None or contains(e.interval, at):
                out[e.other(n)[1]].append(e.interval)
        return dict(out)

    def edge(self, edge_id: int) -> Edge:
        return self._edges[edge_id]

    def provenance(self, discovery: Discovery) -> list:
        return [(self._edges[i].relation.value, self._edges[i].sources) for i in discovery.path]

    @property
    def conflicts(self) -> list:
        return self.detect_conflicts()


def _dedupe(ds: list) -> list:
    seen, out = set(), []
    for d in ds:
        k = (d.node, d.anchor_user, d.interval)
        if k not in seen:
            seen.add(k)
            out.append(d)
    return out


def _dedupe_skips(skips: list) -> list:
    return sorted(set(skips))
