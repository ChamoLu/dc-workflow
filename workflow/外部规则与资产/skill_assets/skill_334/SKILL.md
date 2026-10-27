---
name: EDB-excel-agent-router-DFJJ260928
description: Look up pageTitle in a JSON configuration and return the exact source text and filename of the matching existing Excel Python parser. Use for the routing Agent before one shared Python execution node; do not use to execute, create, or alter parsers.
---

# Excel Agent Router

## Purpose and boundary

This Skill is for the Agent node placed before the shared Python processing node. The Agent only routes and reads code:

1. use incoming `pageTitle` to find a mapping in `python-page-title-config.json`;
2. read the mapped existing Python file's complete original text;
3. output the code text as `PythonCode` and the filename as `PythonFile`.

The Agent does not execute Python, download or parse Excel/CSV, change Python code, create `ExcelData`, read GZK, or create `OUTDATA`. The following shared Python node executes `PythonCode` and returns the target parser's existing `main(params)` result unchanged.

Use the same-directory `python-page-title-config.json` as the routing configuration. The Agent always returns the string variables `PythonCode` and `PythonFile`: either a successfully routed pair, or the explicit no-route sentinel pair.

## Required backend resources

Deploy these resources together in the Agent backend:

- this `SKILL.md`;
- `python-page-title-config.json`;
- the existing reviewed Python parser files in this Skill's file list/directory.

## Runtime rules

### Required planning sequence

Plan and execute only the following sequence. Do not insert any download, external-resource lookup, parser-execution, or fallback-selection step.

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

A configured and uniquely located `PythonFile` is **not** a successful route by itself. Success is atomic: the Agent may output that real `PythonFile` only together with the complete, non-empty raw source of that same Skill `.py` file in `PythonCode`.

Before `output_result`, verify all of the following:

1. `PythonFile` is the exact configured `.py` filename and exactly one same-named Skill file was found.
2. `PythonCode` is a string containing the complete raw text read from that same file.
3. `PythonCode.strip()` is non-empty and the source contains the parser entry point `def main(`.

If steps 1 and 2 found the real Skill file but `PythonCode` is empty, missing, truncated, metadata, a file-list object, an error response, or otherwise fails the verification, classify it as **source retrieval incomplete**, not as a no-route result. During each of the platform-provided retry opportunities, keep the same matched `PythonFile` and focus on repeating `find_skill_python_file_by_exact_name` and `read_skill_python_file_content` until the complete source is obtained. Do not change to another file, invent code, use a partial excerpt, or output the real `PythonFile` with empty `PythonCode`.

Only after all retry opportunities fail to read and verify that same source file may `output_result` return `{"PythonFile":"empty","PythonCode":""}`.

1. `pageTitle` must be a non-empty string. Directly read this Skill's `python-page-title-config.json` and find exactly one `mappings[]` object whose `pageTitle` equals the incoming value exactly.
2. Use only that object's `PythonFile` value. In this Skill's own file list, find exactly one `.py` file whose complete filename exactly equals `PythonFile`. Never infer a filename from separators, use a partial match, choose by directory order, use `title`, use GZK, or choose a similar code file.
3. Directly read the matched Skill `.py` file as raw source text. `PythonCode` must contain its complete source code, preserving code content, newlines, and indentation. Do not summarize it, rewrite it, add a Markdown code fence, prepend an explanation, or call `main(params)`.
4. `PythonFile` is only a filename for locating a file in this Skill. Do not use `fileMd5`, `parseFileMd5`, any download API, or any external file/resource ID to locate or read the configuration or the Python source. Those runtime business-file variables belong only to the downstream parser execution node.
5. On a successful route, return only the two fields in the output schema. `PythonFile` is the configured filename exactly, including its `.py` suffix. It is an input variable for the shared Python node and is not a final business-data field. A non-empty `PythonFile` with empty or invalid `PythonCode` is forbidden.
6. After checking the complete configuration, the Skill file list, and all retry opportunities, if no unique usable exact mapping and readable verified `.py` source exist, return exactly `{"PythonFile":"empty","PythonCode":""}`. This is a normal no-route result for a platform task outside the configured file list; the following workflow filter discards it.
7. Never infer, partially match, select a substitute parser, use an error response as source code, return a partial excerpt, or return non-empty `PythonCode` when `PythonFile` is `"empty"`. Do not output `ExcelData`.

## Adding a parser

When a new reviewed per-file parser is deployed, add exactly one mapping object to `python-page-title-config.json`:

```json
{
  "pageTitle": "工作流实际传入的完整路径",
  "PythonFile": "后台 Python 文件的完整文件名.py"
}
```

Do not add a workflow Python node for each parser. Confirm that the configuration `pageTitle` exactly equals the actual workflow value and that `PythonFile` exactly equals the Skill file name. After locating the file in this Skill by exact filename, directly read its raw text and output it as `PythonCode`.
