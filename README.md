# Session Transcripts

Claude Code writes every session to
`~/.claude/projects/<encoded-cwd>/<session-id>.jsonl`: one JSON record per
content block, interleaved with harness bookkeeping. Nobody was meant to read it
directly, and a model that tries spends its context on `parentUuid` fields.

This renders it instead. The largest transcript in a ~400 session survey ran
1.9 MB. As an outline it's 11 KB.

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

Available next session. Then ask for it however you'd say it out loud: "what did
we do last time", "find the session where we fixed the auth bug".

The repo is private, so the clone goes over SSH using your git credentials. Set
`CLAUDE_CODE_PLUGIN_PREFER_HTTPS=1` if you want HTTPS.

Nothing to install for the script itself. Python 3, no dependencies:

```bash
python3 scripts/transcript.py list --all --since 7d
```

## Three commands, in that order

`list` finds the session. `outline` prints one line per turn. `show --range`
renders the turns you picked out.

```bash
T=scripts/transcript.py

python3 $T list --all --since 7d      # newest first, filtered by age
python3 $T list --grep auth           # or by title / first prompt / id
python3 $T outline c93a1254           # an id prefix works, so does 'latest'
python3 $T show c93a1254 --range 40-60
```

Skipping the outline puts you back to dumping 190 KB into context, which is the
thing this exists to avoid.

An outline line:

```
 [   4] 11:49:08 ASST   Found description.txt locally. Now let me check… {Read, Bash}
```

`[4]` is what you pass to `--range`. The braces list tools that turn called. Turn
numbers never shift between commands, so an outline and a later `show` always
agree - numbering happens before filtering, on purpose. In `show`, `▶` marks a
tool call and `⤷` its result, folded in underneath.

| Flag | Effect |
|---|---|
| `--range N-M` | turns N to M (`N`, `N-`, `-M` also work) |
| `--last N` | how did the session end |
| `--grep RE` / `-C N` | matching turns, with N turns of context |
| `--thinking` | reasoning blocks, hidden otherwise |
| `--full` | no truncation at all; use it on one turn, not a session |
| `--max-lines N` | lines per tool result, default 20 |
| `--main-branch` | drop rewound branches |
| `--no-sidechains` | drop subagent turns |
| `--meta` | system-injected messages |

Two commands sit outside the main flow: `projects` lists every directory that has
transcripts, and `search` runs a regex across them (`--all` to leave the current
project).

## The two things that make this hard

Neither is documented. Both produce plausible wrong output instead of an error,
which is the worst way for a parser to fail.

**One assistant response is split across several records.** Each holds a single
content block, and they all share `message.id`. Render them one per turn and an
11-turn session reports as 19, every tool call divorced from the sentence that
introduced it. Merge on `message.id`.

**`parentUuid` looks like it tracks rewinds. It doesn't.** A parent with several
children is the ordinary case - an assistant's next content block and the
`tool_result` answering its tool call both hang off the same parent. The obvious
detector reads that as a fork and flags 27 of ~400 transcripts as rewound. The
real signal is two genuine user prompts sharing one `parentUuid`, where "genuine"
excludes tool results and `isMeta` records. That finds 2, and both are real.

Rewound branches stay in the file, so they get a label and an `!` in the outline
rather than disappearing. Reporting an abandoned turn as what happened is worse
than showing both. `--main-branch` hides them when you want the conversation as
it finally stood.

Everything above came out of surveying ~400 transcripts from Claude Code 2.1.x.
The format is undocumented and moves between releases, so the parser ignores
record types it doesn't know rather than falling over.
[`reference/format.md`](reference/format.md) has the schema.

## Layout

| Path | What |
|---|---|
| `SKILL.md` | the skill: frontmatter triggers, then the workflow |
| `scripts/transcript.py` | the renderer, ~1100 lines of stdlib Python |
| `reference/format.md` | the JSONL schema |
| `.claude-plugin/` | manifests, so `/plugin marketplace add` works |

The repo root is also the plugin root. Claude Code picks up a `SKILL.md` sitting
there, which is why there's no `skills/` subdirectory.

To hack on it, clone and symlink instead of installing:

```bash
git clone git@github.com:ThatCrispyToast/session-transcripts.git
ln -s "$PWD/session-transcripts" ~/.claude/skills/session-transcripts
```

`SKILL.md` edits take effect in the running session. Don't do both - the plugin
and the symlink will each load the skill.

## Odds and ends

- The session you're in is still being written, so its transcript is incomplete.
- Sessions are keyed by working directory, not by repo. Rename a project and the
  old transcripts stay under the old path; `list --all` still turns them up.
- Timestamps display local. The records store UTC.
