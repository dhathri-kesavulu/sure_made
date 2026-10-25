"""Evidence ingestion + normalisation.

Every evidence format (JSON, SQL snapshot, tuple text, logs in 6 dialects, OCR of PNG/PDF) is turned into
flat 'raw records' and then interpreted into uniform Observations:
    {file_id, ts, system, rid, rtype, state, toks, direct}
state vocabulary: FOUND NOT_FOUND IDENTIFIABLE REDACTED ANONYMIZED ACTIVE DISABLED BLOCKED
"""
import csv, json, os, re, sqlite3, subprocess, functools
from .common import *

KV = re.compile(r'([A-Za-z_][\w]*)=("(?:[^"\\]|\\.)*"|\S+)')
TS = re.compile(r'^(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ)')
TRACE = re.compile(r'\b(tr-[\w-]+|rb-\d+-\d+)\b')
NULLS = {None, '', 'null', 'None', 'NULL', 'none'}
EXPLICIT = {'FOUND', 'NOT_FOUND', 'IDENTIFIABLE', 'REDACTED', 'ANONYMIZED', 'ACTIVE', 'DISABLED', 'BLOCKED'}


def load_manifest(ds):
    return {r['file_id']: r for r in csv.DictReader(open(os.path.join(ds, 'evidence_files.csv')))}


def clean(v):
    if isinstance(v, str):
        v = v.strip().strip('"')
        return None if v in NULLS else v
    return v


# ------------------------------------------------------------------ JSON
def json_records(obj, ctx=None, out=None):
    out = [] if out is None else out
    ctx = dict(ctx or {})
    if isinstance(obj, dict):
        for k in ('captured_at', 'exported_at', 'data_store', 'collection', 'observed_at', 'recorded_at', 'query'):
            if k in obj and not isinstance(obj[k], (dict, list)):
                ctx[k if k != 'query' else 'query'] = obj[k]
        rid = obj.get('resource_id')
        if isinstance(rid, str) and rid_prefix(rid):
            if isinstance(obj.get('hits'), dict):          # search index query result
                docs = obj['hits'].get('documents') or []
                if not docs:
                    out.append({**ctx, 'resource_id': rid, 'result': 'NOT_FOUND', 'query': obj.get('query')})
                for d in docs:
                    out.append({**ctx, **flat(d), 'query': obj.get('query')})
                return out
            out.append({**ctx, **flat(obj)})
            return out
        for v in obj.values():
            json_records(v, ctx, out)
    elif isinstance(obj, list):
        for v in obj:
            json_records(v, ctx, out)
    return out


def flat(d, prefix=''):
    r = {}
    for k, v in d.items():
        if isinstance(v, dict):
            r.update(flat(v))
        elif isinstance(v, list):
            if all(isinstance(x, (str, int, float)) for x in v):
                r[k] = ' '.join(map(str, v))
            else:
                r[k] = json.dumps(v)
        else:
            r[k] = v
    if 'profile' in d and d['profile'] is None:
        r['profile'] = None
    return r


# ------------------------------------------------------------------ SQL
def sql_records(path):
    txt = open(path, encoding='utf-8', errors='replace').read()
    m = re.search(r'Captured at:\s*(\S+)', txt)
    cap = m.group(1) if m else None
    con = sqlite3.connect(':memory:')
    con.executescript(txt)
    recs = []
    scope = {}
    tables = [r[0] for r in con.execute("select name from sqlite_master where type='table'")]
    for t in tables:
        cols = [r[1] for r in con.execute(f'pragma table_info("{t}")')]
        for row in con.execute(f'select * from "{t}"'):
            d = dict(zip(cols, row))
            if t == 'snapshot_scope':
                scope[d['origin_system']] = d['captured_at']
                continue
            if 'record_json' in d:
                j = json.loads(d.pop('record_json'))
                d.update(flat(j)); d.setdefault('collection', d.get('collection_name'))
            d['_table'] = t
            d['captured_at'] = cap
            recs.append(d)
    return recs, scope, cap


# ------------------------------------------------------------------ tuple text / OCR text
TUPLE = re.compile(r'(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ)\s*\|\s*(\w+)\s*\|\s*(\S+)\s*\|\s*(\w+)\s*\|\s*(\w+)\s*\|\s*(\S.*?)\s*$')


def text_records(txt):
    out = []
    for line in txt.splitlines():
        m = TUPLE.search(line)
        if m:
            out.append({'recorded_at': m[1], 'system': m[2], 'resource_id': m[3], 'record_type': m[4], 'result': m[5],
                        'subject_reference': m[6]})
    return out


@functools.lru_cache(maxsize=None)
def ocr_text(path):
    try:
        if path.endswith('.pdf'):
            return subprocess.run(['pdftotext', '-layout', path, '-'], capture_output=True, text=True).stdout
        return subprocess.run(['tesseract', path, '-', '--psm', '6'], capture_output=True, text=True).stdout
    except Exception:
        return ''


# ------------------------------------------------------------------ logs
def parse_kv(s):
    d = {}
    for k, v in KV.findall(s):
        d[k] = clean(v.replace('\\"', '"'))
    return d


def log_line(line):
    line = line.rstrip('\n')
    m = TS.match(line)
    if not m:
        return None
    ts, rest = m.group(1), line[m.end():].strip()
    d = {'ts': ts}
    if rest.startswith('{'):
        try:
            j = json.loads(rest); d.update({k: clean(v) if not isinstance(v, (dict, list)) else v for k, v in j.items()})
            return d
        except Exception:
            pass
    if rest.startswith('|') is False and ' | ' in rest and 'event=' not in rest.split('|')[0]:
        parts = [p.strip() for p in rest.split(' | ')]
        if len(parts) >= 5 and not parts[0].startswith(('INFO', 'WARN')) or len(parts) >= 6:
            d['service'], d['trace_id'], d['event'] = parts[2], parts[3], parts[4]
            d.update(parse_kv(' | '.join(parts[5:])))
            return d
    m2 = re.match(r'\[(\w+)\]\s*\[(\w+)\]\s*\[(.+?)\]\s*(\w+):\s*(.*)', rest)
    if m2:
        d.update({'service': m2[2], 'trace_id': m2[3], 'event': m2[4]}); d.update(parse_kv(m2[5])); return d
    if 'message="' in rest:                                 # cloud_runtime_export
        pre, msg = rest.split('message=', 1)
        d.update(parse_kv(pre)); d.update(parse_kv(msg.strip()[1:-1].replace('\\"', '"') if msg.strip().startswith('"') else msg))
        d['trace_id'] = d.get('requestId'); d['service'] = d.get('function'); return d
    msy = re.match(r'<\d+>1\s+(\S+)\s+(\S+)\s+(\S+)\s+-\s+-\s*(.*)', rest)
    if msy:
        d.update({'service': msy[2], 'trace_id': msy[3]}); d.update(parse_kv(msy[4])); return d
    d.update(parse_kv(rest))
    return d


def log_records(path):
    lines = [x for x in (log_line(l) for l in open(path, encoding='utf-8', errors='replace')) if x]
    by_trace = {}
    out = []
    for d in lines:
        tid = d.get('trace_id') or (TRACE.search(str(d)) or [None])[0]
        d['trace_id'] = tid
        if d.get('event') == 'record_lookup':
            out.append({'recorded_at': d['ts'], 'system': d.get('system'), 'resource_id': d.get('resource_id'),
                        'record_type': d.get('record_type'), 'result': d.get('result'), 'subject_reference': d.get('subject_reference')})
            continue
        by_trace.setdefault(tid, []).append(d)
    for tid, ds in by_trace.items():
        if tid is None:
            continue
        keys = {}
        for d in ds:                                           # context from "started" lines
            for k in ('lookup_key', 'match_key', 'subject_key', 'scan_key', 'owner_key', 'subject_ref', 'correlation_key'):
                if d.get(k): keys.setdefault('_keys', set()).add(d[k])
        for d in ds:
            rid = d.get('resource_id')
            if rid and rid_prefix(rid) and d.get('event', '').endswith(('completed', 'read', 'scan', 'loaded', 'evaluated', 'heartbeat', 'check', 'metadata_read_completed')) or (rid and rid_prefix(rid) and d.get('event') not in ('thumbnail_failed',)):
                r = dict(d); r['_keys'] = set(keys.get('_keys', set())); r['recorded_at'] = d['ts']
                out.append(r)
    return out


# ------------------------------------------------------------------ interpretation
def _bool(v):
    return str(v).lower() in ('true', '1', 'yes', 'allowed', 'authorized')


def interpret(rec):
    """flat raw record -> (rid, rtype, state, toks, direct) or None"""
    rid = rec.get('resource_id')
    p = rid_prefix(rid) if isinstance(rid, str) else None
    if not p:
        return None
    system, rtype = PREFIX[p]
    if rec.get('record_type'):
        rtype = rec['record_type']
    toks, direct = set(), set()
    skip = {'resource_id', 'ticket_id', 'file_name', 'archive_key', 'archive_path', 'object_key', 'trace_id', 'integrity_seal', 'service', 'ts'}
    for k, v in rec.items():
        if k in skip or k.startswith('_') and k != '_keys':
            continue
        vs = list(v) if k == '_keys' else [v]
        for x in vs:
            if isinstance(x, str) and x not in NULLS:
                t = tokens_in(x)
                toks |= t
                if k in ('requester_name', 'display_name', 'full_name', 'name') and x:
                    direct.add('name:' + x.lower())
    direct |= {t for t in toks if not t.startswith('user_')}
    ev = rec.get('event') or ''
    state = None
    if rec.get('result') in EXPLICIT:
        state = rec['result']
    elif rec.get('result') in ('hit',):
        state = 'FOUND'
    elif rec.get('result') in ('miss',):
        state = 'NOT_FOUND'
    elif p in ('account', 'tombstone'):
        status = str(rec.get('account_status') or '').upper()
        if 'status_code' in rec or 'rows' in rec or 'profile' in rec:
            nf = str(rec.get('status_code')) == '404' or str(rec.get('http_status')) == '404' or str(rec.get('rows')) == '0' or ('profile' in rec and rec['profile'] is None and 'status_code' in rec and not toks)
            state = 'NOT_FOUND' if nf else ('REDACTED' if (status == 'DELETED' and not direct) else 'FOUND')
        elif status == 'DELETED' or p == 'tombstone':
            rtype = 'ACCOUNT_TOMBSTONE'; state = 'IDENTIFIABLE' if direct else 'REDACTED'
        else:
            state = 'FOUND'
    elif p == 'search':
        state = 'FOUND'
    elif p == 'support':
        if rec.get('file_name') or rec.get('uploaded_by') or ev.startswith('attachment'):
            rtype = 'SUPPORT_ATTACHMENT'; state = 'FOUND'
        else:
            state = 'IDENTIFIABLE' if direct else 'REDACTED'
    elif p in ('analytics', 'telemetry') and 'email_opt_in' in rec:
        rtype = 'MARKETING_PROFILE'; state = 'FOUND'
    elif p in ('analytics', 'telemetry'):
        if str(rec.get('direct_identifiers')) == '0' and not toks: state = 'ANONYMIZED'
        else: state = 'IDENTIFIABLE' if toks else 'ANONYMIZED'
    elif p == 'email_delivery':
        state = 'IDENTIFIABLE' if direct else 'REDACTED'
    elif p == 'export':
        av = rec.get('download_available', rec.get('available'))
        state = 'NOT_FOUND' if av is not None and not _bool(av) else 'FOUND'
    elif p == 'backup':
        if 'restore_allowed' in rec: state = 'ACTIVE' if _bool(rec['restore_allowed']) else 'BLOCKED'
        elif 'eligible' in rec: state = 'ACTIVE' if _bool(rec['eligible']) and not _bool(rec.get('deletion_block')) else 'BLOCKED'
        elif 'decision' in rec: state = 'ACTIVE' if str(rec['decision']).lower() in ('allowed', 'authorized', 'approve', 'approved', 'allow') else 'BLOCKED'
        else: state = 'ACTIVE'
    elif p == 'data_flow':
        en = rec.get('enabled', rec.get('scheduler_registered'))
        state = 'ACTIVE' if en is not None and _bool(en) else ('DISABLED' if en is not None else 'ACTIVE')
    else:                                                       # audit_record, legal_hold, cache, marketing
        state = 'FOUND'
    return rid, system, rtype, state, toks, direct


def rec_ts(rec, fallback):
    for k in ('observed_at', 'recorded_at', 'updated_at', 'indexed_at', 'ts'):
        v = rec.get(k)
        if isinstance(v, str) and TS.match(v): return v
    v = rec.get('captured_at') or rec.get('exported_at')
    return v if isinstance(v, str) and TS.match(v) else fallback


def build_observations(ds):
    man = load_manifest(ds)
    obs = []
    for fid, m in man.items():
        path = os.path.join(ds, m['file_path']); ext = path.rsplit('.', 1)[-1]
        cap = m['captured_at']
        try:
            if ext == 'json':
                recs = json_records(json.load(open(path)))
            elif ext == 'sql':
                recs, scope, c2 = sql_records(path)
            elif ext == 'log':
                recs = log_records(path)
            elif ext in ('txt', 'png', 'pdf'):
                txt = open(path).read() if ext == 'txt' else ocr_text(path)
                recs = text_records(txt)
                if ext != 'txt':
                    recs += ocr_free_records(txt, cap)
            else:
                recs = []
        except Exception as e:                                   # never let one bad file kill the run
            print('WARN parse failed', fid, e)
            recs = []
        for r in recs:
            if r.get('subject_reference') and 'resource_id' in r and r.get('result'):
                r = dict(r); r['_keys'] = {r['subject_reference']}
                r['subject_reference'] = r['subject_reference']
            if r.get('system') and r.get('record_type') is None: pass
            it = interpret(r)
            if not it: continue
            rid, system, rtype, state, toks, direct = it
            sysid = r.get('system') if r.get('system') in {v[0] for v in PREFIX.values()} else system
            obs.append({'file_id': fid, 'fts': cap, 'ts': rec_ts(r, cap), 'system': sysid, 'rid': rid, 'rtype': rtype,
                        'state': state, 'toks': frozenset(toks), 'direct': frozenset(direct)})
    return obs


def ocr_free_records(txt, cap):
    """Images/PDFs: pull any resource id with nearby identifier tokens (one record per line containing a resource id)."""
    out = []
    for line in txt.splitlines():
        for m in RID_RE.finditer(line):
            out.append({'resource_id': m.group(0), 'captured_at': cap, **{'_keys': tokens_in(line)}})
    return out
