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

Stdlib Python 3, no dependencies, no install. When this skill loads, Claude Code states
its base directory ("Base directory for this skill: …"). Use that absolute path and set
it once:

```bash
T="<skill base directory>/scripts/transcript.py"
python3 "$T" --help
```

**Do not use `$CLAUDE_SKILL_DIR`** — it is usually not set, and the empty expansion fails
as `python3: can't open file '/scripts/transcript.py'`, which is easy to misread as the
skill being broken.

**On Windows use `py -3 "$T"`**, not `python3`. The official installer ships `python.exe`
and the `py` launcher but no `python3`; that name normally resolves to a Microsoft Store
stub that opens the Store instead of running anything.

## Work from wide to narrow

Transcripts are big — a long session is ~2 MB of JSONL. Never dump a whole one into
context. Narrow down in three steps:

1. **`list`** — find the session. `[rewound]` and `[compacted]` flag sessions whose
   history needs care; see below.
2. **`outline`** — one line per turn, ~11 KB even for a 6 MB session; read this to
   locate the turns that matter. It takes `--range` too, so page it with that rather
   than piping to `head`.
3. **`show --range N-M`** — render only those turns

Outline first, essentially always. Keep `--full` to a handful of turns: `--range 162-190
--full` returns ~50 KB, which overflows the tool-output limit and lands in a file you
then have to read back. Narrow the range instead, or raise `--max-lines` a little.

## Commands

```bash
T="<skill base directory>/scripts/transcript.py"

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
`.jsonl`, or `latest`. Session ids are stable and appear in `list` output. `latest`
means the newest session *for the current directory's project*, not on the whole
machine, and never the session you are running in — say `--project` for another
project, or name an id.

`search` and `--grep` cover the whole turn, tool calls and tool output included, so
a build error or a file path is as findable as something the user typed. The pattern
is a regex: escape it if it contains brackets, `Traceback \(most recent call last\)`
rather than the bare parentheses, which otherwise read as a group and match nothing.

## Reading the output

`outline` gives one line per turn:

```
 [   4] 11:49:08 ASST   Found description.txt locally. Now let me check… {Read: description.txt, Bash}
```

`[N]` is the turn number — pass it to `show --range`. Turn numbers are stable across
flags, so an outline and a later `show` always agree. `{...}` lists tools called in that
turn, each with what it acted on where there is something short to name — the file, the
pattern, the agent's task. Two targets are listed and the rest counted (`+3`); Bash is
left bare because it is nearly half of all calls, so read its command in `show`. A
leading `!` marks a turn on an abandoned branch (see below).

Because the target is on the outline line, "which files did this session change" is an
`outline` question, not a `show` one.

`show` renders `->` for a tool call and `<-` for its result, with the result folded in
right under the call that produced it. A content line that would otherwise read as
framing is prefixed with `> `, so the only unquoted turn headers are the real ones.

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

A turn that renders to nothing at these defaults — a system-injected one, usually —
still holds its number, so `show` says on stderr how many it left out and which flag
brings them back. `search` does report hits inside those turns, so this is the note
that explains an otherwise blank `show --range`.

## Rewound sessions

If the user edited a message and re-ran it, the transcript keeps both the abandoned and
the live branch. Abandoned turns are labelled `[abandoned branch]` and flagged `!` in the
outline, and the header says how many there are. They are shown by default because they
are real history — pass `--main-branch` to see only the conversation as it finally stood.
Do not report an abandoned turn as what happened without saying it was rewound.

`list` and `search` both mark these `[rewound]`, which matters when several sessions cover
one topic: an attempt that was interrupted or rewound often sits in a *different, earlier*
session than the one where the work finally landed. Answering from the successful session
alone gives a tidier story than the truth — it silently drops the false start. **When a
`[rewound]` session shares a topic with the one you are reading, open it before concluding,
and say that the first attempt was abandoned.**

## Compacted sessions

A long session may have been compacted. `CONTEXT COMPACTED` marks the seam, with the
token count before and after. Turns above it were summarized out of the model's context,
so anything the assistant seems to forget just after that point was forgotten for a
reason. The turns themselves are still in the file and still render — the compaction
removed them from the model's context, not from the transcript.

## Notes

- Sessions are grouped by working directory, not by git repo. `list` with no arguments
  uses the current directory; use `--project <path>` or `--all` for anything else.
- The session you are in is hidden from `list` and `search` by default. It contains the
  question you were just asked, so it matches nearly any query and sorts first, crowding
  out the real answer. `--include-current` brings it back.
- `search` scans the current project by default; it needs `--all` to go wider.
- Timestamps render in local time.

`reference/format.md` documents the JSONL schema itself — read it only if you need to
work with the raw records rather than the rendered output.
