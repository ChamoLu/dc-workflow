# -*- coding: utf-8 -*-
import json, os, re

ROOT = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(ROOT, 'nodes')
os.makedirs(OUT, exist_ok=True)

d = json.load(open(os.path.join(ROOT, 'aiWorkflow_detail.json'), encoding='utf-8'))['body']
ci = json.loads(d['canvasInfo'])
cells = ci['cells']

nodes, edges = [], []
for c in cells:
    if c.get('shape') == 'workflow-edge' or 'source' in c:
        edges.append(c)
    else:
        nodes.append(c)

print('nodes', len(nodes), 'edges', len(edges))

# 保存 templateConfig
with open(os.path.join(ROOT, 'templateConfig.json'), 'w', encoding='utf-8') as f:
    json.dump(ci['templateConfig'], f, ensure_ascii=False, indent=2)

# 保存每个节点
inv = []
for n in nodes:
    data = n.get('data', {})
    name = data.get('displayName') or data.get('nodeName') or n['id']
    safe = re.sub(r'[\\/:*?"<>|]', '_', name)
    fname = f"{n['shape']}__{n['id']}__{safe}.json"
    with open(os.path.join(OUT, fname), 'w', encoding='utf-8') as f:
        json.dump(n, f, ensure_ascii=False, indent=2)
    inv.append({'id': n['id'], 'shape': n['shape'], 'displayName': name,
                'x': n.get('position',{}).get('x'), 'y': n.get('position',{}).get('y'),
                'file': fname})

with open(os.path.join(ROOT, 'edges.json'), 'w', encoding='utf-8') as f:
    json.dump(edges, f, ensure_ascii=False, indent=2)
with open(os.path.join(ROOT, 'inventory.json'), 'w', encoding='utf-8') as f:
    json.dump(inv, f, ensure_ascii=False, indent=2)

for i in sorted(inv, key=lambda x:(x['y'] or 0, x['x'] or 0)):
    print(f"y={i['y']:>6} x={i['x']:>6}  {i['shape']:14s} {i['displayName']}")
