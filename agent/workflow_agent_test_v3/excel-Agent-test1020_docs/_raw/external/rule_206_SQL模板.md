# 动态查询规则 206：EDB指标代码查询（V22，共 5 个规则组）

## 规则组 0：新宏观代码\比例\单位\名称查询
- ruleIndexId: `2071900657280163842`
- 数据源: dim_4 (dataSourceId=2068983833818808322)
- 来源表: dim_EDB_newmacro_indicator, dim_EDB_newmacro_constant, dim_EDB_source_collection_indicator_table, dim_EDB_unit_convert_table
- 动态输入: pageTitle（关系: SINGLE，匹配: EXACT）
- 输出: indicator_code, indicator_name, constant_name, unit_ratio, indicator_release_frequency
- 返回策略: ALL | 适用: 画布=True 标注=False 批量=True
- SQL 模板:
```sql
select a.indicator_code,a.indicator_name,c.constant_name,b.unit_ratio,d.constant_name indicator_release_frequency
from dim_EDB_source_collection_indicator_table a
inner join dim_EDB_newmacro_indicator b on b.indicator_code=a.indicator_code
inner join dim_EDB_newmacro_constant c on c.constant_type=17 and c.constant_code=b.indicator_unit_code
inner join dim_EDB_newmacro_constant d on d.constant_type=11 and d.constant_code=b.indicator_release_frequency
where a.pageTitle=${pageTitle}
limit 5000
```

## 规则组 1：查询单位换算率
- ruleIndexId: `2074016997430190081`
- 数据源: dim_4 (dataSourceId=2068983833818808322)
- 来源表: dim_EDB_unit_convert_table
- 动态输入: raw_unit, target_code（关系: AND，匹配: EXACT, EXACT）
- 输出: raw_unit, target_code, convert_ratio
- 返回策略: ALL | 适用: 画布=True 标注=False 批量=True
- SQL 模板:
```sql
select a.raw_unit,a.target_code,a.convert_ratio 
from dim_EDB_unit_convert_table a
where raw_unit IN(${raw_unit}) and target_code IN(${target_code})
```

## 规则组 2：国际宏观代码\比例\单位\名称查询
- ruleIndexId: `2082796849719992322`
- 数据源: dim_4 (dataSourceId=2068983833818808322)
- 来源表: dim_EDB_intlmacro_constant, dim_EDB_intlmacro_indicator, dim_EDB_source_collection_indicator_table, dim_violation_sys_dict
- 动态输入: pageTitle（关系: SINGLE，匹配: EXACT）
- 输出: indicator_code, indicator_name, constant_name, unit_ratio, indicator_release_frequency
- 返回策略: ALL | 适用: 画布=True 标注=False 批量=True
- SQL 模板:
```sql
select a.indicator_code,a.indicator_name,c.constant_name,b.unit_ratio,d.constant_name indicator_release_frequency
from dim_EDB_source_collection_indicator_table a
inner join dim_EDB_intlmacro_indicator b on b.indicator_code=a.indicator_code
inner join dim_EDB_intlmacro_constant c on c.constant_type_code=17 and c.constant_code=b.indicator_unit_code
inner join dim_EDB_intlmacro_constant d on d.constant_type_code=11 and d.constant_code=b.indicator_release_frequency
where a.pageTitle=${pageTitle}
limit 5000
```

## 规则组 3：地方经济运行代码\比例\单位\名称查询
- ruleIndexId: `2082796849787101185`
- 数据源: dim_4 (dataSourceId=2068983833818808322)
- 来源表: dim_EDB_localecon_constant, dim_EDB_localecon_indicator, dim_EDB_source_collection_indicator_mapping, dim_EDB_unit_convert, dim_EDB_source_collection_indicator_table, dim_EDB_unit_convert_table
- 动态输入: pageTitle（关系: SINGLE，匹配: EXACT）
- 输出: indicator_code, indicator_name, constant_name, indicator_release_frequency
- 返回策略: ALL | 适用: 画布=True 标注=False 批量=True
- SQL 模板:
```sql
select distinct a.indicator_code,a.indicator_name,c.constant_name,d.constant_name indicator_release_frequency
from dim_EDB_source_collection_indicator_table a
inner join dim_EDB_localecon_indicator b on b.indicator_code=a.indicator_code
inner join dim_EDB_localecon_constant c on c.type_code=2000 and c.constant_code=b.indicator_unit
inner join dim_EDB_localecon_constant d on d.type_code=1000 and d.constant_code=b.indicator_release_frequency
where a.pageTitle=${pageTitle}
limit 5000
```

## 规则组 4：行业代码\比例\单位\名称查询
- ruleIndexId: `2082796849833238529`
- 数据源: dim_4 (dataSourceId=2068983833818808322)
- 来源表: dim_EDB_industry_constant, dim_EDB_industry_indicator, dim_EDB_source_collection_indicator_table, dim_EDB_unit_convert_table
- 动态输入: pageTitle（关系: SINGLE，匹配: EXACT）
- 输出: indicator_code, indicator_name, constant_name, unit_ratio, indicator_release_frequency
- 返回策略: ALL | 适用: 画布=True 标注=False 批量=True
- SQL 模板:
```sql
select a.indicator_code,a.indicator_name,c.constant_name,b.unit_ratio,d.constant_name indicator_release_frequency
from dim_EDB_source_collection_indicator_table a
inner join dim_EDB_industry_indicator b on b.indicator_code=a.indicator_code
inner join dim_EDB_industry_constant c on c.constant_type_code=17 and c.constant_code=b.indicator_unit_code
inner join dim_EDB_industry_constant d on d.constant_type_code=12 and d.constant_code=b.release_frequency
where a.pageTitle=${pageTitle}
limit 5000
```
