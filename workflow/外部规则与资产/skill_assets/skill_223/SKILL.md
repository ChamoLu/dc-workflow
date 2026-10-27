---
name: html-agent-router-DFJJ260907
description: Look up pageTitle in a JSON configuration and return the exact source text and filename of the matching existing HTML parser. Use for the routing Agent before one shared Python execution node; do not use to execute, create, or alter parsers.
---

# HTML Agent Router

## Purpose and boundary

This Skill is for the Agent node before the shared HTML Python processing node. The Agent only routes and reads code:

1. use the incoming `pageTitle` to find a mapping in `python-page-title-config.json`;
2. read the mapped existing Python file's complete original text;
3. output the code text as `PythonCode` and the filename as `PythonFile`.

The Agent does not execute Python, download or parse HTML/Markdown, modify code, create `ExcelData`, read GZK, or create `OUTDATA`. The following shared Python node executes `PythonCode`. The selected existing parser continues to use its own `main(params)` contract and, for this HTML chain, reads runtime Markdown only through `markdownMD5`.

## Required backend resources

Deploy these resources together in the Agent backend:

- this `SKILL.md`;
- `task_prompt.md`;
- `python-page-title-config.json`;
- the reviewed `.py` files in this Skill's file list/directory.

## Runtime rules

### Required planning sequence

Plan and execute only this sequence. Do not insert download, external-resource lookup, parser-execution, or fallback-selection steps.

```text
read_skill_config
→ find_mapping
→ find_skill_python_file_by_exact_name
→ read_skill_python_file_content
→ output_result
```

1. `read_skill_config`: directly read `python-page-title-config.json` from this Skill.
2. `find_mapping`: compare the incoming `pageTitle` with every `mappings[].pageTitle`; retain exactly one exact match and obtain its `PythonFile`.
3. `find_skill_python_file_by_exact_name`: compare that `PythonFile` with the complete filenames in this Skill's file list; retain exactly one exact `.py` file.
4. `read_skill_python_file_content`: directly read the retained Skill file's complete raw text as `PythonCode`.
5. `output_result`: return the successful `{ "PythonFile": "...py", "PythonCode": "..." }` pair only after the raw source has been verified as a non-empty string; otherwise follow the retry rules below, then return `{"PythonFile":"empty","PythonCode":""}` only after retries are exhausted.

### Atomic success and retry rules

A configured and uniquely located `PythonFile` is **not** a successful route by itself. The Agent may output that real `PythonFile` only together with the complete, non-empty raw source of that same Skill `.py` file in `PythonCode`.

Before `output_result`, verify all of the following:

1. `PythonFile` is the exact configured `.py` filename and exactly one same-named Skill file was found.
2. `PythonCode` is a string containing the complete raw text read from that same file.
3. `PythonCode.strip()` is non-empty and the source contains the parser entry point `def main(`.

If the real Skill file was found but `PythonCode` is empty, missing, truncated, metadata, a file-list object, an error response, or otherwise fails verification, classify it as **source retrieval incomplete**, not as a no-route result. During each platform-provided retry opportunity, keep the same matched `PythonFile` and repeat `find_skill_python_file_by_exact_name` and `read_skill_python_file_content` until the complete source is obtained. Do not change to another file, invent code, use a partial excerpt, or output the real `PythonFile` with empty `PythonCode`.

Only after all retry opportunities fail to read and verify that same source file may `output_result` return `{"PythonFile":"empty","PythonCode":""}`.

1. `pageTitle` must be a non-empty string. Directly read this Skill's `python-page-title-config.json` and find exactly one item in `mappings` whose `pageTitle` exactly equals the incoming value.
2. Use only that item's `PythonFile` value. In this Skill's own file list, find exactly one `.py` file whose complete filename exactly equals `PythonFile`. Never use a partial match, path-separator conversion, source-name inference, directory order, `title`, `markdownMD5`, GZK, or a similar filename as a fallback.
3. Directly read the matched Skill `.py` file as raw source text. `PythonCode` must contain the entire original code, preserving all content, newlines, and indentation. Do not summarize, rewrite, add Markdown fences, prepend explanations, or call `main(params)`.
4. `PythonFile` is only a filename for locating a file in this Skill. Do not use `fileMd5`, `parseFileMd5`, any download API, or any external file/resource ID to locate or read the configuration or the Python source. Those runtime variables belong only to the downstream parser execution node.
5. On a successful route, output only `PythonCode` and `PythonFile`, both strings. `PythonFile` must be the configured filename including `.py`. A non-empty `PythonFile` with empty or invalid `PythonCode` is forbidden.
6. After checking the complete configuration, the Skill file list, and all retry opportunities, if no unique usable exact mapping and readable verified `.py` source exist, output exactly `{"PythonFile":"empty","PythonCode":""}`. This is a normal no-route result for a platform task outside the configured file list; the following workflow filter discards it.
7. Never infer, partially match, select a substitute parser, use an error response as source code, return a partial excerpt, or return non-empty `PythonCode` when `PythonFile` is `"empty"`. Do not output `ExcelData`.
8. `unresolved_page_title_conflicts` is documentation of known non-unique source paths only. It is not routable. Do not select any file from that list until the platform supplies a unique routing field or unique full `pageTitle` values are added to `mappings`.

## Adding or correcting a parser mapping

For each newly deployed parser that has a unique workflow `pageTitle`, add one object to `mappings`:

```json
{
  "pageTitle": "工作流实际传入的完整路径",
  "PythonFile": "后台 Python 文件的完整文件名.py"
}
```

The configuration value must exactly match the actual workflow input, not a filename-derived approximation. If two scripts receive the same `pageTitle`, do not add duplicate active mappings: first obtain an additional reliable routing field or distinct actual page titles.
