# -*- coding: utf-8 -*-
import json, os

ROOT = os.path.dirname(os.path.abspath(__file__))
d = json.load(open(os.path.join(ROOT, 'aiWorkflow_detail.json'), encoding='utf-8'))['body']
ci = json.loads(d['canvasInfo'])
cells = ci['cells']

nodes = [c for c in cells if c.get('shape') != 'edge' and 'source' not in c]
edges = [c for c in cells if c.get('shape') == 'edge' or 'source' in c]
print('cells:', len(cells), '| nodes:', len(nodes), '| edges:', len(edges))

print('\n=== 第一个 node 的完整键 ===')
if nodes:
    n0 = nodes[0]
    print(list(n0.keys()))
    for k, v in n0.items():
        s = json.dumps(v, ensure_ascii=False) if not isinstance(v, str) else v
        print(' ', k, '=>', s[:200])

print('\n=== 第一个 edge 的完整内容 ===')
if edges:
    print(json.dumps(edges[0], ensure_ascii=False, indent=1)[:1500])
