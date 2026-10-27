# -*- coding: utf-8 -*-
import json, os
ROOT = os.path.dirname(os.path.abspath(__file__))
tc = json.load(open(os.path.join(ROOT,'templateConfig.json'), encoding='utf-8'))
print('=== templateConfig 顶层键 ===', list(tc.keys()))
for k,v in tc.items():
    print('\n#', k, type(v).__name__, len(v) if isinstance(v,(list,dict,str)) else v)
    print(json.dumps(v, ensure_ascii=False, indent=1)[:3000])
