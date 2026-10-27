# -*- coding: utf-8 -*-
import json, glob, os
for f in sorted(glob.glob('nodes/agent__*.json')) + sorted(glob.glob('nodes/file_extract__*.json')):
    n = json.load(open(f, encoding='utf-8'))
    print('=' * 90)
    print(os.path.basename(f))
    def find_config(o):
        cfg = {}
        def walk(o):
            if isinstance(o, dict):
                for k in ('taskPrompt', 'outputInstruction', 'model', 'modelId', 'aiModelId', 'modelName',
                          'stepNum', 'maxSteps', 'retryNum', 'failureRetryCount', 'timeout',
                          'skillId', 'skillPackageId', 'skillGroupId', 'skillVersion', 'temperature',
                          'topP', 'inputParams', 'outputParams', 'batchExecution', 'prompt',
                          'maxConcurrency', 'concurrentNum'):
                    if k in o and k not in cfg:
                        cfg[k] = o[k]
                for v in o.values():
                    walk(v)
            elif isinstance(o, list):
                for x in o:
                    walk(x)
        walk(o)
        return cfg
    cfg = find_config(n)
    for k, v in cfg.items():
        if k in ('inputParams', 'outputParams'):
            print(' ', k, ':')
            for p in v:
                if isinstance(p, dict):
                    print('    -', p.get('fieldName'), p.get('dataType'), '|', p.get('value'))
        elif k in ('taskPrompt', 'outputInstruction', 'prompt'):
            print(' ', k, '=>')
            print('   <<<')
            print(v if isinstance(v, str) else json.dumps(v, ensure_ascii=False, indent=1))
            print('   >>>')
        else:
            print(' ', k, '=', v)
