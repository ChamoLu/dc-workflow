# -*- coding: utf-8 -*-
import json, os
ROOT = os.path.dirname(os.path.abspath(__file__))
d = json.load(open(os.path.join(ROOT, 'rule_206_dynamic_query.json'), encoding='utf-8'))
b = d['body']
L = []
L.append('# 动态查询规则 206：%s（%s，共 %d 个规则组）\n' % (b.get('ruleName'), b.get('versionStr'), len(b['groups'])))
for i, g in enumerate(b['groups']):
    L.append('## 规则组 %d：%s' % (i, g.get('groupName')))
    L.append('- ruleIndexId: `%s`' % g.get('ruleIndexId'))
    L.append('- 数据源: %s (dataSourceId=%s)' % (g.get('databaseName'), g.get('dataSourceId')))
    L.append('- 来源表: %s' % ', '.join(t.get('tableName') for t in g.get('sourceTables', [])))
    di = g.get('dynamicInputs', [])
    if di:
        L.append('- 动态输入: %s（关系: %s，匹配: %s）' % (
            ', '.join(x.get('name') for x in di),
            g.get('inputRelation'),
            ', '.join(x.get('matchType') for x in di)))
    L.append('- 输出: %s' % ', '.join(g.get('outputs', [])))
    L.append('- 返回策略: %s | 适用: 画布=%s 标注=%s 批量=%s' % (
        g.get('returnStrategy'), g.get('applicableCanvas'), g.get('applicableMark'), g.get('applicableBatch')))
    L.append('- SQL 模板:')
    L.append('```sql')
    L.append(g.get('sqlTemplate', ''))
    L.append('```\n')
open(os.path.join(ROOT, 'rule_206_SQL模板.md'), 'w', encoding='utf-8').write('\n'.join(L))
print('written rule_206_SQL模板.md')
