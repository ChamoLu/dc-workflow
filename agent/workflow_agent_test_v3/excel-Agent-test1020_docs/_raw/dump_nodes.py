# -*- coding: utf-8 -*-
import json, os
ROOT = os.path.dirname(os.path.abspath(__file__))
s = json.load(open(os.path.join(ROOT, 'node_summary.json'), encoding='utf-8'))
order = sorted(s, key=lambda k: (s[k]['position']['x'], s[k]['position']['y']))
for k in order:
    n = s[k]
    d = n['data']
    print('=' * 90)
    print('%s | %s | %s' % (n['shape'], k, n['displayName']))
    if k in ('start', 'end'):
        print(' crawlList:', d.get('crawlList'))
        if k == 'end':
            print(' result:', d.get('result'))
        continue
    if n['shape'] == 'condition':
        for br in d.get('branches', []):
            cs = ' | '.join('%s %s %r' % (c.get('globalData', {}).get('indexCode') or c.get('leftKey'), c.get('method'), c.get('rightValue')) for c in br.get('condition', []))
            print('  [%s] logic=%s :: %s' % (br.get('branchName'), br.get('logic'), cs or '（兜底）'))
        continue
    if n['shape'] == 'custom_rule':
        for key in ('ruleType', 'ruleId', 'ruleIndexId', 'ruleName'):
            if key in d:
                print('  %s = %s' % (key, d.get(key)))
    # generic params
    for pk in ('inputParams', 'outputParams'):
        params = d.get(pk)
        if params:
            print('  %s:' % pk)
            for p in params:
                if isinstance(p, dict):
                    print('    - %s (%s) <- %s' % (p.get('fieldName'), p.get('dataType'), p.get('value')))
    for key in ('timeout', 'maxTimeout', 'failureRetryCount', 'failRetryCount'):
        if key in d:
            print('  %s = %s' % (key, d.get(key)))
    # code
    code = d.get('code') or d.get('script') or d.get('pythonCode')
    if code:
        print('  CODE LEN = %d' % len(code))
    # other possibly-relevant keys
    for key in ('language', 'codeType'):
        if key in d:
            print('  %s = %s' % (key, d.get(key)))
