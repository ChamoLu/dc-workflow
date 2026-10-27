# -*- coding: utf-8 -*-
import json, os
ROOT = os.path.dirname(os.path.abspath(__file__))
for rid in ['206','682']:
    j = json.load(open(os.path.join(ROOT, f'rule_{rid}_dynamic_query.json'), encoding='utf-8'))['body']
    out = []
    out.append(f"# 动态查询规则 {rid}：{j['ruleName']}（{j['versionStr']}，共 {len(j['groups'])} 个规则组）\n")
    for gi, g in enumerate(j['groups']):
        out.append(f"\n## 规则组 {gi}：{g['groupName']}")
        out.append(f"- ruleIndexId: {g['ruleIndexId']}")
        out.append(f"- 数据源: {g['databaseName']} (dataSourceId={g['dataSourceId']})")
        out.append(f"- 来源表: {', '.join(t['tableName'] for t in g['sourceTables'])}")
        din = g.get('dynamicInputs', [])
        out.append(f"- 动态输入: {', '.join(x['name'] for x in din)}（关系: {g.get('inputRelation')}，匹配: {', '.join(str(x.get('matchType')) for x in din)}）")
        out.append(f"- 输出: {', '.join(g.get('outputs', []))}")
        out.append(f"- 返回策略: {g.get('returnStrategy')} | 适用: 画布={g.get('applicableCanvas')} 标注={g.get('applicableMark')} 批量={g.get('applicableBatch')}")
        out.append(f"- 说明: {g.get('applicableReason')}")
        out.append("- SQL 模板:\n```sql")
        out.append(g.get('sqlTemplate',''))
        out.append("```\n")
    with open(os.path.join(ROOT, f'rule_{rid}_SQL模板.md'),'w',encoding='utf-8') as f:
        f.write('\n'.join(out))
    print(f'rule {rid}: {len(j["groups"])} groups saved')
