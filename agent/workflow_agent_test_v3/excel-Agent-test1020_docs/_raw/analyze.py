# -*- coding: utf-8 -*-
import json, os
ROOT = os.path.dirname(os.path.abspath(__file__))
d = json.load(open(os.path.join(ROOT, 'aiWorkflow_detail.json'), encoding='utf-8'))
b = d['body']
ci = b['canvasInfo']
if isinstance(ci, str):
    ci = json.loads(ci)
cells = ci['cells']

nodes = [c for c in cells if c['shape'] != 'workflow-edge']
edges = [c for c in cells if c['shape'] == 'workflow-edge']

def tail(nid):
    return nid if nid in ('start', 'end') else nid.split('_')[-1]

# map condition port -> branch label
port_label = {}
for n in nodes:
    if n['shape'] == 'condition':
        data = n.get('data', {})
        branches = data.get('branches', [])
        ports = n.get('ports', {}).get('items', [])
        # branchCode order matches ports? Build from branchCode list and port texts
        for i, br in enumerate(branches):
            bn = br.get('branchName')
            bc = br.get('branchCode')
            # port labels are on out ports in order; collect branchCode->label
        for p in ports:
            if p.get('group') == 'out':
                pid = p['id']
                txt = (p.get('attrs', {}).get('text', {}) or {}).get('text', '')
                port_label[(n['id'], pid)] = txt

# build edges
E = []
for e in edges:
    src = e['source']['cell']; sport = e['source'].get('port')
    tgt = e['target']['cell']
    lab = port_label.get((src, sport), '')
    E.append({'source': src, 'target': tgt, 'label': lab, 'port': sport})

# save node files
os.makedirs(os.path.join(ROOT, 'nodes'), exist_ok=True)
summ = {}
for n in nodes:
    nid = n['id']
    json.dump(n, open(os.path.join(ROOT, 'nodes', nid + '.json'), 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    data = n.get('data', {})
    summ[nid] = {
        'shape': n['shape'],
        'id': nid,
        'displayName': data.get('displayName') or data.get('nodeName') or '',
        'nodeName': data.get('nodeName'),
        'position': n.get('position'),
        'data': data,
    }
json.dump({'edges': E, 'templateConfig': ci.get('templateConfig'), 'workflow': {k: b.get(k) for k in ('id','appName','appDesc','tableName','runType','type','releaseTime','creatorName','creator','groupName')}},
          open(os.path.join(ROOT, 'graph.json'), 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
json.dump(summ, open(os.path.join(ROOT, 'node_summary.json'), 'w', encoding='utf-8'), ensure_ascii=False, indent=1)

# print concise overview
order = sorted(nodes, key=lambda n: (n['position']['x'], n['position']['y']))
print('=== NODES (by x) ===')
for n in order:
    dd = n.get('data', {})
    print('%-8s %-7s %s' % (tail(n['id']), n['shape'], dd.get('displayName') or dd.get('nodeName')))
print()
print('=== EDGES ===')
for e in sorted(E, key=lambda x: (summ[x['source']]['position']['x'], summ[x['source']]['position']['y'])):
    print('%s (%s) --[%s]--> %s (%s)' % (
        summ[e['source']]['displayName'], tail(e['source']), e['label'] or '-',
        summ[e['target']]['displayName'], tail(e['target'])))
