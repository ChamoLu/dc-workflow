# -*- coding: utf-8 -*-
import json, os, re
ROOT = os.path.dirname(os.path.abspath(__file__))
s = json.load(open(os.path.join(ROOT, 'node_summary.json'), encoding='utf-8'))
os.makedirs(os.path.join(ROOT, 'scripts'), exist_ok=True)
order = sorted([k for k in s if s[k]['shape'] == 'script'], key=lambda k: s[k]['position']['x'])
for i, k in enumerate(order, 1):
    d = s[k]['data']
    code = d.get('scriptCode', '')
    tail = k.split('_')[-1]
    name = d.get('displayName', '')
    safe = re.sub(r'[\\/:*?"<>|]', '_', name)
    fn = '%02d_%s_%s.py' % (i, safe, tail)
    open(os.path.join(ROOT, 'scripts', fn), 'w', encoding='utf-8').write(code)
    print('saved', fn, 'len', len(code))
