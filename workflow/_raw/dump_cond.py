# -*- coding: utf-8 -*-
import json, glob, os
for f in sorted(glob.glob('nodes/condition__*.json')):
    n = json.load(open(f, encoding='utf-8'))
    res = {}
    def walk(o):
        if isinstance(o, dict):
            if 'branches' in o and isinstance(o['branches'], list) and o['branches'] and isinstance(o['branches'][0], dict) and 'condition' in o['branches'][0]:
                res['b'] = o['branches']
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for x in o:
                walk(x)
    walk(n)
    if 'b' in res:
        print(os.path.basename(f))
        for b in res['b']:
            cs = ' | '.join('%s %s %r' % (
                c.get('globalData', {}).get('indexCode') or c.get('leftKey'),
                c.get('method'), c.get('rightValue')) for c in b.get('condition', []))
            print('   [%s] logic=%s :: %s' % (b.get('branchName'), b.get('logic'), cs or '(无)'))
        print()
