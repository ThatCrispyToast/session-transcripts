# CLAUDE.md

Guidance for working *on* this repo. For using the tool, read `README.md` or
`SKILL.md`.

## What this is

A Claude Code skill that renders session transcript JSONL into readable text.
Three files carry everything, all under `skills/session-transcripts/`:

| Path | Role |
|---|---|
| `SKILL.md` | frontmatter (`name`, `description`, `allowed-tools`) plus the workflow the model follows. The `description` is the trigger — edit it only with intent. |
| `scripts/transcript.py` | the whole implementation, ~1200 lines, single file |
| `reference/format.md` | the JSONL schema; progressive disclosure, loaded only for raw-record work |

At the repo root, `.claude-plugin/` holds `plugin.json` + `marketplace.json` and
makes the repo installable via `/plugin marketplace add`, and `tests/` holds the
suite. Both sit outside `skills/` on purpose: the skill directory is what gets
symlinked in the dev workflow, and it should contain only what the skill loads.

The repo is the plugin, and Claude Code auto-discovers `skills/*/SKILL.md` inside
it — neither manifest names the skill, so adding a second one needs no manifest
change. Paths inside `SKILL.md` resolve against `$CLAUDE_SKILL_DIR`, so they
survive a move; paths in this file and in `README.md` are repo-relative and do
not.

Published at `ThatCrispyToast/session-transcripts`. Users install with
`/plugin marketplace add ThatCrispyToast/session-transcripts` then
`/plugin install session-transcripts@session-transcripts`.

## Constraints

- **Stdlib only.** No dependencies, no venv, no install step. The skill has to run
  from a bare `python3`. Do not add imports outside the standard library.
- **Single file.** `transcript.py` stays self-contained; it is invoked by
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
2. **Discovery** — `encode_cwd`, `looks_like_path`, `all_session_files`, `Meta` /
   `read_meta`, `resolve_session`, `newest_session`. `read_meta` scans cheaply for
   `list`; it must stay fast because it runs over every file on disk.
3. **Tool rendering** — `render_tool_input`, `render_tool_result`. Prefer the
   `toolUseResult` field over the `tool_result` block; it is the richer,
   structured version.
4. **Turn assembly** — `Turn`, `build_turns`, `open_assistant_turn`, `is_real_prompt`,
   `chain_parent`, `mark_abandoned`. This is where the two format traps live.
   `open_assistant_turn` decides what may interrupt a response without ending it;
   `chain_parent` keeps the parent chain connected across a compaction. Note that
   `Turn.payload` is only the record that *started* the turn — `blocks` and
   `results` are what hold the whole of it.
5. **Output** — `turn_text`, `turn_summary`, `tool_target`, `tools_section`,
   `session_header`, `select_range`.
6. **Searching** — `content_strings`, `looks_like_a_blob`, `turn_haystack`,
   `turn_matches`, `matching_lines`. Every matcher goes through `turn_haystack`;
   nothing should reach back to `payload` to decide whether a turn matches.
7. **Commands** — `cmd_projects`, `cmd_list`, `cmd_outline`, `cmd_show`,
   `cmd_search`, then `main`. Shared flags come from `add_render_flags` /
   `add_select_flags`; add a flag there, not per-command, unless it genuinely
   belongs to one.

## Invariants to preserve

These were each found by breaking them. Regressions here are silent, not loud.

- **Merge assistant records by `message.id`.** One response is split across
  records, one per content block. One-record-per-turn separates a tool call from
  the text introducing it and roughly doubles the turn count. Merging must also
  look *past* harness bookkeeping — a `read_truncation_notice` attachment or an
  injected `isMeta` record can land mid-response — which is what
  `open_assistant_turn` is for. It stops at a real user prompt, so nothing merges
  across actual conversation.
- **Rewind detection is strict.** A rewind is two *real* user prompts sharing one
  `parentUuid` — "real" excludes `tool_result` content and `isMeta` records. The
  loose heuristic (any parent with multiple children) false-positives on **60%**
  of transcripts here (375 of 630), against **1.3%** — 8 files — for the strict
  rule. That 8 has not moved across 84 more transcripts than the previous survey,
  which is the useful signal: the strict rule is stable, the loose one tracks
  whatever the harness happens to be writing (65–71% per recent release). If you
  touch `mark_abandoned`, re-check both a true positive and a known false positive.
- **The calling session is excluded from `list` and `search`.** Its transcript
  contains the question being asked, so it matches nearly any query, and being the
  newest file it sorts first. In a four-model trial every single `search` spent
  part of its top ten hits on it. `CURRENT_SESSION_ID` comes from
  `CLAUDE_CODE_SESSION_ID`; `--include-current` opts back in.
- **Triage flags belong in `list` *and* `search`.** `[rewound]` / `[compacted]`
  originally went only into `list`, and the trials showed models routinely go
  `search` → `outline` → `show` without ever running `list`. A signal on a path
  nobody walks is not a signal.
- **Search matches content, not identifiers.** `NON_CONTENT_KEYS` keeps base64
  `thinking` signatures and uuids out of both the match test and the printed
  lines; without it a short pattern hits base64 and prints blobs as if they were
  matching text. `looks_like_a_blob` is the same guard one level up, for the
  base64 images that arrive inside tool results — one here is a 58 KB PNG on a
  single line. It keys on *length with no whitespace*, never length alone: a
  dumped file is long too and has to stay findable.
- **Search reads the whole turn, not `turn.payload`.** `absorb` merges an
  assistant response's blocks but leaves `payload` on the first record, and tool
  results are folded in separately — so searching the payload saw one content
  block and no tool output at all. That hid **89%** of all content, and hid it
  silently, since a search that finds nothing prints the same thing whether or not
  the text was there: `search "No such file or directory" --all` found 5 sessions
  of the 85 containing it. `turn_haystack` is the fix and every matcher goes
  through it — `select_range`, `cmd_search`, `matching_lines`. It costs about 2x
  on a full-corpus search (4.7s → 9.1s over 299 MB), which is the right trade.
  What it deliberately still does *not* reach is anything filtered out before a
  turn exists: `NOISE_TYPES`, and attachments outside `INTERESTING_ATTACHMENTS` —
  `nested_memory` above all, which is the CLAUDE.md injected into every session.
  40 files here "contain" a pattern only in that sense. Indexing it would make
  every session on the machine match anything in the user's memory files, which
  is the same crowding-out that hiding the current session exists to prevent.
- **Envelope wrappers never render as speech.** `<command-name>` and
  `<local-command-stdout>` are containers, not content. Both the `user` path and
  the `system`/`local_command` path have to strip them, in `turn_text` *and*
  `turn_summary` — the leak that shipped only affected the latter pair.
- **Content may never imitate framing.** Rendered content is indented under its
  header, and that indent was for a long time the *only* thing separating it from
  a real turn header - so a fetched page, a pasted log, or this script's own
  output quoted inside a transcript could forge a turn and have tool calls
  attributed to it. `quote_framing` prefixes any content line matching
  `FRAMING_RE` with `> `; `clip` is the chokepoint every content path goes
  through, and `matching_lines` covers `search`. Keep `FRAMING_RE` tight: it
  matched `=== banner ===` at first and fired on 326 lines of a 40-file sample,
  because shell scripts echo that constantly. Keeping it that tight is the bar - a
  false positive is pure noise in the common case. The real truncation notice is
  appended *after* quoting, on purpose; quoting it would break the `--full` hint.
  It no longer fires on zero lines of that sample: it fires **57 times in ~21,000
  rendered lines across 40 sessions**, and every one is a true positive - this
  tool's own headers, its `====` rule, and the retired `▶`/`⤷` glyphs, quoted
  inside transcripts of sessions that ran it. That is the mechanism earning its
  keep, not drift. The retired glyphs in `FRAMING_RE` are what catch the old
  renders still sitting on disk; do not tidy them out.
- **`outline` names what a tool acted on.** `{Edit}` does not say what was
  edited, and answering "which files did this session change" meant a full render
  - 89 KB for one 2.9 MB transcript, which is the cost this tool exists to avoid.
  `tool_target` covers file paths, Grep/Glob patterns and agent/skill names, and
  **excludes Bash on purpose**: Bash is 45% of all tool calls, so its description
  costs 12 points of outline size by itself (159× → 143×), nearly three times what
  everything else costs together (+4.3%, 166× → 159×). Price a new entry before
  adding one. `TOOLS_SECTION_CHARS` caps the braces so a turn with a dozen calls
  cannot run away with the line.
- **`latest` is scoped, and never the caller.** It used to mean "newest file
  anywhere under `PROJECTS_DIR`", which on a box running several sessions
  resolved to the transcript of the session doing the asking - the exact thing
  `list` and `search` hide - or to an unrelated busy project. Both were seen
  live, minutes apart. `newest_session` scopes to the working directory the way
  `list` does, falls back across projects only when this one is empty, and says
  so when it does.
- **Project lookup prefers an exact directory match.** Substring is the fallback
  that makes `--project rc-g2` and a parent path work, but as the primary rule it
  swept in every scratchpad: `/tmp/claude-<id>/<encoded-cwd>/…/scratchpad` encodes
  to a name *containing* the project's own, and three of the top five sessions
  listed for this repo were throwaway scratch sessions from a different cwd.
- **`show` says what it left out.** A turn that renders empty still holds its
  number, so a range quietly under-delivers - 2.6% of turns at default flags.
  `search` reports hits inside system-injected turns, which is how a reader gets
  sent to a `show --range` that then prints nothing at all.
- **Turn numbers are stable across flags.** Numbering happens before filtering, so
  an `outline` and a later `show --range` always line up. Filtering must never
  renumber.
- **`AskUserQuestion` results are never clipped.** The user's answer is the point
  of the turn.
- **Tool results fold under their call** via `tool_use_id` → `tool_use.id`, not by
  document order — for search as well as for display. `build_turns` attaches them
  to the calling turn (`Turn.results`) precisely because searching has to know
  what a turn contains before deciding whether to print it.
- **`main` forces UTF-8 on stdout.** Windows Python encodes to cp1252 whenever
  stdout is a pipe rather than a console — precisely how a tool harness reads it.
  Dropping `force_utf8_output` breaks Windows two ways, and the quiet one is
  worse: `show` dies on the first codepoint cp1252 lacks, while `outline` and
  `list` exit 0 having written bytes no UTF-8 reader can decode. An interactive
  Windows console hides both, because Python writes UTF-8 there. This used to be
  justified by the framing glyphs; since those went ASCII the exposure is the
  *content* — emoji, CJK, the model's own em dashes — so the fixture in
  `TestCrossPlatform` carries such codepoints deliberately. Do not "simplify"
  them out.
- **Nothing may assume `/` is the separator.** `C:\Users\me\proj` contains no
  forward slash, so the old `"/" in project` test sent native Windows paths to
  the substring-match branch, where they matched nothing. `looks_like_path` is
  the test; it stays platform-independent, since transcripts get copied between
  machines.

## Testing

`tests/test_transcript.py` — stdlib `unittest`, no dependencies, same constraint
as the script. It loads `transcript.py` by path, since the script is invoked
rather than imported.

```bash
python3 -m unittest discover -s tests -v
TRANSCRIPT_FULL_SWEEP=1 python3 -m unittest discover -s tests   # every transcript, not a sample
```

Two halves, and both matter. Most classes build **synthetic records** — a rewind,
a compaction, a response split by a truncation notice are all too rare to find on
demand in a real corpus, and a fixture states the shape exactly. `TestRealTranscripts`
then sweeps **whatever is actually on the machine** (630 files, 299 MB here), which
is what catches format drift no fixture anticipates; it skips cleanly on a machine
with none, and samples ~40 files unless `TRANSCRIPT_FULL_SWEEP=1`.

Two things this corpus cannot exercise, so do not read a green suite as covering
them. **Sidechains**: all 630 files have `isSidechain: false` throughout, so
`[subagent]` and `--no-sidechains` have no coverage beyond not crashing.
**Compaction**: there is not one genuine `compact_boundary` record anywhere in it —
every file that greps positive is quoting the string, this repo's own source
included — so the whole compaction path rests on synthetic fixtures and the
original survey. Check with a parse, never a grep.

A test that cannot fail is worse than no test. After changing a fix, revert it and
confirm the suite goes red — every fix currently in the file was checked that way.

The sweep still has value by hand, since it exercises the real CLI end to end:

```bash
T=skills/session-transcripts/scripts/transcript.py
for f in ~/.claude/projects/*/*.jsonl; do
  python3 "$T" outline "$f" >/dev/null || echo "FAIL outline $f"
  python3 "$T" show "$f" --full --thinking --meta >/dev/null || echo "FAIL show $f"
done
```

After a rendering change, eyeball a session that exercises the hard cases rather
than trusting exit status — merged multi-block turns, a rewound session, an
`AskUserQuestion`, and a Bash call with both stdout and stderr. Sidechains and
compaction are the two you cannot eyeball here at all; see the note above.

### Checking Windows behaviour from a Linux box

`TestCrossPlatform` reproduces the Windows failures anywhere, by forcing the code
page rather than the platform — but it only covers the failures already known
about. To exercise the genuine article, run the real python.org build under Wine.
No Windows licence, no VM, no system change:

```bash
cd "$(mktemp -d)"                    # keep the download out of the repo
curl -sLO https://www.python.org/ftp/python/3.13.1/python-3.13.1-embed-amd64.zip
python3 -c "import zipfile;zipfile.ZipFile('python-3.13.1-embed-amd64.zip').extractall('winpy')"
export WINEPREFIX=$PWD/wineprefix WINEDEBUG=-all
WINPY=$PWD/winpy/python.exe
cd -                                 # back to the repo
nix shell nixpkgs#wine64 --command wine "$WINPY" "$(pwd)/tests/test_transcript.py"
```

Pass the test file as an absolute Unix path: Wine hands argv through untranslated,
and a leading `/` resolves against the current drive, which is the `Z:` mapping of
`/`. A relative path works too; a `~` does not.

That reports `sys.platform: win32` and `cp1252`, which is the whole point — the
encoding bugs live in CPython's own `GetACP` path, so this finds them for real.
`TestRealTranscripts` skips there (Wine's `C:\users\…` has no `.claude`), so it
runs 5 tests fewer than on Linux; that is expected, not drift.

Wine cannot settle two things: whether the Microsoft Store `python3.exe` alias
stub intercepts the command, and how Claude Code's own Windows build encodes
project directory names. The second is the one that would actually matter, and it
needs Claude Code installed and signed in on Windows to answer.

### Trialling the skill against real models

Unit tests prove the renderer is correct; they say nothing about whether a model
*uses* it well. That needs headless trials. The harness is a throwaway project dir
with the skill symlinked into `.claude/skills/`, driven per model and captured as
a full event log:

```bash
mkdir -p lab/.claude/skills && ln -s "$PWD/skills/session-transcripts" lab/.claude/skills/
cd lab && printf '%s' "$PROMPT" | claude -p --model claude-sonnet-5 \
  --output-format stream-json --verbose \
  --allowedTools Bash Read Grep Glob Skill --disallowedTools Write Edit > run.jsonl
```

`stream-json` records every tool call, which is the ground truth — self-reported
summaries are not. Pass the prompt on **stdin**: variadic flags like `--allowedTools`
swallow a positional prompt and the run dies with "Input must be provided".

Three things this caught that no unit test could: `$CLAUDE_SKILL_DIR` is not set
(the documented invocation expanded to `/scripts/transcript.py` and one model
answered by running `find /`), `search` was burying real hits under the caller's
own session, and a flag added only to `list` never reached models that go straight
from `search` to `outline`.

Two cautions if you repeat it. Trial sessions land in `~/.claude/projects` and
pollute the next round's corpus, so move that project dir aside between runs. And
n=1 per model is noise: across three rounds the same model both caught and missed
the same rewind. Treat these as instrumentation checks, not benchmarks — the
finding is whether a signal is *reachable*, not which model scores best.

Check the compression ratio when output format changes; it is the tool's reason
for existing. Measure it the way the numbers below were measured — run the old
and new scripts over the *same* file list, because sampling noise between two
runs is larger than the effect of most changes.

Currently **159× for `outline` and 10.7× for `show`** over a 28.6 MB, 35-file
sample. Those replace a documented "~220× / ~15×, floor 170× / 10×" that the
corpus had quietly fallen through: the assertion only ever guarded 100×, so
`show` drifted under its stated floor with the suite green throughout. Both are
asserted now, at 100× and 5× — set below the aggregate rather than beside it,
because the ratio is a strong function of session size and the mix moves. It runs
44× on a small transcript and 492× on the 13.5 MB one, which is the right way
round: the compression is largest exactly where it is needed.

## Releasing

The manifests carry a `version` in two places and they must agree:
`.claude-plugin/plugin.json` and the plugin entry in
`.claude-plugin/marketplace.json`. Bump both together — `claude plugin tag`
refuses to tag when they disagree.

```bash
claude plugin validate .          # both manifests
claude plugin marketplace add .   # install from the local path and check
claude plugin install session-transcripts@session-transcripts
claude plugin details session-transcripts   # expect: Skills (1) session-transcripts
```

Undo that local test before pushing, or the plugin keeps resolving to your
working tree instead of the remote:

```bash
claude plugin uninstall session-transcripts@session-transcripts
claude plugin marketplace remove session-transcripts
```

Users pull changes with `/plugin update session-transcripts`. Version pinning
means they see nothing until the `version` string changes, so a content fix
without a bump reaches no one.

## Conventions

- Em dashes and `·` separators in rendered output; `->` for a tool call, `<-` for
  its result, `!` for an abandoned turn, `> ` for a content line quoted because
  it imitated framing.
- **Framing is ASCII, and that is a measured decision, not taste.** Priced against
  a byte-level BPE tokenizer over 40 real transcripts: `·` costs **0** tokens
  (1,732 uses — it merges with its neighbours), `…` costs 6 tokens across 442
  uses, and the old `▶` cost 0 under o200k but 2 under cl100k. The old `⤷`
  (U+2937) cost **3 tokens every time**, 812 uses, and was single-handedly
  responsible for 99.6% of the available saving — replacing the pair with `->`
  and `<-` cut 0.410% of all rendered output. The lesson is not "avoid Unicode":
  `·` is free and stays. It is that a *rare* codepoint is a tokenizer lottery,
  and framing repeats once per tool call forever. Price a new glyph before
  adding one, and prefer ASCII where the look is not doing real work.
- Truncation is always visible — `... (+N lines, use --full)`, never a silent cut.
- Docs are prose over bullet soup, and every claim about the format should be one
  you verified against real transcripts, not inferred.
