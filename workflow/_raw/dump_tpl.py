# -*- coding: utf-8 -*-
import json, glob, os
# 1) json markFields full
import re
n = json.load(open([f for f in glob.glob('nodes/mark_task__*.json') if '3138' in f][0], encoding='utf-8'))
def find(o, key):
    if isinstance(o, dict):
        if key in o:
            return o[key]
        for v in o.values():
            r = find(v, key)
            if r is not None:
                return r
    elif isinstance(o, list):
        for x in o:
            r = find(x, key)
            if r is not None:
                return r
    return None
print('json 3138 markFields:')
for m in find(n, 'markFields'):
    print('  -', m.get('indexCode'), m.get('dataType'), m.get('indexName'), 'required=', m.get('isRequired'))

# 2) templates form widgets
for tid in ('2103026958354526211', '2103026958354526212', '2103026958354526213'):
    files = glob.glob('external/*%s*' % tid)
    if not files:
        print('NO template file for', tid); continue
    d = json.load(open(files[0], encoding='utf-8'))
    print('=' * 70)
    print('template', tid, files[0])
    # find canvasInfo -> widgetList
    def walk(o):
        if isinstance(o, dict):
            if 'widgetList' in o and isinstance(o['widgetList'], list):
                return o['widgetList']
            for v in o.values():
                r = walk(v)
                if r:
                    return r
        elif isinstance(o, list):
            for x in o:
                r = walk(x)
                if r:
                    return r
        return None
    wl = walk(d)
    # template name
    def find2(o, key):
        if isinstance(o, dict):
            if key in o and isinstance(o[key], str):
                return o[key]
            for v in o.values():
                r = find2(v, key)
                if r:
                    return r
        elif isinstance(o, list):
            for x in o:
                r = find2(x, key)
                if r:
                    return r
        return None
    for nmkey in ('templateName', 'name', 'title'):
        nm = find2(d, nmkey)
        if nm:
            print(' name:', nm); break
    if wl:
        for w in wl:
            attr = w.get('attribute', {}) if isinstance(w, dict) else {}
            print('  widget:', w.get('type') if isinstance(w, dict) else '',
                  '| fieldCode=', attr.get('fieldCode'),
                  '| label=', attr.get('label'),
                  '| status=', attr.get('status'),
                  '| required=', attr.get('isRequired'))
