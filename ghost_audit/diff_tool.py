import csv,json,sys
sys.path.insert(0,'/home/claude/proj')
from ghost_audit.evaluate import load
P=load(sys.argv[1]);G=load(sys.argv[2]);cases=sys.argv[3:] 
k=lambda s:(s['phase'][:3],s['system_id'][:8],s['resource_id'],s['operation'][:22],s['evidence_id'])
for c in cases:
    gs,g=G[c];ps,p=P[c]
    print('##',c,gs,ps)
    gk=[k(s) for s in g];pk=[k(s) for s in p]
    for x in gk:
        if x not in pk: print('  MISSING',x)
    for x in pk:
        if x not in gk: print('  EXTRA  ',x)
