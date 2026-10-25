"""Compare predictions with sample_output.csv: status, step-level, field-level and sequence accuracy."""
import csv, json, sys


def load(p): return {r['case_id']: (r['deletion_status'], json.loads(r['steps_json'])) for r in csv.DictReader(open(p))}
F = ['phase', 'system_id', 'resource_id', 'operation', 'state', 'evidence_id']


def evaluate(pred_csv, gold_csv, verbose=False):
    P, G = load(pred_csv), load(gold_csv)
    n = len(G); st = 0; exact = 0; steps_tp = steps_p = steps_g = 0; fld = {f: [0, 0] for f in F}; seq = 0; seq_t = 0
    bad = []
    for c, (gs, gst) in G.items():
        ps, pst = P.get(c, ('MISSING', []))
        st += ps == gs
        key = lambda s: tuple(s[f] for f in F)
        gk = [key(s) for s in gst]; pk = [key(s) for s in pst]
        steps_tp += len(set(gk) & set(pk)); steps_p += len(set(pk)); steps_g += len(set(gk))
        for g in gst:
            m = next((p for p in pst if (p['phase'], p['system_id'], p['resource_id'], p['operation']) == (g['phase'], g['system_id'], g['resource_id'], g['operation'])), None)
            for f in F:
                fld[f][1] += 1; fld[f][0] += bool(m and m[f] == g[f])
        seq_t += 1; seq += gk == pk
        exact += (gs == ps and gk == pk)
        if not (gs == ps and gk == pk): bad.append(c)
    r = {'cases': n, 'status_acc': st / n, 'exact_case_acc': exact / n, 'step_precision': steps_tp / max(steps_p, 1), 'step_recall': steps_tp / max(steps_g, 1),
         'sequence_acc': seq / seq_t, 'field_acc': {f: v[0] / max(v[1], 1) for f, v in fld.items()}, 'mismatched_cases': bad}
    return r


if __name__ == '__main__':
    import pprint; pprint.pprint(evaluate(sys.argv[1], sys.argv[2]))
