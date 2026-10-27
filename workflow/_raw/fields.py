# -*- coding: utf-8 -*-
import json, os, glob
ROOT = os.path.dirname(os.path.abspath(__file__))
tc = json.load(open(os.path.join(ROOT,'templateConfig.json'), encoding='utf-8'))['data']

def show(fields, depth=0):
    for f in fields:
        print('  '*depth + f"- {f.get('fieldName')} [{f.get('dataType')}] {f.get('desc') or ''}  (来源:{f.get('selectedType')}, 主键:{f.get('primaryKey')}, storage:{f.get('storageFlag')})")
        if f.get('children'):
            show(f['children'], depth+1)

print('=== 模板配置/全局数据字段（templateConfig.data.templateConfig）===')
show(tc['templateConfig'])

print('\n=== templateConfig.data 其他键 ===')
for k,v in tc.items():
    if k!='templateConfig':
        print(k, '=', json.dumps(v, ensure_ascii=False)[:300])

# start 节点
for f in glob.glob(os.path.join(ROOT,'nodes','start__*.json')):
    s = json.load(open(f, encoding='utf-8'))['data']
    print('\n=== start 节点 ===')
    print('crawlList:', json.dumps(s.get('crawlList'), ensure_ascii=False))
    print('cleanFieldList:', json.dumps(s.get('cleanFieldList'), ensure_ascii=False))
    print('advancedConfigEnabled:', s.get('advancedConfigEnabled'))
    print('appDependency:', json.dumps(s.get('appDependency'), ensure_ascii=False))
