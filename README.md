# Session Transcripts

Read past [Claude Code](https://docs.anthropic.com/en/docs/claude-code) sessions
as text a model can use. Claude Code appends every session to
`~/.claude/projects/<encoded-cwd>/<session-id>.jsonl` - raw event logs, one JSON
record per content block. This skill renders them: find the session, outline it,
pull only the turns that matter. A 1.9 MB transcript outlines to 11 KB.

```
$ transcript.py outline c93a1254

 [   4] 13:47:56 USER   Create a claude skill that allows a claude session to parse transcripts…
 [   5] 13:47:59 ASST   I'll create this skill. Let me first look at the actual transcript… {Bashx2}
 [  20] 13:54:06 ASST   Confirmed - each assistant response is split across multiple records… {Edit}
 [  35] 13:56:17 ASST   Real rewinds exist in only 2 files - my detector was over-broad… {Edit}
```

## Install

```
/plugin marketplace add ThatCrispyToast/session-transcripts
/plugin install session-transcripts@session-transcripts
```

Available next session. Then ask in plain language: "what did we do last time",
"find the session where we fixed the auth bug", "recover what got compacted".

The repo is private, so the clone uses your git credentials over SSH. For HTTPS,
set `CLAUDE_CODE_PLUGIN_PREFER_HTTPS=1`.

The script also runs standalone - Python 3, stdlib only:

```bash
python3 scripts/transcript.py list --all --since 7d
```

## What it does

- **Finds the session** - `list` covers one project or all of them, filtered by
  age or by a regex over title and first prompt. Sessions group by working
  directory, not by git repo.
- **Outlines before it renders** - one line per turn: timestamp, snippet, and the
  tools that turn called (`{Read, Bash}`). Turn numbers hold steady across every
  flag, so an outline and a later `show` always agree.
- **Renders only the slice you ask for** - by range, by `--last N`, or by regex
  with `-C` turns of context. Tool results fold in under the call that produced
  them (`▶` call, `⤷` result), clipped to 20 lines unless you say otherwise.
- **Flags rewound branches** - edit a message and re-run, and both branches stay
  in the file. Abandoned turns carry a label and an `!`, so you never mistake a
  rewind for what happened.
- **Searches across history** - regex over every transcript on disk, one line per
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

Everything here comes from surveying ~400 transcripts written by Claude Code
2.1.x. Anthropic documents none of it, so the parser skips record types it
doesn't recognize instead of dying on them.

## Commands

```bash
T=scripts/transcript.py

python3 $T projects                      # every project with transcripts, newest first
python3 $T list                          # sessions for the current directory
python3 $T list --all --since 7d         # across all projects, last week
python3 $T list --grep auth              # match on title / first prompt / id

python3 $T outline <session>             # one line per turn - start here
python3 $T show <session> --range 40-60  # render just those turns
python3 $T show <session> --grep docker -C 3

python3 $T search "flaky test" --all     # regex across transcripts
```

`<session>` takes a full session id, a unique id prefix (`c93a1254`), a path to a
`.jsonl`, or `latest`.

| Flag | Effect |
|---|---|
| `--range N-M` | turns N to M (`N`, `N-`, `-M` also work) |
| `--last N` | last N turns - good for "how did the session end" |
| `--grep RE` / `-C N` | only matching turns, with N turns of context |
| `--thinking` | include reasoning blocks (hidden by default) |
| `--full` | no truncation anywhere - use on a narrow `--range` |
| `--max-lines N` | lines per tool result, default 20 |
| `--main-branch` | drop rewound branches |
| `--no-sidechains` | drop subagent turns |
| `--meta` | include system-injected messages |

When a clipped result holds the thing you need, re-run that turn with
`--range N --full`.

## Two traps in the format

Both wreck naive parsing, and both fail quietly.
[`reference/format.md`](reference/format.md) has the details.

**An assistant response spans several records.** Each carries one content block -
`thinking`, `text`, or `tool_use` - and they all share `message.id`. One record
per turn inflated an 11-turn session to 19 and cut every tool call away from the
sentence introducing it. Merge by `message.id`.

**`parentUuid` branching lies.** A parent with several children is normal: an
assistant's next content block and the `tool_result` answering its tool call both
hang off the same parent. Treating that as a fork flagged 27 of ~400 transcripts
as rewound. The strict signal - two real user prompts sharing one `parentUuid`,
where "real" excludes tool results and `isMeta` records - finds 2, with no false
positives.

## Layout

| Path | What |
|---|---|
| `SKILL.md` | the skill itself - frontmatter triggers, then the wide-to-narrow workflow |
| `scripts/transcript.py` | the renderer; stdlib Python 3, five subcommands |
| `reference/format.md` | the JSONL schema, read only when working with raw records |
| `.claude-plugin/` | `plugin.json` and `marketplace.json`, so the repo installs as a plugin |

The repo root doubles as the plugin root, where Claude Code picks up a bare
`SKILL.md`. No `skills/` subdirectory needed.

## Working on it

Clone it and point your skills directory at it:

```bash
git clone git@github.com:ThatCrispyToast/session-transcripts.git
ln -s "$PWD/session-transcripts" ~/.claude/skills/session-transcripts
```

`SKILL.md` edits land in the running session. Run `claude plugin validate .`
after touching either manifest.

Pick one method or the other. Doing both loads the skill twice.

## Notes

- Claude Code writes the current session's transcript live, so it's always
  incomplete.
- `search` scans the current project unless you pass `--all`.
- Timestamps render in local time; the records store UTC.
- Renaming a project directory leaves its old transcripts where they are, filed
  under the path they came from. `list --all` still finds them.
