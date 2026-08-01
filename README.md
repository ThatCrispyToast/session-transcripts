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

`owner/repo` sources clone over SSH. Set `CLAUDE_CODE_PLUGIN_PREFER_HTTPS=1` if
you don't have a key loaded.

Nothing to install for the script itself. Python 3, no dependencies:

```bash
python3 skills/session-transcripts/scripts/transcript.py list --all --since 7d
```

On Windows, invoke it with `py -3` rather than `python3`.

## Human Use

`list` finds the session. `outline` prints one line per turn. `show --range`
renders the turns you picked out.

```bash
T=skills/session-transcripts/scripts/transcript.py

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

`list` and `search` tag a session `[rewound]` or `[compacted]` when its history
needs care, and both hide the session you are running in - it contains whatever
you just asked, so it matches nearly every query and sorts first. `--include-current`
turns that off.

## Transcript Format

One JSON object per line. `type` says what it is: `user`, `assistant`, `system`,
and a dozen bookkeeping variants. Only the first two carry conversation.

An assistant response is not one record. Every content block gets its own line,
tied together by `message.id`. Turn 5 in the outline above is three of them:

```json
{"type":"assistant","uuid":"73aa8fef…","message":{"id":"msg_011CdaZ…","content":[{"type":"text","text":"I'll create this skill. Let me…"}]}}
{"type":"assistant","uuid":"348d27cf…","message":{"id":"msg_011CdaZ…","content":[{"type":"tool_use","name":"Bash"}]}}
{"type":"assistant","uuid":"63bede02…","message":{"id":"msg_011CdaZ…","content":[{"type":"tool_use","name":"Bash"}]}}
```

Count those as three turns and an 11-turn session renders as 19, tool calls
stranded from the sentence that introduced them. Group on `message.id` before
anything else.

`parentUuid` reads like a linked list and behaves like a tree, where a node with
several children is completely ordinary: the text block above and the
`tool_result` answering its Bash call both point at the same parent. Treat any
fork as a rewind and 335 of the 546 transcripts on this machine look rewound. The
true count is 8. That gap widens with every release - the same loose rule flags
36% of transcripts written by Claude Code 2.1.140 and 74% of those written by
2.1.220.

A rewind is two user prompts under one parent, and `type: "user"` alone doesn't
identify a prompt - tool results arrive under that type, as does injected context
flagged `isMeta`. Exclude both and the false positives go away.

Both halves of a rewind stay on disk. The outline marks the abandoned side with
`!` rather than hiding it; `--main-branch` hides it when you want the
conversation as it finally stood.

A long session may also have been compacted, which is a different kind of seam: a
`compact_boundary` record marks where the history above it was summarized out of
the model's context. It renders as `CONTEXT COMPACTED`, with the token count
before and after. Without it a transcript reads as one continuous conversation in
which the model abruptly forgets what it just did.

[`format.md`](skills/session-transcripts/reference/format.md) has the rest: record types,
the `toolUseResult` shapes per tool, and the lossy cwd encoding.

## Layout

| Path | What |
|---|---|
| `skills/session-transcripts/SKILL.md` | the skill: frontmatter triggers, then the workflow |
| `skills/session-transcripts/scripts/transcript.py` | the renderer, ~1200 lines of stdlib Python |
| `skills/session-transcripts/reference/format.md` | the JSONL schema |
| `.claude-plugin/` | manifests, so `/plugin marketplace add` works |
| `tests/` | `python3 -m unittest discover -s tests` |

The repo is the plugin; the skill sits under `skills/` where Claude Code finds it
by convention. Room for a second one later.

To hack on it, clone and symlink the skill directory instead of installing:

```bash
git clone https://github.com/ThatCrispyToast/session-transcripts.git
ln -s "$PWD/session-transcripts/skills/session-transcripts" \
      ~/.claude/skills/session-transcripts
```

`SKILL.md` edits take effect in the running session. Don't do both - the plugin
and the symlink will each load the skill.

## Notes

- The session you're in is still being written, so its transcript is incomplete.
- Sessions are keyed by working directory, not by repo. Rename a project and the
  old transcripts stay under the old path; `list --all` still turns them up.
- Timestamps display local. The records store UTC.

## License

[MIT](LICENSE). Unaffiliated with Anthropic. The transcript format is
undocumented and can change in any Claude Code release.
