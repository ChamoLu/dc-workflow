# -*- coding: utf-8 -*-
import aha_browser_use as bu, json, os
# 注意：本脚本由执行器运行，路径在 sandbox python 侧
OUT = "D:/EDBPythonCode/dc_workflow/_raw/aux"
os.makedirs(OUT, exist_ok=True)

def post(path, payload):
    body = json.dumps(payload)
    js = (
        "(function(){try{var xhr=new XMLHttpRequest();"
        "xhr.open('POST', "+json.dumps(path)+", false);"
        "xhr.setRequestHeader('Content-Type','application/json');xhr.withCredentials=true;"
        "xhr.send("+json.dumps(body)+");"
        "return xhr.responseText;"
        "}catch(e){return JSON.stringify({error:String(e)});}})()"
    )
    return bu.js(js)

base = "/dc-bot/dc/"
jobs = [
    ("rule_404_feel_table.json", "ruleEngine/configDetail", {"ruleId":404}),
    ("rule_206_dynamic_query.json", "dynamicQuery/rule/detail", {"ruleId":206}),
    ("rule_682_dynamic_query.json", "dynamicQuery/rule/detail", {"ruleId":682}),
    ("rule_206_config.json", "ruleEngine/configDetail", {"ruleId":206}),
    ("rule_682_config.json", "ruleEngine/configDetail", {"ruleId":682}),
    ("model_DeepSeek.json", "aiModelCenter/detail", {"modelId":"2101861565690597377"}),
    ("task_template_212.json", "workflow/taskConfig/template/getById", {"id":"2103026958354526212"}),
    ("task_template_211.json", "workflow/taskConfig/template/getById", {"id":"2103026958354526211"}),
    ("task_template_213.json", "workflow/taskConfig/template/getById", {"id":"2103026958354526213"}),
]
for fn, ep, pl in jobs:
    raw = post(base+ep, pl)
    with open(os.path.join(OUT, fn), "w", encoding="utf-8") as f:
        f.write(raw)
    try:
        j = json.loads(raw)
        print(fn, "status", j.get("status"), "len", len(raw))
    except Exception:
        print(fn, "raw len", len(raw))
