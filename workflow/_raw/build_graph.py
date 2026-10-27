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

N = {n['id']: n for n in nodes}
def nm(i): return (N[i].get('data',{}).get('displayName') or N[i].get('data',{}).get('nodeName')) if i in N else i

# 条件节点：branchCode -> 分支序号/标签
branch_map = {}   # (nodeId, branchCode) -> label
for n in nodes:
    if n.get('shape') == 'condition':
        for bi, b in enumerate(n['data'].get('branches', [])):
            branch_map[(n['id'], b.get('branchCode'))] = b.get('branchName') or ('IF' if bi==0 else f'ELIF/{bi}')
        # 通常最后一个 ELSE 端口
        port_items = n.get('ports',{}).get('items', [])
        for p in port_items:
            pid = p.get('id','')
            if pid.startswith('out_'):
                code = pid[4:]
                label = p.get('attrs',{}).get('text',{}).get('text','')
                if (n['id'], code) in branch_map:
                    branch_map[(n['id'], code)] = label
                else:
                    branch_map[(n['id'], code)] = label or 'ELSE'

adj = {n['id']: [] for n in nodes}   # source -> (label, target, edgeId)
indeg = {n['id']: 0 for n in nodes}
for e in edges:
    s = e['source']['cell']; t = e['target']['cell']
    sport = e['source'].get('port','')
    label = ''
    if s in N and N[s].get('shape')=='condition' and sport.startswith('out_'):
        label = branch_map.get((s, sport[4:]), sport)
    adj.setdefault(s, []).append({'label': label, 'target': t, 'edge': e['id']})
    indeg[t] = indeg.get(t,0)+1

print('=== 出向连线（按节点）===')
for n in nodes:
    outs = adj.get(n['id'], [])
    if outs:
        print(f"\n[{n.get('shape')}] {nm(n['id'])}  ({n['id']})")
        for o in outs:
            lab = f" --{o['label']}-->" if o['label'] else " -->"
            print(f"   {lab} [{N[o['target']].get('shape') if o['target'] in N else '?'}] {nm(o['target'])}")

print('\n\n=== 入向（无前置的节点 = 根）===')
for n in nodes:
    if indeg.get(n['id'],0)==0:
        print('ROOT:', n.get('shape'), nm(n['id']), n['id'])

# 保存图结构
graph = {'nodes': {n['id']: {'shape': n.get('shape'), 'name': nm(n['id']),
                             'position': n.get('position')} for n in nodes},
         'edges': [{'id': e['id'], 'source': e['source']['cell'], 'sourcePort': e['source'].get('port'),
                    'target': e['target']['cell'], 'targetPort': e['target'].get('port'),
                    'label': branch_map.get((e['source']['cell'], e['source'].get('port','')[4:]), '')
                             if e['source']['cell'] in N and N[e['source']['cell']].get('shape')=='condition' else ''}
                   for e in edges]}
with open(os.path.join(ROOT,'graph.json'),'w',encoding='utf-8') as f:
    json.dump(graph, f, ensure_ascii=False, indent=2)
print('\nsaved graph.json')
