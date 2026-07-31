# CLAUDE.md

Guidance for working *on* this repo. For using the tool, read `README.md` or
`SKILL.md`.

## What this is

A Claude Code skill that renders session transcript JSONL into readable text.
Three files carry everything:

| Path | Role |
|---|---|
| `SKILL.md` | frontmatter (`name`, `description`, `allowed-tools`) plus the workflow the model follows. The `description` is the trigger — edit it only with intent. |
| `scripts/transcript.py` | the whole implementation, ~1100 lines, single file |
| `reference/format.md` | the JSONL schema; progressive disclosure, loaded only for raw-record work |

The directory is symlinked to `~/.claude/skills/session-transcripts`, so edits
here are live for the next session. Not a git repo — nothing is committed. Ask
before `git init`.

## Constraints

- **Stdlib only.** No dependencies, no venv, no install step. The skill has to run
  from a bare `python3`. Do not add imports outside the standard library.
- **Single file.** `scripts/transcript.py` stays self-contained; it is invoked by
  path, not imported.
- **Never break the read-only contract.** This tool only reads
  `~/.claude/projects`. It must not write, move, or delete anything there.
- **Skip unknown records, never fail on them.** The transcript format is
  undocumented and changes between Claude Code releases. New `type` values must
  degrade to "ignored", not to a traceback.

## Structure of transcript.py

Sections are marked by `# ---` banners, in pipeline order:

1. **Text helpers** — `clip`, `oneline`, `strip_reminders`, `command_stdout`,
   `slash_command`. Envelope strippers matter: slash-command and
   `<local-command-stdout>` wrappers must not render as user speech.
2. **Discovery** — `encode_cwd`, `all_session_files`, `Meta` / `read_meta`,
   `resolve_session`. `read_meta` scans cheaply for `list`; it must stay fast
   because it runs over every file on disk.
3. **Tool rendering** — `render_tool_input`, `render_tool_result`. Prefer the
   `toolUseResult` field over the `tool_result` block; it is the richer,
   structured version.
4. **Turn assembly** — `Turn`, `build_turns`, `is_real_prompt`, `mark_abandoned`.
   This is where the two format traps live.
5. **Output** — `turn_text`, `turn_summary`, `session_header`, `select_range`.
6. **Commands** — `cmd_projects`, `cmd_list`, `cmd_outline`, `cmd_show`,
   `cmd_search`, then `main`. Shared flags come from `add_render_flags` /
   `add_select_flags`; add a flag there, not per-command, unless it genuinely
   belongs to one.

## Invariants to preserve

These were each found by breaking them. Regressions here are silent, not loud.

- **Merge assistant records by `message.id`.** One response is split across
  records, one per content block. One-record-per-turn separates a tool call from
  the text introducing it and roughly doubles the turn count.
- **Rewind detection is strict.** A rewind is two *real* user prompts sharing one
  `parentUuid` — "real" excludes `tool_result` content and `isMeta` records. The
  loose heuristic (any parent with multiple children) false-positives on ~7% of
  transcripts. If you touch `mark_abandoned`, re-check both a true positive and a
  known false positive.
- **Turn numbers are stable across flags.** Numbering happens before filtering, so
  an `outline` and a later `show --range` always line up. Filtering must never
  renumber.
- **`AskUserQuestion` results are never clipped.** The user's answer is the point
  of the turn.
- **Tool results fold under their call** via `tool_use_id` → `tool_use.id`, not by
  document order.

## Testing

There is no test suite. Verification is a sweep over real transcripts — ~400 of
them on this machine, which is the point: they cover format variation no fixture
would.

```bash
# every transcript renders under every flag combination, no traceback
python3 scripts/transcript.py list --all --limit 9999 --include-empty

for f in ~/.claude/projects/*/*.jsonl; do
  python3 scripts/transcript.py outline "$f" >/dev/null || echo "FAIL outline $f"
  python3 scripts/transcript.py show "$f" --full --thinking --meta >/dev/null \
    || echo "FAIL show $f"
done
```

After a rendering change, eyeball a session that exercises the hard cases rather
than trusting exit status — merged multi-block turns, a rewound session, a
sidechain, an `AskUserQuestion`, and a Bash call with both stdout and stderr.

Check the compression ratio when output format changes; it is the tool's reason
for existing. Target roughly 170× for `outline` and 10× for `show`.

## Conventions

- Em dashes and `·` separators in rendered output; `▶` for a tool call, `⤷` for
  its result, `!` for an abandoned turn.
- Truncation is always visible — `... (+N lines, use --full)`, never a silent cut.
- Docs are prose over bullet soup, and every claim about the format should be one
  you verified against real transcripts, not inferred.
