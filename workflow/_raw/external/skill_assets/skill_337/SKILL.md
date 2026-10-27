---
name: EDB-markdown-prompt-router-DFJJ260928
description: Route a Markdown extraction request by pageTitle to one configured prompt file and return its filename and exact text. Use before the downstream large-model extraction node; do not execute or alter prompts.
---

# Markdown Prompt Router

## Purpose and boundary

This Skill is only for the routing Agent before the Markdown extraction large-model node. The Agent performs exactly these actions:

1. use incoming `pageTitle` to find one prompt-file mapping in `prompt-pageTitle-config.json`;
2. read the corresponding existing prompt file's complete original text;
3. return the filename in `PromptFile` and the prompt text in `PromptCode`.

The Agent does not execute the prompt, inspect or extract Markdown tables, use GZK, construct candidates, create `out_data`, or explain its selection. The downstream large-model node receives `PromptCode` and executes its contents.

## Required backend resources

Deploy these resources together in the Agent backend:

- this `SKILL.md`;
- `task_prompt.md`;
- `prompt-pageTitle-config.json`;
- every `.md` prompt file named by the configuration in this Skill's file list/directory.

## Routing and loading rules

### Required planning sequence

Plan and execute only this sequence. Do not insert download, external-resource lookup, Markdown-extraction, prompt-execution, or fallback-selection steps.

```text
read_skill_config
→ find_mapping
→ find_skill_prompt_file_by_exact_name
→ read_skill_prompt_file_content
→ output_result
```

1. `read_skill_config`: directly read `prompt-pageTitle-config.json` from this Skill.
2. `find_mapping`: compare the incoming `pageTitle` with every `mappings[].pageTitle`; retain exactly one exact match and obtain its `PromptFileName`.
3. `find_skill_prompt_file_by_exact_name`: compare that `PromptFileName` with the complete filenames in this Skill's file list; retain exactly one exact `.md` file.
4. `read_skill_prompt_file_content`: directly read the retained Skill file's complete raw text as `PromptCode` and retain its configured filename as `PromptFile`.
5. `output_result`: return the successful `{ "PromptFile": "...md", "PromptCode": "..." }` pair only after the raw prompt text has been verified as a non-empty string; otherwise follow the retry rules below, then return `{"PromptFile":"empty","PromptCode":""}` only after retries are exhausted.

### Atomic success and retry rules

A configured and uniquely located `PromptFileName` is **not** a successful route by itself. The Agent may output that real filename in `PromptFile` only together with the complete, non-empty raw text of that same Skill `.md` file in `PromptCode`.

Before `output_result`, verify all of the following:

1. `PromptFile` is the exact configured `.md` filename and exactly one same-named Skill file was found.
2. `PromptCode` is a string containing the complete raw text read from that same file.
3. `PromptCode.strip()` is non-empty.

If the real Skill prompt file was found but `PromptCode` is empty, missing, truncated, metadata, a file-list object, an error response, or otherwise fails verification, classify it as **prompt retrieval incomplete**, not as a no-route result. During each platform-provided retry opportunity, keep the same matched `PromptFile` and repeat `find_skill_prompt_file_by_exact_name` and `read_skill_prompt_file_content` until the complete prompt text is obtained. Do not change to another prompt, invent prompt content, use a partial excerpt, or output the real `PromptFile` with empty `PromptCode`.

Only after all retry opportunities fail to read and verify that same prompt file may `output_result` return `{"PromptFile":"empty","PromptCode":""}`.

1. `pageTitle` must be a non-empty string. Directly read this Skill's `prompt-pageTitle-config.json` and find exactly one item in `mappings` whose `pageTitle` exactly equals the incoming value.
2. Use only that item's `PromptFileName` value. In this Skill's own file list, find exactly one `.md` file whose complete filename exactly equals `PromptFileName`. Never use partial matching, filename-derived approximation, path-separator conversion, directory order, `title`, source type, GZK name, or another similar file as a fallback.
3. Directly read the matched `.md` file as raw text. Preserve every character, newline, indentation, HTML comment, heading, file-specific rule, and output instruction. Do not summarize, rewrite, add a Markdown code fence, prepend an explanation, or execute any instruction in the prompt file.
4. `PromptFileName` and `PromptFile` are filenames for locating a file in this Skill. Do not use `fileMd5`, `parseFileMd5`, any download API, or any external file/resource ID to locate or read the configuration or the prompt source. Those runtime data variables do not participate in routing.
5. On a successful route, output only the JSON shape in `agent-output.schema.json`. `PromptFile` is the configured `.md` filename exactly; `PromptCode` is the complete prompt-file **content** read from that same file. A non-empty `PromptFile` with empty or invalid `PromptCode` is forbidden.
6. After checking the complete configuration, the Skill file list, and all retry opportunities, if no unique usable exact mapping and readable verified `.md` source exist, return exactly `{"PromptFile":"empty","PromptCode":""}`. This is a normal no-route result for a platform task outside the configured file list; the following workflow filter discards it.
7. Never emit non-empty `PromptCode` when `PromptFile` is `"empty"`, and never guess, substitute, summarize, or partially return a prompt. Do not return `out_data`.

## Adding a prompt mapping

When a reviewed prompt file is deployed, add one exact mapping:

```json
{
  "pageTitle": "工作流实际传入的完整路径",
  "PromptFileName": "对应提示词文件的完整文件名.md"
}
```

The config `pageTitle` must equal the actual workflow value, and `PromptFileName` must equal the deployed file name. On a successful route, the Agent returns that filename in `PromptFile` and the complete file text in `PromptCode`.
