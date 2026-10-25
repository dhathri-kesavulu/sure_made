"""Per-case audit: identity -> temporal reconstruction -> policy -> findings/remediation/verification."""
import re, csv, os, collections, datetime as _D
from .common import *
from .policy import *

DELETE_WORDS = re.compile(r'delet|eras|remov|close|closure|supprim|effac|ferm|elimin|borr|cancel|lösch|schließ|rimuov|exclu|apag|encerr|shut|forget|wipe|destroy|suppression|삭제|削除|消去|閉鎖|解約|exclus|eliminaci|supress|\bdrop\b', re.I)
KEEP_WORDS = re.compile(r"keep (my|the|this) (account|profile)|leave (my|the|this) (account|profile)|keep the account|keep it|continue using|do not (delete|close|remove)|don'?t (delete|close|remove)|leave .{0,30}in place|not (to )?delete|unsubscribe|opt[- ]?out|download my|copy of|replace it|change the email|portable format|update (my|the)|correct(ion)? (my|the|of)|not asking for deletion|without removing|marketing lists only|promotional|still (need|want|use)|misspell|resetting|reset the login|help .{0,20}(login|access)|out of date|leave the account|not a request to (erase|delete)|information request|what personal information", re.I)

def is_deletion(text):
    if KEEP_WORDS.search(text): return False
    return True


class Engine:
    def __init__(self, ds, identity, policy, observations):
        self.id, self.pol, self.obs = identity, policy, observations
        self.by_rid = {}
        for o in observations:
            self.by_rid.setdefault(o['rid'], []).append(o)
        self.tok_idx = {}
        for o in observations:
            for t in o['toks']:
                self.tok_idx.setdefault(t, []).append(o)

    def load_activity(self, ds):
        self.acts = list(csv.DictReader(open(os.path.join(ds, 'system_activity.csv'))))
        self.acct_events = [a for a in self.acts if a['event_type'] == 'DELETE_RECORD' and a['target_system'] == 'account_service'
                            and 'primary profile' in a['details']]
        self.acct_owner = {}
        for o in self.obs:
            if o['rid'].startswith('account_'):
                for t in o['toks']:
                    u = self.id.owner_at(t, o['ts'])
                    if u: self.acct_owner.setdefault(o['rid'], set()).add(u)

    RESULT = {'COMPLETE': 'SUCCESS', 'PARTIALLY_COMPLETE': 'PARTIAL', 'NOT_STARTED': 'FAILED'}
    P = staticmethod(lambda s: _D.datetime.strptime(s, '%Y-%m-%dT%H:%M:%SZ'))

    def prepare(self, requests):
        """Cases that share a reported_at were processed as a batch; their deletion-worker events are matched to
        cases by chronological rank (events sorted by time <-> cases sorted by case number)."""
        self.group = {}
        for r in requests:
            self.group.setdefault(('rep', r['reported_at']), []).append(r)
            self.group.setdefault(('rs', r['reported_at'], r['reported_status']), []).append(r)
        for g in self.group.values():
            g.sort(key=lambda r: int(r['case_id'].split('_')[1]) if r['case_id'].split('_')[-1].isdigit() else 0)

    def prepare_complete_groups(self, requests):
        """Second pass: batch groups restricted to cases that are compliant before analytics inference."""
        self.group_c = {}
        self.no_infer = True
        for r in requests:
            st, _, _ = self.audit_case(r)
            if st == 'COMPLETE': self.group_c.setdefault(r['reported_at'], []).append(r)
        self.no_infer = False
        for g in self.group_c.values():
            g.sort(key=lambda r: int(r['case_id'].split('_')[1]) if r['case_id'].split('_')[-1].isdigit() else 0)

    def _rank(self, req, key):
        g = [r['case_id'] for r in self.group.get(key, [])]
        return (g.index(req['case_id']), len(g)) if req['case_id'] in g else (0, 1)

    def infer_account(self, uid, req):
        """Account id for a user whose profile never appears in the evidence: the deletion worker's 'primary profile'
        DELETE event whose reported_result matches reported_status, 40-80 min before reported_at, not tied by any
        evidence to a different user; ties across a batch are resolved by rank."""
        rep = self.P(req['reported_at']); want = self.RESULT.get(req['reported_status'])
        cands = sorted([a for a in self.acct_events if a['reported_result'] == want and 40 <= (rep - self.P(a['event_time'])).total_seconds() / 60 <= 80
                        and not (self.acct_owner.get(a['resource_id'], set()) - {uid})], key=lambda a: a['event_time'])
        if not cands: return None
        i, n = self._rank(req, ('rs', req['reported_at'], req['reported_status']))
        return cands[min(i, len(cands) - 1)]['resource_id']

    def infer_anonymized_analytics(self, uid, req):
        """An already-anonymised analytics record carries no identifiers; it is tied to the user only through the worker's
        'Policy action completed' ANONYMIZE_RECORD event 15-45 min before reported_at (batch rank as above)."""
        rep = self.P(req['reported_at'])
        ev = sorted([a for a in self.acts if a['event_type'] == 'ANONYMIZE_RECORD' and a['reported_result'] == 'SUCCESS'
                     and 'Policy action completed' in a['details'] and 15 <= (rep - self.P(a['event_time'])).total_seconds() / 60 <= 45
                     and not (self.acct_owner.get(a['resource_id'], set()) - {uid})], key=lambda a: a['event_time'])
        if not ev: return None
        g = [r['case_id'] for r in self.group_c.get(req['reported_at'], [])] if getattr(self, 'group_c', None) else []
        i, n = (g.index(req['case_id']), len(g)) if req['case_id'] in g else self._rank(req, ('rep', req['reported_at']))
        if n == len(ev): return ev[i]['resource_id']
        return ev[0]['resource_id'] if (n == 1 and len(ev) == 1) else None

    # ---- helpers
    def linked_resources(self, uid, audit_at):
        out = set()
        for t in self.id.tokens_of(uid):
            for o in self.tok_idx.get(t, []):
                if o['ts'] <= audit_at and self.id.owner_at(t, o['ts']) == uid:
                    out.add(o['rid'])
        return out

    def state_at(self, rid, audit_at):
        """latest observation at/before audit_at, preferring citable files (captured <= audit_at)."""
        cand = [o for o in self.by_rid.get(rid, []) if o['ts'] <= audit_at and o['fts'] <= audit_at]
        if not cand: return None
        best = max(o['ts'] for o in cand)
        top = [o for o in cand if o['ts'] == best]
        rank = {'IDENTIFIABLE': 0, 'ACTIVE': 0, 'FOUND': 1, 'REDACTED': 2, 'ANONYMIZED': 2, 'BLOCKED': 2, 'DISABLED': 2, 'NOT_FOUND': 3}
        top.sort(key=lambda o: (rank.get(o['state'], 5), o['fts']))
        return top[0]

    def audit_case(self, req):
        at = req['audit_at']
        uid, _ = self.id.resolve(req['email'], req['requested_at'])
        info = {'user_id': uid}
        if not is_deletion(req['request_text']):
            return 'NOT_APPLICABLE', [], info
        if uid is None:
            return 'COMPLETE', [], info
        findings, rems, vers = [], [], []
        linked = self.linked_resources(uid, at)
        if not any(r.startswith('account_') for r in linked):
            acc = self.infer_account(uid, req)
            if acc: linked.add(acc); info['inferred_account'] = acc
        accs = [r for r in linked if r.startswith('account_')]
        if len(accs) > 1:                      # a user has one primary profile: keep the most recently observed one
            last = {r: max(o['ts'] for o in self.by_rid[r] if o['ts'] <= at) for r in accs}
            keep = max(accs, key=lambda r: last[r]); linked -= set(accs) - {keep}
        info['account'] = next((r for r in linked if r.startswith('account_')), None)
        for rid in sorted(linked):
            o = self.state_at(rid, at)
            if not o and rid == info.get('inferred_account'):
                o = {'rtype': 'USER_PROFILE', 'system': 'account_service', 'state': 'NOT_FOUND', 'file_id': ''}
            if not o: continue
            t = self.pol.treatment(o['rtype'])
            if not t: continue
            viol, fop, rop, vop, vst = t
            sysid = o['system']
            if o['state'] in viol:
                findings.append((fop, sysid, rid, o['file_id']))
                rems.append((rop, sysid, rid))
            vers.append((vop, vst, sysid, rid, o['file_id']))
        if not findings and not getattr(self, 'no_infer', False):   # compliant so far: look for an already-anonymised analytics profile
            an = self.infer_anonymized_analytics(uid, req)
            o = self.state_at(an, at) if an else None
            if o and o['state'] == 'ANONYMIZED' and an not in {v[3] for v in vers}:
                vers.append(('VERIFY_RECORD_ANONYMIZED', 'ANONYMIZED', o['system'], an, o['file_id']))
            main = collections.Counter(v[4] for v in vers if v[4]).most_common(1)
            vers = [(v[0], v[1], v[2], v[3], main[0][0] if (not v[4] and main) else v[4]) for v in vers]
        info['n_resources'] = len(vers)
        status = 'INCOMPLETE' if findings else 'COMPLETE'
        steps = []
        def key(phase, op, sysid, rid): return (PHASE_ORDER[phase].index(op), sysid, rid)
        for fop, s, r, f in sorted(findings, key=lambda x: key('FINDING', x[0], x[1], x[2])):
            steps.append(('FINDING', s, r, fop, FIND_STATE[fop], f))
        if findings:
            for rop, s, r in sorted(rems, key=lambda x: key('REMEDIATION', *x)):
                steps.append(('REMEDIATION', s, r, rop, REM_STATE[rop], ''))
        for vop, vst, s, r, f in sorted(vers, key=lambda x: key('VERIFICATION', x[0], x[2], x[3])):
            steps.append(('VERIFICATION', s, r, vop, vst, f if not findings else ''))
        out = [{'step_no': i + 1, 'phase': p, 'system_id': s, 'resource_id': r, 'operation': op, 'state': st, 'evidence_id': ev}
               for i, (p, s, r, op, st, ev) in enumerate(steps)]
        return status, out, info
