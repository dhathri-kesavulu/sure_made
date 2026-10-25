"""Identity resolution over user_identities.csv + user_audit_log.csv.

Token set per user = current + historical email / email_hash / phone + user_id (+ sha256(email) for every email).
Temporal rule: a historical token is only attributed to the user for evidence observed at/before audit_at.
Contamination guard: a token claimed by >1 user is 'ambiguous'; it is attributed only to the user whose
ownership interval covers the evidence time, otherwise it is dropped (never silently shared).
"""
import csv, os, collections
from .common import *


class Identity:
    def __init__(self, ds):
        self.users = {r['user_id']: r for r in csv.DictReader(open(os.path.join(ds, 'user_identities.csv')))}
        self.audit = sorted(csv.DictReader(open(os.path.join(ds, 'user_audit_log.csv'))), key=lambda r: r['event_time'])
        self.intervals = collections.defaultdict(list)   # token -> [(user, start, end)]
        self._build()

    def _build(self):
        hist = collections.defaultdict(lambda: collections.defaultdict(list))
        for a in self.audit:
            hist[a['user_id']][a['field_name']].append(a)
        for uid, u in self.users.items():
            for field, tokf in (('email', norm_email), ('email_hash', str.lower), ('phone', norm_phone)):
                cur = u[field]
                chain = hist[uid].get(field, [])
                end = '9999'
                # walk backwards: current value valid from last change; old_value valid until that change
                if chain:
                    last = chain[-1]
                    self.intervals[tokf(last['new_value'])].append((uid, last['event_time'], end))
                    for i in range(len(chain) - 1, -1, -1):
                        a = chain[i]
                        start = chain[i - 1]['event_time'] if i > 0 else '0000'
                        self.intervals[tokf(a['old_value'])].append((uid, start, a['event_time']))
                else:
                    self.intervals[tokf(cur)].append((uid, '0000', end))
            for e in {u['email']} | {a['old_value'] for a in hist[uid].get('email', [])}:
                self.intervals[email_hash(e)].append((uid, '0000', '9999'))
            self.intervals[uid].append((uid, '0000', '9999'))
        self.amb = {t for t, iv in self.intervals.items() if len({i[0] for i in iv}) > 1}

    def emails_of(self, uid):
        u = self.users[uid]
        out = {norm_email(u['email'])}
        out |= {norm_email(a['old_value']) for a in self.audit if a['user_id'] == uid and a['field_name'] == 'email'}
        return out

    def tokens_of(self, uid):
        return {t for t, iv in self.intervals.items() if any(i[0] == uid for i in iv)}

    def resolve(self, email, at):
        """-> (user_id | None, evidence rows (audit_ids) used)"""
        e = norm_email(email)
        iv = self.intervals.get(e, [])
        if not iv:
            return None, []
        valid = [i for i in iv if i[1] <= at <= i[2]]
        cands = valid or iv
        return sorted(cands, key=lambda i: i[1])[-1][0], [e]

    def owner_at(self, token, ts):
        """user owning `token` at time `ts` (None if unknown, or ambiguous and no interval covers ts)."""
        iv = self.intervals.get(token, [])
        if not iv: return None
        if len({i[0] for i in iv}) == 1: return iv[0][0]
        cov = [i[0] for i in iv if i[1] <= ts <= i[2]]
        return cov[0] if len(set(cov)) == 1 else None

    def audit_trail(self, uid):
        return [a['audit_id'] for a in self.audit if a['user_id'] == uid]
