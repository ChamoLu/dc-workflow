# -*- coding: utf-8 -*-
import json, os

ROOT = os.path.dirname(os.path.abspath(__file__))
d = json.load(open(os.path.join(ROOT, 'aiWorkflow_detail.json'), encoding='utf-8'))['body']
ci = json.loads(d['canvasInfo'])
cells = ci['cells']

nodes, edges = [], []
for c in cells:
    if c.get('shape') == 'workflow-edge' or 'source' in c:
        edges.append(c)
    else:
        nodes.append(c)

name = {n['id']: (n.get('data',{}).get('displayName') or n.get('data',{}).get('nodeName')) for n in nodes}
shape = {n['id']: n.get('shape') for n in nodes}

# 打印边的原始结构样本（看是否有标签）
print('=== 边样本（前3条，含全部键）===')
for e in edges[:3]:
    print(json.dumps(e, ensure_ascii=False)[:600])
    print('-'*60)

# 条件节点的 ports 结构，看分支端口命名
print('\n=== 条件节点 ports 样本 ===')
for n in nodes:
    if n.get('shape') == 'condition':
        ports = n.get('ports', {})
        items = ports.get('items', [])
        print(name[n['id']], n['id'])
        for p in items:
            print('   port:', p.get('id'), '| group:', p.get('group'), '| attrs:', json.dumps(p.get('attrs',{}), ensure_ascii=False)[:160])
        break
