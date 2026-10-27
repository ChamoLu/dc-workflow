# -*- coding: utf-8 -*-
import json, os

ROOT = os.path.dirname(os.path.abspath(__file__))
j = json.load(open(os.path.join(ROOT, 'aiWorkflow_detail.json'), encoding='utf-8'))
d = j['body']

print('=== 工作流元信息 ===')
for k in ['id','appName','appDesc','tableName','tags','creator','creatorName',
          'groupName','releaseTime','runType','type','parentId','dependId','dependName',
          'relatedAppId','relatedAppName']:
    print(f'{k}: {d.get(k)}')

ci = json.loads(d['canvasInfo'])
print('\n=== canvasInfo 类型 ===', type(ci).__name__)
if isinstance(ci, dict):
    for k, v in ci.items():
        print(' canvasInfo.'+k, '->', type(v).__name__, len(v) if isinstance(v,(list,dict,str)) else v)

print('\n=== nodeList 数量:', len(d['nodeList']))
for i, n in enumerate(d['nodeList']):
    if isinstance(n, dict):
        print(i, {k: (str(v)[:40]) for k,v in n.items() if k not in ('config','data','nodeConfig','properties')})
