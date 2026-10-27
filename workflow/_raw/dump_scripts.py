# -*- coding: utf-8 -*-
import json, os, glob, re

ROOT = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(ROOT, 'scripts')
os.makedirs(OUT, exist_ok=True)

scripts = []
for f in glob.glob(os.path.join(ROOT,'nodes','script__*.json')):
    n = json.load(open(f, encoding='utf-8'))
    d = n['data']
    scripts.append((n['id'], d))

# 用节点 id 尾号排序，避免重名覆盖
def tail(nid):
    m = re.search(r'_(\d+)$', nid)
    return int(m.group(1)) if m else 0
scripts.sort(key=lambda x: tail(x[0]))

index = []
for i, (nid, d) in enumerate(scripts, 1):
    name = d.get('displayName','')
    code = d.get('scriptCode','')
    safe = re.sub(r'[\\/:*?"<>|\s]+','_', name)
    fname = f"{i:02d}_{safe}_{tail(nid)}.py"
    path = os.path.join(OUT, fname)
    with open(path,'w',encoding='utf-8') as fh:
        fh.write(code)
    index.append({'file': fname, 'nodeId': nid, 'displayName': name,
                  'scriptType': d.get('scriptType'), 'timeout': d.get('timeout'),
                  'failureRetryCount': d.get('failureRetryCount'),
                  'lines': code.count('\n')+1, 'chars': len(code)})
    print(f"{fname:55s} {code.count(chr(10))+1:>4} 行  {len(code):>6} 字符")

with open(os.path.join(OUT,'_index.json'),'w',encoding='utf-8') as fh:
    json.dump(index, fh, ensure_ascii=False, indent=2)
print('\n共', len(scripts), '个脚本')
