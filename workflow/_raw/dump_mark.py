# -*- coding: utf-8 -*-
import json, glob, os
for f in sorted(glob.glob('nodes/mark_task__*.json')):
    n = json.load(open(f, encoding='utf-8'))
    print('=' * 80)
    print(os.path.basename(f))
    def find(o):
        if isinstance(o, dict):
            if 'markTaskTypeCode' in o or 'templateId' in o or 'taskTemplateId' in o:
                return o
            for v in o.values():
                r = find(v)
                if r:
                    return r
        elif isinstance(o, list):
            for x in o:
                r = find(x)
                if r:
                    return r
    dd = find(n)
    if dd:
        for k, v in dd.items():
            if isinstance(v, (str, int, float, bool, type(None))):
                print(' ', k, '=', v)
            elif isinstance(v, list):
                print(' ', k, '= list[%d]' % len(v), json.dumps(v, ensure_ascii=False)[:300])
            elif isinstance(v, dict):
                print(' ', k, '= dict', json.dumps(v, ensure_ascii=False)[:300])
