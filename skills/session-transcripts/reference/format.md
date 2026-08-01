# Claude Code transcript JSONL format

Reference for working with raw records. For normal reading use `scripts/transcript.py`.
Everything here was derived by surveying ~400 transcripts written by Claude Code 2.1.x;
the format is undocumented and can change between releases, so parsers should skip
unknown record types rather than fail on them.

## Layout

```
~/.claude/projects/<encoded-cwd>/<session-id>.jsonl
~/.claude/projects/<encoded-cwd>/<session-id>/tool-results/   # binary artifacts (PDFs etc.)
```

`<encoded-cwd>` is the session's working directory with every non-alphanumeric character
replaced by `-`, so `/home/u/Projects/_WORK/foo` becomes `-home-u-Projects--WORK-foo`.
The encoding is lossy — `_` and `/` both become `-` — so recover the real path from the
`cwd` field of any record rather than by decoding the directory name.

`<session-id>` is a UUID and is also the `sessionId` on every record.

Related state lives outside `projects/`: `~/.claude/sessions/<pid>.json` describes live
sessions, `~/.claude/history.jsonl` holds the prompt history.

## Records

One JSON object per line, appended in chronological order. Common fields: `type`,
`uuid`, `parentUuid`, `sessionId`, `timestamp` (ISO 8601 UTC), `cwd`, `gitBranch`,
`version`, `isSidechain`, `isMeta`, `slug`.

| `type` | meaning |
|---|---|
| `user` | a user prompt **or** a tool result — distinguish by content, see below |
| `assistant` | one content block of an assistant response |
| `system` | harness events; `subtype` is `local_command`, `turn_duration`, `away_summary`, `bridge_status`, `api_error`, `compact_boundary`, `scheduled_task_fire`, `informational`, `model_refusal_fallback` |
| `attachment` | injected context; `attachment.type` is `hook_success`, `task_reminder`, `queued_command`, `skill_listing`, `plan_mode_exit`, `read_truncation_notice`, `nested_memory`, `deferred_tools_delta`, … |
| `ai-title` | generated session title (`aiTitle`) |
| `last-prompt` | most recent prompt plus `leafUuid` |
| `file-history-snapshot`, `file-history-delta` | file backup bookkeeping |
| `queue-operation` | messages queued while the turn was busy |
| `mode`, `permission-mode`, `bridge-session`, `agent-color`, `agent-name`, `pr-link` | session state |

Only `user`, `assistant`, and some `system` / `attachment` records carry conversation.

## Two traps

**An assistant response is split across several records.** Each carries one content block
(`thinking`, `text`, or `tool_use`) and they all share `message.id`. Rendering one record
per turn triples the apparent turn count and separates a tool call from the sentence
introducing it. Merge `assistant` records by `message.id`.

Merge by id rather than by adjacency, because the records are not always consecutive.
A `tool_result` lands between two `tool_use` blocks whenever tools run in sequence, and
harness bookkeeping interleaves too — a `read_truncation_notice` attachment, or an
`isMeta` user record such as an image-rescaling notice. None of those end the response.
An actual user prompt does, so treat only that as a boundary.

**A `user` record is often not from the user.** If `message.content` is a list containing
a `tool_result` block, the record is a tool result and belongs under the assistant turn
that called the tool — match `tool_result.tool_use_id` to the `tool_use.id`. Records with
`isMeta: true` are injected context, not user speech. Content may also be a slash-command
envelope (`<command-name>` / `<command-args>`) or a `<local-command-stdout>` envelope.

## Tool results

The `tool_result` block holds what the model saw. The sibling `toolUseResult` field on the
same record holds a richer structured version, and is the better source:

| shape | tool |
|---|---|
| `{stdout, stderr, interrupted, isImage, backgroundTaskId?}` | Bash |
| `{type: "text", file: {filePath, content, numLines, totalLines}}` | Read |
| `{filePath, oldString, newString, structuredPatch, originalFile}` | Edit |
| `{type: "create", filePath, content}` | Write |
| `{questions, answers, annotations}` | AskUserQuestion — `answers` is the user's choice |
| plain string | many others |

`tool_result.content` may itself be a list containing `text`, `image`, or `tool_reference`
blocks (the last is how `ToolSearch` reports loaded tools).

## Branching

`parentUuid` forms a tree, not a chain. **A parent with several children is normal**: the
next content block of an assistant response and the `tool_result` answering its tool call
both hang off the same parent. Treating that as a fork produces false positives on roughly
7% of transcripts.

A genuine rewind — the user edited an earlier message and re-ran — shows up as **two real
user prompts sharing one `parentUuid`**, where "real" excludes tool results and `isMeta`
records. The live branch is the one the file's last record descends from via `parentUuid`;
sibling subtrees at that fork are abandoned. Both branches stay in the file.

One record breaks the chain: a `compact_boundary` has `parentUuid: null` and puts the
pre-compaction record in **`logicalParentUuid`** instead. Walk that when `parentUuid` is
absent, or the live branch appears to start at the compaction and everything before it
looks abandoned.

## Compaction

`{"type": "system", "subtype": "compact_boundary"}` marks where the conversation was
summarized to free context. `compactMetadata` carries `trigger` (`auto` or `manual`),
`preTokens`, `postTokens`, and `durationMs`.

It matters for reading, not just parsing: records above the boundary were **not** in the
model's context afterwards. Drop the marker and a transcript reads as one continuous
conversation in which the model inexplicably forgets what it just did.

## Other notes

- `thinking` may be an empty string with a `signature` set — the reasoning was not stored.
- Text content can contain `<system-reminder>` blocks injected by the harness.
- `message.usage` on assistant records carries token counts and cache statistics.
- Subagent turns set `isSidechain: true`.
- `message.model` is `<synthetic>` for harness-generated assistant messages such as
  interruption notices.
