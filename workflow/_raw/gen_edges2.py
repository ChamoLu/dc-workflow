# -*- coding: utf-8 -*-
import json, os
ROOT = os.path.dirname(os.path.abspath(__file__))
g = json.load(open(os.path.join(ROOT, 'graph.json'), encoding='utf-8'))
s = json.load(open(os.path.join(ROOT, 'node_summary.json'), encoding='utf-8'))

def t(nid):
    return nid if nid in ('start', 'end') else nid.split('_')[-1]

def key(nid):
    p = s[nid]['position']
    return (p['x'], p['y'])

def nm(nid):
    return '%s(%s)' % (s[nid].get('displayName', ''), t(nid))

edges = g['edges']
# 按来源节点位置排序，再按目标节点位置
edges = sorted(edges, key=lambda e: (key(e['source']), key(e['target'])))
lines = []
for i, e in enumerate(edges, 1):
    lines.append('| %d | %s | %s | %s |' % (
        i, nm(e['source']), e.get('label') or '—', nm(e['target'])))
open(os.path.join(ROOT, '_edges_rows.md'), 'w', encoding='utf-8').write('\n'.join(lines))
print('real edges:', len(edges))
