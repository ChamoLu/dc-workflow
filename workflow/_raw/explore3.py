# -*- coding: utf-8 -*-
import json, os

ROOT = os.path.dirname(os.path.abspath(__file__))
d = json.load(open(os.path.join(ROOT, 'aiWorkflow_detail.json'), encoding='utf-8'))['body']
ci = json.loads(d['canvasInfo'])
cells = ci['cells']

nodes = [c for c in cells if c.get('shape') != 'edge']
edges = [c for c in cells if c.get('shape') == 'edge']

print('=== 节点 data 字段（取每种 shape 一个样本）===')
seen_shape = {}
for n in nodes:
    sh = n.get('shape')
    if sh not in seen_shape:
        seen_shape[sh] = n
for sh, n in seen_shape.items():
    data = n.get('data', {})
    print('\n##### shape =', sh, '| id =', n['id'])
    print(' data keys:', list(data.keys()))
    for k, v in data.items():
        s = json.dumps(v, ensure_ascii=False) if not isinstance(v, str) else v
        print('   ', k, '=>', s[:160].replace('\n','\\n'))

print('\n\n=== 全部节点清单 ===')
for n in sorted(nodes, key=lambda x: (x.get('shape'), x['id'])):
    data = n.get('data', {})
    title = data.get('nodeTitleAlias') or data.get('title') or ''
    print(f"{n['id']:45s} | {n.get('shape'):16s} | {title}")
