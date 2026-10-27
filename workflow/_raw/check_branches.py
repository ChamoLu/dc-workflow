# -*- coding: utf-8 -*-
import json, os

ROOT = os.path.dirname(os.path.abspath(__file__))
d = json.load(open(os.path.join(ROOT, 'aiWorkflow_detail.json'), encoding='utf-8'))['body']
ci = json.loads(d['canvasInfo'])
cells = ci['cells']
nodes, edges = [], []
for c in cells:
    if c.get('shape') == 'workflow-edge' or 'source' in c: edges.append(c)
    else: nodes.append(c)
N = {n['id']: n for n in nodes}
def nm(i): return (N[i].get('data',{}).get('displayName') or N[i].get('data',{}).get('nodeName')) if i in N else i

# 端口 -> 边
from collections import defaultdict
out_by_port = defaultdict(list)
for e in edges:
    out_by_port[(e['source']['cell'], e['source'].get('port'))].append(e)

for n in nodes:
    if n.get('shape') != 'condition': continue
    print('\n########', nm(n['id']), n['id'])
    for b in n['data'].get('branches', []):
        bc = b.get('branchCode')
        port = f'out_{bc}'
        es = out_by_port.get((n['id'], port), [])
        conds = b.get('condition', [])
        print(f"  分支 {b.get('branchName')!r} logic={b.get('logic')} 条件数={len(conds)} -> 边{len(es)}条")
        for c in conds:
            print('     条件:', json.dumps({k:c.get(k) for k in ('selectedType','method','value','key','fieldName')}, ensure_ascii=False)[:220])
        for e in es:
            print('     =>', nm(e['target']['cell']))
    # 检查所有未归属到分支的出边（ELSE 端口等）
    ports_in_branches = {f"out_{b.get('branchCode')}" for b in n['data'].get('branches',[])}
    for e in edges:
        if e['source']['cell']==n['id'] and e['source'].get('port') not in ports_in_branches:
            p = e['source'].get('port')
            # 查端口文本
            lab = ''
            for it in n.get('ports',{}).get('items',[]):
                if it.get('id')==p: lab = it.get('attrs',{}).get('text',{}).get('text','')
            print(f"  [其他端口 {p} {lab}] => {nm(e['target']['cell'])}")
