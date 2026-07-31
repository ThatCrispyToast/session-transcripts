# Session Transcripts

Read past [Claude Code](https://docs.anthropic.com/en/docs/claude-code) sessions
as text a model can actually use. Claude Code appends every session to
`~/.claude/projects/<encoded-cwd>/<session-id>.jsonl` — raw event logs, one JSON
record per content block, interleaved with harness bookkeeping. This is a skill
that renders them: find the session, outline it, then pull only the turns that
matter.

```
$ transcript.py outline c93a1254

 [   4] 13:47:56 USER   Create a claude skill that allows a claude session to parse transcripts…
 [   5] 13:47:59 ASST   I'll create this skill. Let me first look at the actual transcript… {Bashx2}
 [  20] 13:54:06 ASST   Confirmed — each assistant response is split across multiple records… {Edit}
 [  35] 13:56:17 ASST   Real rewinds exist in only 2 files — my detector was over-broad… {Edit}
```

## Quick start

1. **Install it as a skill** — symlink this directory into your skills dir:

   ```bash
   ln -s "$PWD" ~/.claude/skills/session-transcripts
   ```

   It becomes available in the *next* session; the skill list is computed at
   session start.

2. **Ask for it in plain language.** The skill triggers on "what did we do last
   time", "find the session where we fixed the auth bug", "recover what got
   compacted", and similar.

3. **Or run the script directly** — Python 3, stdlib only, nothing to install:

   ```bash
   python3 scripts/transcript.py list --all --since 7d
   ```

> [!IMPORTANT]
> Never dump a whole transcript into context. A long session is ~2 MB of JSONL.
> Go `list` → `outline` → `show --range N-M`. On the largest transcript here,
> 1.9 MB becomes an 11 KB outline (170×) or 190 KB fully rendered (10×).

## What it does

- **Finds the session** — `list` across one project or all of them, filtered by
  age or by a regex over title and first prompt. Sessions are grouped by working
  directory, not by git repo.
- **Outlines before it renders** — one line per turn with a timestamp, a snippet,
  and the tools that turn called (`{Read, Bash}`). Turn numbers are stable across
  every flag, so an outline and a later `show` always agree.
- **Renders only the slice you asked for** — by range, by `--last N`, or by regex
  with `-C` turns of context. Tool results fold in under the call that produced
  them (`▶` call, `⤷` result), clipped to 20 lines unless you say otherwise.
- **Flags rewound branches** — if you edited a message and re-ran, both branches
  are still in the file. Abandoned turns are labelled and marked `!`, so a rewind
  never gets reported as what actually happened.
- **Searches across history** — regex over every transcript on disk, one line per
  hit or fully rendered with `--render`.

## How it works

```
~/.claude/projects/<encoded-cwd>/<session-id>.jsonl
        │  one JSON record per content block
        ▼
iter_records  ──  skip bookkeeping types, tolerate unknown ones
        ▼
build_turns   ──  merge assistant records sharing message.id
              ──  attach tool_result records to the call they answer
        ▼
mark_abandoned ─  strict rewind detection (two real prompts, one parent)
        ▼
outline (1 line/turn)  ·  show (folded tool I/O)  ·  search (regex)
```

Everything is derived from surveying ~400 transcripts written by Claude Code
2.1.x. The format is undocumented, so the parser skips record types it does not
recognize rather than failing on them.

## Commands

```bash
T=scripts/transcript.py

python3 $T projects                      # every project with transcripts, newest first
python3 $T list                          # sessions for the current directory
python3 $T list --all --since 7d         # across all projects, last week
python3 $T list --grep auth              # match on title / first prompt / id

python3 $T outline <session>             # one line per turn — start here
python3 $T show <session> --range 40-60  # render just those turns
python3 $T show <session> --grep docker -C 3

python3 $T search "flaky test" --all     # regex across transcripts
```

`<session>` accepts a full session id, a unique id prefix (`c93a1254`), a path to
a `.jsonl`, or `latest`.

| Flag | Effect |
|---|---|
| `--range N-M` | turns N to M (`N`, `N-`, `-M` also work) |
| `--last N` | last N turns — good for "how did the session end" |
| `--grep RE` / `-C N` | only matching turns, with N turns of context |
| `--thinking` | include reasoning blocks (hidden by default) |
| `--full` | no truncation anywhere — use on a narrow `--range` |
| `--max-lines N` | lines per tool result, default 20 |
| `--main-branch` | drop rewound branches |
| `--no-sidechains` | drop subagent turns |
| `--meta` | include system-injected messages |

Defaults are tuned for reading in context. When a clipped result is the thing you
actually need, re-run that one turn with `--range N --full`.

## Two traps in the format

Both would silently corrupt naive parsing. Both are handled here and documented
in [`reference/format.md`](reference/format.md).

**An assistant response is split across several records.** Each carries one
content block — `thinking`, `text`, or `tool_use` — and they all share
`message.id`. Rendering one record per turn inflated an 11-turn session to 19 and
separated every tool call from the sentence introducing it. Merge by `message.id`.

**`parentUuid` branching is mostly a false signal.** A parent with several
children is *normal*: an assistant's next content block and the `tool_result`
answering its tool call both hang off the same parent. Treating that as a fork
flagged 27 of ~400 transcripts as rewound. The strict signal — two *real* user
prompts sharing one `parentUuid`, where "real" excludes tool results and `isMeta`
records — finds the actual number: 2, with no false positives.

## Layout

| Path | What |
|---|---|
| `SKILL.md` | the skill itself — frontmatter triggers, then the wide-to-narrow workflow |
| `scripts/transcript.py` | the renderer; stdlib Python 3, five subcommands |
| `reference/format.md` | the JSONL schema, read only when working with raw records |

## Notes

- The current session's transcript is being written live, so it is always
  incomplete.
- `search` scans the current project unless given `--all`.
- Timestamps render in local time; records store UTC.
- Renaming a project directory does not move its old transcripts — they stay
  filed under the path they were written from. `list --all` still finds them.
