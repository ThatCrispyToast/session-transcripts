---
name: session-transcripts
description: Read, search and summarize transcripts of past Claude Code sessions, which are stored as hard-to-read JSONL under ~/.claude/projects. Use when the user refers to an earlier or previous session, asks what was done before / last time / yesterday, wants context recovered from a session that was cleared or compacted, asks when or why something was decided or changed, or asks to find, read, search or summarize an old conversation, chat log or transcript.
allowed-tools: [Bash, Read, Grep]
---

# Session transcripts

Claude Code appends every session to `~/.claude/projects/<encoded-cwd>/<session-id>.jsonl`.
Those files are raw event logs: one JSON record per content block, interleaved with
harness bookkeeping. Reading them directly wastes a huge amount of context and is easy
to misinterpret. `scripts/transcript.py` renders them instead.

Stdlib Python 3, no dependencies, no install:

```bash
python3 "$CLAUDE_SKILL_DIR/scripts/transcript.py" --help
```

If `$CLAUDE_SKILL_DIR` is not set, use the directory this SKILL.md lives in.

## Work from wide to narrow

Transcripts are big — a long session is ~2 MB of JSONL. Never dump a whole one into
context. Narrow down in three steps:

1. **`list`** — find the session
2. **`outline`** — one line per turn, ~10 KB even for the largest session; read this to
   locate the turns that matter
3. **`show --range N-M`** — render only those turns

A 1.9 MB transcript is ~11 KB as an outline and ~190 KB fully rendered. Outline first,
essentially always.

## Commands

```bash
T="$CLAUDE_SKILL_DIR/scripts/transcript.py"

python3 $T projects                      # every project with transcripts, newest first
python3 $T list                          # sessions for the current directory's project
python3 $T list --all --since 7d         # across all projects, last week
python3 $T list --grep auth              # match on title / first prompt

python3 $T outline <session>             # one line per turn — start here
python3 $T show <session> --range 40-60  # render just those turns
python3 $T show <session> --grep "docker" -C 3

python3 $T search "flaky test" --all     # regex across transcripts
```

`<session>` accepts a full session id, a unique id prefix (`b746e04f`), a path to a
`.jsonl`, or `latest`. Session ids are stable and appear in `list` output.

## Reading the output

`outline` gives one line per turn:

```
 [   4] 11:49:08 ASST   Found description.txt locally. Now let me check… {Read, Bash}
```

`[N]` is the turn number — pass it to `show --range`. Turn numbers are stable across
flags, so an outline and a later `show` always agree. `{...}` lists tools called in that
turn. A leading `!` marks a turn on an abandoned branch (see below).

`show` renders `▶` for a tool call and `⤷` for its result, with the result folded in
right under the call that produced it.

## Flags that matter

| Flag | Effect |
|---|---|
| `--range N-M` | turns N to M (`N`, `N-`, `-M` also work) |
| `--last N` | last N turns — good for "how did the session end" |
| `--grep RE` | only turns matching a regex, with `-C` turns of context |
| `--thinking` | include reasoning blocks (hidden by default) |
| `--full` | no truncation anywhere — can be enormous, use on a narrow `--range` |
| `--max-lines N` | lines per tool result, default 20 |
| `--main-branch` | drop rewound branches |
| `--meta` | include system-injected messages |

Defaults are tuned for reading in context: thinking hidden, tool output clipped to 20
lines. When a clipped result is the thing you actually need, re-run that one turn with
`--range N --full`.

## Rewound sessions

If the user edited a message and re-ran it, the transcript keeps both the abandoned and
the live branch. Abandoned turns are labelled `[abandoned branch]` and flagged `!` in the
outline, and the header says how many there are. They are shown by default because they
are real history — pass `--main-branch` to see only the conversation as it finally stood.
Do not report an abandoned turn as what happened without saying it was rewound.

## Notes

- Sessions are grouped by working directory, not by git repo. `list` with no arguments
  uses the current directory; use `--project <path>` or `--all` for anything else.
- The current session is being written live, so its transcript is incomplete.
- `search` scans the current project by default; it needs `--all` to go wider.
- Timestamps render in local time.

`reference/format.md` documents the JSONL schema itself — read it only if you need to
work with the raw records rather than the rendered output.
