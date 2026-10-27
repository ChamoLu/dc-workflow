# 动态查询规则 682：EDB数据特殊性查询（V4，共 5 个规则组）


## 规则组 0：价格不可为零入库
- ruleIndexId: 2100863914902134785
- 数据源: dim_4 (dataSourceId=2068983833818808322)
- 来源表: dim_EDB_usrHGHYZBWDB, dim_EDB_usrSJTSXB
- 动态输入: indicator_code（关系: SINGLE，匹配: EXACT）
- 输出: indicator_code, is_zero
- 返回策略: ALL | 适用: 画布=True 标注=False 批量=True
- 说明: 该分组仅可用于画布配置引用，不可用于标注配置引用
- SQL 模板:
```sql
select distinct a.indicator_code,1005 "is_zero"
from dim_EDB_usrHGHYZBWDB a
left join dim_EDB_usrSJTSXB b on a.module=b.SSMK and a.indicator_code=b.indicator_code and b.special_code=1005
where a.dimension_type_code=10 and a.dimension_code IN (10101,10102,10112,10113,10120,10150,10170) and a.module=1 and b.indicator_code is null
and a.indicator_code IN(${indicator_code})
```


## 规则组 1：截止日期不为月末必错
- ruleIndexId: 2100866155633549314
- 数据源: dim_4 (dataSourceId=2068983833818808322)
- 来源表: dim_EDB_industry_indicator, dim_EDB_usrSJTSXB
- 动态输入: indicator_code（关系: SINGLE，匹配: EXACT）
- 输出: indicator_code, problem_fin_time
- 返回策略: ALL | 适用: 画布=True 标注=False 批量=True
- 说明: 该分组仅可用于画布配置引用，不可用于标注配置引用
- SQL 模板:
```sql
select distinct a.indicator_code,1002 "problem_fin_time"
from dim_EDB_industry_indicator a
left join dim_EDB_usrSJTSXB b on b.SSMK=1 and a.indicator_code=b.indicator_code and b.special_code=1002
where b.indicator_code is null
and a.indicator_code IN(${indicator_code})
```


## 规则组 2：截止日期不为季末必错
- ruleIndexId: 2100893349355159554
- 数据源: dim_4 (dataSourceId=2068983833818808322)
- 来源表: dim_EDB_usrSJTSXB, dim_EDB_industry_indicator
- 动态输入: indicator_code（关系: SINGLE，匹配: EXACT）
- 输出: indicator_code, problem_fin_time
- 返回策略: ALL | 适用: 画布=True 标注=False 批量=True
- 说明: 该分组仅可用于画布配置引用，不可用于标注配置引用
- SQL 模板:
```sql
select distinct a.indicator_code,1003 "problem_fin_time"
from dim_EDB_industry_indicator a
left join dim_EDB_usrSJTSXB b on b.SSMK=1 and a.indicator_code=b.indicator_code and b.special_code=1003
where b.indicator_code is null
and a.indicator_code IN(${indicator_code})
```


## 规则组 3：截止日期不为年末必错
- ruleIndexId: 2100893349430657025
- 数据源: dim_4 (dataSourceId=2068983833818808322)
- 来源表: dim_EDB_industry_indicator, dim_EDB_usrSJTSXB
- 动态输入: indicator_code（关系: SINGLE，匹配: EXACT）
- 输出: indicator_code, problem_fin_time
- 返回策略: ALL | 适用: 画布=True 标注=False 批量=True
- 说明: 该分组仅可用于画布配置引用，不可用于标注配置引用
- SQL 模板:
```sql
select distinct a.indicator_code,1004 "problem_fin_time"
from dim_EDB_industry_indicator a
left join dim_EDB_usrSJTSXB b on b.SSMK=1 and a.indicator_code=b.indicator_code and b.special_code=1004
where b.indicator_code is null
and a.indicator_code IN(${indicator_code})
```


## 规则组 4：截止日期不为半年末必错
- ruleIndexId: 2101956092384337922
- 数据源: dim_4 (dataSourceId=2068983833818808322)
- 来源表: dim_EDB_industry_indicator, dim_EDB_usrSJTSXB
- 动态输入: indicator_code（关系: SINGLE，匹配: EXACT）
- 输出: indicator_code, problem_fin_time
- 返回策略: ALL | 适用: 画布=True 标注=False 批量=True
- 说明: 该分组仅可用于画布配置引用，不可用于标注配置引用
- SQL 模板:
```sql
select distinct a.indicator_code,1005 "problem_fin_time"
from dim_EDB_industry_indicator a
left join dim_EDB_usrSJTSXB b on b.SSMK=1 and a.indicator_code=b.indicator_code and b.special_code=1005
where b.indicator_code is null
and a.indicator_code IN(${indicator_code})
```
