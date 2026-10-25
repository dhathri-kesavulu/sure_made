import csv, json, os, sys, pickle, argparse
from .evidence import build_observations
from .identity import Identity
from .policy import Policy
from .engine import Engine


def load(ds, cache=None):
    if cache and os.path.exists(cache):
        obs = pickle.load(open(cache, 'rb'))
    else:
        obs = build_observations(ds)
        if cache: pickle.dump(obs, open(cache, 'wb'))
    e = Engine(ds, Identity(ds), Policy(ds), obs); e.load_activity(ds); return e


def run(ds, requests_csv, out_csv, cache=None):
    eng = load(ds, cache)
    rows = []
    reqs = list(csv.DictReader(open(os.path.join(ds, requests_csv))))
    eng.prepare(reqs)
    eng.prepare_complete_groups(reqs)
    for req in reqs:
        st, steps, info = eng.audit_case(req)
        rows.append((req['case_id'], st, json.dumps(steps, separators=(',', ':'), ensure_ascii=False), info))
    rows.sort(key=lambda r: r[0])
    with open(out_csv, 'w', newline='', encoding='utf-8') as f:
        w = csv.writer(f, lineterminator='\n'); w.writerow(['case_id', 'deletion_status', 'steps_json'])
        for r in rows: w.writerow(r[:3])
    return rows


if __name__ == '__main__':
    ap = argparse.ArgumentParser(); ap.add_argument('--dataset', default='dataset'); ap.add_argument('--requests', default='deletion_requests.csv')
    ap.add_argument('--out', default='output.csv'); ap.add_argument('--cache', default=None)
    a = ap.parse_args(); run(a.dataset, a.requests, a.out, a.cache)
