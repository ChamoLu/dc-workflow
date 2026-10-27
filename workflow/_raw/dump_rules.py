# -*- coding: utf-8 -*-
import json, glob, os
for f in sorted(glob.glob('nodes/custom_rule__*.json')):
    n = json.load(open(f, encoding='utf-8'))
    cfg = {}
    def walk(o):
        if isinstance(o, dict):
            for key in ('ruleType', 'ruleId', 'ruleIndexId', 'ruleName', 'ruleVersion',
                        'inputParams', 'outputParams', 'config', 'groupList', 'groupListData'):
                if key in o and key not in cfg:
                    cfg[key] = o[key]
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for x in o:
                walk(x)
    walk(n)
    print('=' * 78)
    print(os.path.basename(f))
    for k in ('ruleType', 'ruleId', 'ruleIndexId', 'ruleName', 'ruleVersion'):
        if k in cfg:
            print(' ', k, '=', cfg[k])
    for k in ('inputParams', 'outputParams'):
        if k in cfg:
            print(' ', k, ':')
            for p in cfg[k]:
                if isinstance(p, dict):
                    val = p.get('value')
                    print('    -', p.get('fieldName'), '|', p.get('dataType'), '|', val if not isinstance(val, list) else val)
