# -*- coding: utf-8 -*-
import json, os, glob
ROOT = os.path.dirname(os.path.abspath(__file__))
summ = json.load(open(os.path.join(ROOT,'node_summary.json'), encoding='utf-8'))
graph = json.load(open(os.path.join(ROOT,'graph.json'), encoding='utf-8'))
edges = graph['edges']

def tail(nid):
    if nid in ('start','end'): return nid
    return nid.split('_')[-1]

# 上下游
up = {}; down = {}
for e in edges:
    down.setdefault(e['source'], []).append((e['target'], e.get('label') or ''))
    up.setdefault(e['target'], []).append((e['source'], e.get('label') or ''))

def nm(nid):
    return summ[nid].get('displayName','')

# 按流程位置排序（用画布 x 坐标，再 y）
order = sorted(summ.keys(), key=lambda k:(summ[k]['position']['x'], summ[k]['position']['y']))

TYPE_CN={'start':'开始','end':'结束','agent':'智能体','file_extract':'大模型','script':'脚本','condition':'条件','custom_rule':'自定义规则','mark_task':'标注任务'}

lines=[]
lines.append('# 02 · 流程编排与节点清单\n')
lines.append('> 共 **47 个节点 / 59 条连线**。节点尾号 = 节点 ID 最后一段（如 `script_1784000760345_8300` 记作“8300”）。下表按画布从左到右（执行先后）排列。\n')
lines.append('## 1. 节点总表\n')
lines.append('| # | 类型 | 尾号 | 节点显示名 | 上游节点 | 下游节点（分支标签） |')
lines.append('|--:|--|--|--|--|--|')
for i,k in enumerate(order,1):
    s=summ[k]; t=s['shape']; n=s.get('displayName','')
    ups=[f"{nm(u)}({tail(u)})" for u,_ in up.get(k,[])]
    dns=[]
    for tgt,lab in down.get(k,[]):
        dns.append(f"{nm(tgt)}({tail(tgt)})" + (f"〔{lab}〕" if lab else ""))
    if not dns: dns=['—（无下游/流程终止）']
    lines.append(f"| {i} | {TYPE_CN.get(t,t)} | {tail(k)} | {n} | {'; '.join(ups) or '—'} | {'; '.join(dns)} |")
open(os.path.join(ROOT,'_inventory_table.md'),'w',encoding='utf-8').write('\n'.join(lines))
print('rows:', len(order))
# 统计每个条件节点的分支端口是否悬空
lines2=['\n## 2. 条件节点分支连接情况（悬空分支核查）\n','| 条件节点 | 尾号 | 分支 | 条件 | 是否连出 |','|--|--|--|--|--|']
for k in order:
    s=summ[k]
    if s['shape']!='condition': continue
    dd=s['data']
    # 分支名→端口
    dns=down.get(k,[])
    # 每个边标签即分支
    labels=[lab for _,lab in dns]
    targets={lab or '默认':nm(t) for t,lab in dns}
    for b in dd['branches']:
        bn=b['branchName']
        cond=' / '.join(f"{c['field']} {c['method']} {c['rightValue']}".strip() for c in b['conditions']) or '（兜底）'
        # 匹配
        if bn=='IF': keylab='IF'
        elif bn=='ELSE': keylab='ELSE'
        elif bn=='ELSE IF':
            keylab='ELSE IF'
        hit=[lab for lab in labels if lab.replace(' ','')== (keylab.replace(' ','')) ]
        # ELSE IF 有多个，需按顺序
        # 简单处理：统计端口数量
        pass
    # 用端口数量判断
    nports=len(dd['branches'])
    nedge=len(dns)
    lines2.append(f"| {s.get('displayName')} | {tail(k)} | 分支数 {nports} | 连出边 {nedge} | {'⚠ 有悬空分支' if nedge<nports else '全部连接'} |")
open(os.path.join(ROOT,'_inventory_table.md'),'a',encoding='utf-8').write('\n'.join(lines2))
print('done')
