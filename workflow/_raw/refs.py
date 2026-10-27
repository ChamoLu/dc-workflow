# -*- coding: utf-8 -*-
import json, os, glob

ROOT = os.path.dirname(os.path.abspath(__file__))
rules = {}
skills = set()
tpls = set()
models = set()
for f in glob.glob(os.path.join(ROOT,'nodes','*.json')):
    n = json.load(open(f, encoding='utf-8'))
    d = n.get('data', {})
    sh = n.get('shape')
    nm = d.get('displayName')
    if sh == 'custom_rule':
        rules.setdefault(d.get('ruleId'), {'types': set(), 'nodes': [], 'serviceApiId': d.get('serviceApiId'),
                                           'ruleIndexId': d.get('ruleIndexId'), 'sourceType': d.get('sourceType')})
        rules[d.get('ruleId')]['types'].add(d.get('ruleType'))
        rules[d.get('ruleId')]['nodes'].append(nm)
    if sh == 'agent':
        skills.add(d.get('skillId'))
    if sh == 'mark_task':
        tpls.add(d.get('taskTemplateId'))
    if sh == 'file_extract':
        models.add(d.get('aiModelId'))

print('=== custom_rule 引用 ===')
for rid, info in rules.items():
    print('ruleId', rid, '| ruleType', info['types'], '| serviceApiId', info['serviceApiId'],
          '| ruleIndexId', repr(info['ruleIndexId']), '| sourceType', info['sourceType'])
    for nn in set(info['nodes']): print('     -', nn)
print('\nskillIds:', skills)
print('taskTemplateIds:', tpls)
print('aiModelIds:', models)
