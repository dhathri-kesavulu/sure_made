"""Shared constants: resource-prefix map, token normalisation, policy table (from deletion_rules.csv)."""
import re, hashlib

# resource_id prefix -> (system_id, default data_category).  Verified against system_activity.csv
# (source/target systems) and the evidence files.
PREFIX = {
    'account': ('account_service', 'USER_PROFILE'),
    'tombstone': ('identity_service', 'ACCOUNT_TOMBSTONE'),
    'search': ('search_service', 'SEARCH_INDEX_ENTRY'),
    'cache': ('cache_service', 'CACHE_ENTRY'),
    'support': ('support_platform', 'SUPPORT_MESSAGE'),
    'analytics': ('analytics_platform', 'ANALYTICS_PROFILE'),
    'telemetry': ('analytics_platform', 'PRODUCT_TELEMETRY'),
    'export': ('export_service', 'USER_EXPORT'),
    'marketing': ('marketing_platform', 'MARKETING_PROFILE'),
    'email_delivery': ('email_delivery_service', 'EMAIL_DELIVERY_LOG'),
    'audit_record': ('security_service', 'SECURITY_AUDIT_LOG'),
    'legal_hold': ('legal_service', 'LEGAL_HOLD_RECORD'),
    'legal': ('legal_service', 'LEGAL_HOLD_RECORD'),
    'backup': ('backup_service', 'BACKUP_ARCHIVE'),
    'data_flow': ('integration_service', 'DATA_FLOW'),
}
RID_RE = re.compile(r'\b(account|tombstone|search|cache|support|analytics|telemetry|export|marketing|email_delivery|audit_record|legal_hold|backup|data_flow)_([A-Za-z0-9]+)\b')
EMAIL_RE = re.compile(r'[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}')
HASH_RE = re.compile(r'\b[0-9a-f]{64}\b')
USER_RE = re.compile(r'\buser_\d+\b')
PHONE_RE = re.compile(r'\+\d[\d ()\-]{6,}\d')

def rid_prefix(rid):
    m = RID_RE.fullmatch(rid or '')
    return m.group(1) if m else None

def norm_email(e):
    return e.strip().lower()

def email_hash(e):
    return hashlib.sha256(norm_email(e).encode()).hexdigest()

def norm_phone(p):
    d = re.sub(r'\D', '', p or '')
    return '+' + d if d else ''

def tokens_in(text):
    """Identifier tokens found in a free-text string (emails, hashes, user ids, phones)."""
    out = set()
    if not text or not isinstance(text, str):
        return out
    for e in EMAIL_RE.findall(text): out.add(norm_email(e))
    for h in HASH_RE.findall(text.lower()): out.add(h)
    for u in USER_RE.findall(text): out.add(u)
    for p in PHONE_RE.findall(text): out.add(norm_phone(p))
    return out

# Policy-as-code: loaded from deletion_rules.csv at runtime (see policy.py); this maps category -> treatment
# only as a *fallback* sanity table and is cross-checked against the CSV on load.
