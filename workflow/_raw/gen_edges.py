# -*- coding: utf-8 -*-
import json, os
ROOT = os.path.dirname(os.path.abspath(__file__))
g = json.load(open(os.path.join(ROOT, 'graph.json'), encoding='utf-8'))
s = json.load(open(os.path.join(ROOT, 'node_summary.json'), encoding='utf-8'))

def t(nid):
    return nid if nid in ('start', 'end') else nid.split('_')[-1]

def nm(nid):
    return '%s(%s)' % (s[nid].get('displayName', ''), t(nid))

edges = g['edges']
lines = ['# 附录 · 连线明细与字段速查\n',
         '## 1. 全部 59 条连线（含分支标签）\n',
         '| # | 来源节点 | 分支标签 | 目标节点 |',
         '|--:|--|--|--|']
for i, e in enumerate(edges, 1):
    lines.append('| %d | %s | %s | %s |' % (
        i, nm(e['source']), e.get('label') or '—', nm(e['target'])))
open(os.path.join(ROOT, '_edges_table.md'), 'w', encoding='utf-8').write('\n'.join(lines))
print('edges:', len(edges))
