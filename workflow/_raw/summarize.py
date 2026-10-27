# -*- coding: utf-8 -*-
import json, os, glob, re

ROOT = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(ROOT, 'node_summary')
os.makedirs(OUT, exist_ok=True)

def map_path(p):
    """把 #template_config__0__out_data.indicator_data 这样的路径转成可读形式"""
    if not p: return p
    s = p.replace('#template_config__0__', '').replace('template_config__0__', '')
    return s

def param(p):
    if not isinstance(p, dict): return p
    val = p.get('value')
    if isinstance(val, list):
        val = [map_path(x) for x in val]
    return {
        'fieldName': p.get('fieldName'),
        'desc': p.get('desc'),
        'dataType': p.get('dataType'),
        'selectedType': p.get('selectedType'),
        'value': val,
        'globalData': None,  # 不重复输出全局字段描述
    }

summary = {}
for f in sorted(glob.glob(os.path.join(ROOT,'nodes','*.json'))):
    n = json.load(open(f, encoding='utf-8'))
    d = n.get('data', {})
    sh = n.get('shape')
    item = {'id': n['id'], 'shape': sh, 'displayName': d.get('displayName'),
            'nodeName': d.get('nodeName'), 'position': n.get('position'), 'data': {}}
    dd = item['data']
    if sh == 'script':
        dd.update({'scriptType': d.get('scriptType'), 'timeout': d.get('timeout'),
                   'failureRetryCount': d.get('failureRetryCount'),
                   'inputParams': [param(p) for p in d.get('inputParams',[])],
                   'outputParams': [param(p) for p in d.get('outputParams',[])],
                   'outputConfig': d.get('outputConfig')})
    elif sh == 'custom_rule':
        dd.update({'serviceApiId': d.get('serviceApiId'), 'ruleId': d.get('ruleId'),
                   'ruleType': d.get('ruleType'), 'ruleIndexId': d.get('ruleIndexId'),
                   'sourceType': d.get('sourceType'), 'batchExecution': d.get('batchExecution'),
                   'inputParams': [param(p) for p in d.get('inputParams',[])],
                   'outputParams': [param(p) for p in d.get('outputParams',[])],
                   'outputConfig': d.get('outputConfig')})
    elif sh == 'condition':
        branches = []
        for b in d.get('branches', []):
            conds = []
            for c in b.get('condition', []):
                conds.append({'field': map_path(c.get('leftKey')), 'fieldDesc': (c.get('globalData') or {}).get('desc') if c.get('globalData') else None,
                              'dataType': c.get('dataType'), 'method': c.get('method'),
                              'rightValue': c.get('rightValue'),
                              'rightVariable': c.get('rightVariableValue'),
                              'isRightVariable': c.get('isRightVariable')})
            branches.append({'branchName': b.get('branchName'), 'logic': b.get('logic'), 'conditions': conds})
        dd['branches'] = branches
    elif sh == 'agent':
        dd.update({'model': d.get('model'), 'skillId': d.get('skillId'),
                   'maxSteps': d.get('maxSteps'), 'failRetryCount': d.get('failRetryCount'),
                   'maxTimeout': d.get('maxTimeout'),
                   'inputParams': [param(p) for p in d.get('inputParams',[])],
                   'outputParams': [param(p) for p in d.get('outputParams',[])],
                   'taskPrompt': d.get('taskPrompt'),
                   'outputInstruction': d.get('outputInstruction')})
    elif sh == 'file_extract':
        dd.update({'fileMd5': d.get('fileMd5'), 'batchRead': d.get('batchRead'),
                   'modelSource': d.get('modelSource'), 'aiModelId': d.get('aiModelId'),
                   'prompt': d.get('prompt'), 'batchExecution': d.get('batchExecution'),
                   'temperature': d.get('temperature'), 'topP': d.get('topP'),
                   'maxTimeout': d.get('maxTimeout'), 'failureRetryCount': d.get('failureRetryCount'),
                   'inputConfig': d.get('inputConfig'),
                   'outputFields': d.get('outputFields')})
    elif sh == 'mark_task':
        dd.update({'markTaskTypeCode': d.get('markTaskTypeCode'), 'typeName': d.get('typeName'),
                   'markTaskPolicyCode': d.get('markTaskPolicyCode'),
                   'filterTaskPolicy': d.get('filterTaskPolicy'),
                   'fieldSource': d.get('fieldSource'), 'taskTemplateId': d.get('taskTemplateId'),
                   'difficulty': d.get('difficulty'), 'timeLimit': d.get('timeLimit'),
                   'timeLimitUnit': d.get('timeLimitUnit'),
                   'markFields': d.get('markFields')})
    elif sh == 'start':
        dd.update({'crawlList': d.get('crawlList'), 'cleanFieldList': d.get('cleanFieldList'),
                   'advancedConfigEnabled': d.get('advancedConfigEnabled'),
                   'appDependency': d.get('appDependency')})
    elif sh == 'end':
        dd.update({'result': d.get('result')})
    summary[n['id']] = item

with open(os.path.join(ROOT,'node_summary.json'),'w',encoding='utf-8') as f:
    json.dump(summary, f, ensure_ascii=False, indent=2)
print('saved', len(summary), 'node summaries')
