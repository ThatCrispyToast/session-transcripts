#!/usr/bin/env python3
"""
transcript.py - read Claude Code session transcripts as readable text.

Claude Code stores every session as JSONL under ~/.claude/projects/<encoded-cwd>/<session-id>.jsonl.
This renders those files into compact, structured text meant to be read by a model or a human.

Subcommands:
  projects            list project directories with session counts
  list                list sessions (newest first)
  outline             one line per turn - cheap map of a session
  show                render a session (or a slice of one)
  search              regex search across transcripts

Stdlib only. No dependencies.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECTS_DIR = Path(
    os.environ.get("CLAUDE_PROJECTS_DIR", Path.home() / ".claude" / "projects")
)

# The session doing the searching. Its transcript already contains the question
# being asked, so it matches almost any query and — being the newest file — sorts
# to the top of every result list. Hidden by default; --include-current brings it
# back. Naming a session explicitly always works regardless.
CURRENT_SESSION_ID = (
    os.environ.get("CLAUDE_CODE_SESSION_ID") or os.environ.get("CLAUDE_SESSION_ID") or ""
)

# Payload keys whose values are identifiers or crypto blobs, not content. A
# `thinking` block's base64 `signature` is long enough to match many patterns by
# accident, which puts nonsense in search output.
NON_CONTENT_KEYS = {
    "signature", "uuid", "parentUuid", "logicalParentUuid", "leafUuid",
    "sessionId", "requestId", "id", "tool_use_id",
}

# Record types that carry no conversational meaning.
NOISE_TYPES = {
    "file-history-snapshot",
    "file-history-delta",
    "ai-title",
    "mode",
    "permission-mode",
    "bridge-session",
    "last-prompt",
    "agent-color",
    "agent-name",
    "pr-link",
    "queue-operation",
}

# Attachment subtypes worth surfacing; everything else is harness bookkeeping.
INTERESTING_ATTACHMENTS = {
    "queued_command",
    "plan_mode_exit",
    "edited_text_file",
    "read_truncation_notice",
}

MAX_LINE_CHARS = 400


# --------------------------------------------------------------------------
# low-level helpers
# --------------------------------------------------------------------------


def iter_records(path: Path):
    """Yield parsed JSON objects from a .jsonl file, skipping unparseable lines."""
    with path.open("r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


def parse_ts(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def fmt_ts(dt, with_date=False):
    if dt is None:
        return "??:??"
    local = dt.astimezone()
    return local.strftime("%Y-%m-%d %H:%M" if with_date else "%H:%M:%S")


def fmt_duration(seconds):
    if seconds is None:
        return "?"
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m{seconds % 60:02d}s"
    return f"{seconds // 3600}h{(seconds % 3600) // 60:02d}m"


def clip(text, max_lines, indent="     "):
    """Trim text to max_lines and cap absurdly long single lines.

    Returns the indented block. max_lines <= 0 means no limit.
    """
    if text is None:
        return ""
    text = str(text).rstrip("\n")
    if not text:
        return ""
    lines = text.split("\n")
    capped = [
        ln if len(ln) <= MAX_LINE_CHARS else ln[:MAX_LINE_CHARS] + f" ... (+{len(ln) - MAX_LINE_CHARS} chars)"
        for ln in lines
    ]
    if max_lines > 0 and len(capped) > max_lines:
        dropped = len(capped) - max_lines
        capped = capped[:max_lines] + [f"... (+{dropped} lines, use --full)"]
    return "\n".join(indent + ln for ln in capped)


ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]|\[[0-9]{1,2}m")


def oneline(text, width=100):
    if not text:
        return ""
    flat = " ".join(ANSI_RE.sub("", str(text)).split())
    return flat if len(flat) <= width else flat[: width - 1] + "…"


def strip_reminders(text, full=False):
    """Collapse injected <system-reminder> blocks unless --full."""
    if full or not text or "<system-reminder>" not in text:
        return text

    def repl(match):
        return f"[system-reminder collapsed, {len(match.group(0))} chars - use --full]"

    return re.sub(r"<system-reminder>.*?</system-reminder>", repl, text, flags=re.S).strip()


def command_stdout(text):
    """Return the payload if text is only a <local-command-stdout> envelope."""
    if not text or "<local-command-stdout>" not in text:
        return None
    m = re.search(r"<local-command-stdout>(.*?)</local-command-stdout>", text, re.S)
    if not m:
        return None
    remainder = re.sub(r"<local-command-stdout>.*?</local-command-stdout>", "", text, flags=re.S)
    if remainder.strip():
        return None  # mixed content; treat as a normal message
    return m.group(1).strip() or "(no output)"


def slash_command(text):
    """Return '/name args' if text is a slash-command envelope, else None."""
    if not text or "<command-name>" not in text:
        return None
    name = re.search(r"<command-name>(.*?)</command-name>", text, re.S)
    args = re.search(r"<command-args>(.*?)</command-args>", text, re.S)
    stdout = re.search(r"<local-command-stdout>(.*?)</local-command-stdout>", text, re.S)
    out = (name.group(1).strip() if name else "?") + " " + (args.group(1).strip() if args else "")
    out = out.strip()
    if stdout and stdout.group(1).strip():
        out += f"\n  -> {oneline(stdout.group(1), 200)}"
    return out


# --------------------------------------------------------------------------
# session discovery + metadata
# --------------------------------------------------------------------------


def encode_cwd(path) -> str:
    return re.sub(r"[^a-zA-Z0-9]", "-", str(path))


def looks_like_path(value) -> bool:
    """True if this is a filesystem path rather than an already-encoded dir name.

    Encoded names are alphanumerics and dashes only, so any separator gives a
    path away. Testing for "/" alone missed native Windows paths — C:\\Users\\me
    has none — and sent them down the substring-match branch, where they matched
    nothing at all. The drive-letter test is deliberately not gated on the host
    platform: transcripts get copied between machines, so a Windows path may
    well be handed to a POSIX box.
    """
    text = str(value)
    seps = {"/", os.sep, os.altsep} - {None}
    return any(sep in text for sep in seps) or bool(re.match(r"^[a-zA-Z]:", text))


def all_session_files(project=None):
    """Yield .jsonl transcript paths, optionally restricted to one project dir."""
    if not PROJECTS_DIR.is_dir():
        return
    dirs = sorted(d for d in PROJECTS_DIR.iterdir() if d.is_dir())
    if project:
        wanted = encode_cwd(project) if (looks_like_path(project) or Path(project).exists()) else str(project)
        dirs = [d for d in dirs if d.name == wanted or wanted in d.name]
    for d in dirs:
        yield from sorted(d.glob("*.jsonl"))


class Meta:
    """Cheap summary of one transcript file."""

    __slots__ = (
        "path", "session_id", "cwd", "branch", "title", "first_prompt",
        "start", "end", "n_user", "n_assistant", "n_tools", "models", "versions", "tools",
        "rewound", "compacted",
    )

    def __init__(self, path):
        self.path = path
        self.session_id = path.stem
        self.cwd = None
        self.branch = None
        self.title = None
        self.first_prompt = None
        self.start = None
        self.end = None
        self.n_user = 0
        self.n_assistant = 0
        self.n_tools = 0
        self.models = set()
        self.versions = set()
        self.tools = {}
        self.rewound = False
        self.compacted = False


def read_meta(path: Path) -> Meta:
    m = Meta(path)
    last_assistant_id = None
    prompt_parents = {}  # parent uuid -> real user prompts hanging off it
    for rec in iter_records(path):
        rtype = rec.get("type")
        # Same strict rule as mark_abandoned: two real prompts under one parent.
        # Surfaced here so `list` can flag it during triage, before any turn is read.
        if is_real_prompt(rec):
            parent = chain_parent(rec)
            if parent:
                prompt_parents[parent] = prompt_parents.get(parent, 0) + 1
                if prompt_parents[parent] > 1:
                    m.rewound = True
        if rtype == "system" and rec.get("subtype") == "compact_boundary":
            m.compacted = True
        if rec.get("sessionId"):
            m.session_id = rec["sessionId"]
        if rec.get("cwd") and not m.cwd:
            m.cwd = rec["cwd"]
        if rec.get("gitBranch"):
            m.branch = rec["gitBranch"]
        if rtype == "ai-title" and rec.get("aiTitle"):
            m.title = rec["aiTitle"]
        if rec.get("version"):
            m.versions.add(rec["version"])
        ts = parse_ts(rec.get("timestamp"))
        if ts:
            if m.start is None or ts < m.start:
                m.start = ts
            if m.end is None or ts > m.end:
                m.end = ts

        msg = rec.get("message")
        if rtype == "user" and isinstance(msg, dict):
            content = msg.get("content")
            is_tool_result = isinstance(content, list) and any(
                isinstance(b, dict) and b.get("type") == "tool_result" for b in content
            )
            is_envelope = isinstance(content, str) and (
                slash_command(content) is not None or command_stdout(content) is not None
            )
            if not is_tool_result and not is_envelope and not rec.get("isMeta"):
                m.n_user += 1
                if m.first_prompt is None and isinstance(content, str):
                    text = strip_reminders(content).strip()
                    if text:
                        m.first_prompt = oneline(text, 160)
        elif rtype == "assistant" and isinstance(msg, dict):
            # one API response is split across several records sharing message.id
            if msg.get("id") != last_assistant_id:
                m.n_assistant += 1
                last_assistant_id = msg.get("id")
            if msg.get("model"):
                m.models.add(msg["model"])
            for block in msg.get("content") or []:
                if isinstance(block, dict) and block.get("type") == "tool_use":
                    m.n_tools += 1
                    name = block.get("name", "?")
                    m.tools[name] = m.tools.get(name, 0) + 1
    if m.end is None:
        try:
            m.end = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
        except OSError:
            pass
    return m


def resolve_session(ref: str, project=None) -> Path:
    """Resolve a session reference: path, full/partial id, or 'latest'."""
    p = Path(ref).expanduser()
    if p.is_file():
        return p

    candidates = list(all_session_files(project))
    if not candidates:
        die("no transcripts found under " + str(PROJECTS_DIR))

    if ref == "latest":
        return max(candidates, key=lambda f: f.stat().st_mtime)

    exact = [f for f in candidates if f.stem == ref]
    if exact:
        return exact[0]
    partial = [f for f in candidates if f.stem.startswith(ref)]
    if len(partial) == 1:
        return partial[0]
    if len(partial) > 1:
        die(
            f"ambiguous session '{ref}' matches {len(partial)}:\n  "
            + "\n  ".join(f.stem for f in partial[:10])
        )
    die(f"no session matching '{ref}' (try: transcript.py list --all)")


def die(msg):
    print(f"error: {msg}", file=sys.stderr)
    sys.exit(1)


# --------------------------------------------------------------------------
# tool rendering
# --------------------------------------------------------------------------


def render_tool_input(name, params, full):
    """Render a tool call's input compactly, one tool family at a time."""
    limit = 0 if full else 25
    p = params if isinstance(params, dict) else {}

    if name == "Bash":
        head = f"Bash: {p.get('description', '')}".rstrip(": ").rstrip()
        body = "$ " + str(p.get("command", "")).replace("\n", "\n  ")
        if p.get("run_in_background"):
            head += "  [background]"
        return head, clip(body, limit)
    if name in ("Read", "NotebookEdit"):
        extra = ""
        if p.get("offset") or p.get("limit"):
            extra = f" (offset={p.get('offset')}, limit={p.get('limit')})"
        return f"{name}: {p.get('file_path', p.get('notebook_path', '?'))}{extra}", ""
    if name == "Write":
        return f"Write: {p.get('file_path', '?')}", clip(p.get("content", ""), limit)
    if name == "Edit":
        old = clip(p.get("old_string", ""), 0 if full else 12, indent="     - ")
        new = clip(p.get("new_string", ""), 0 if full else 12, indent="     + ")
        tag = " (replace_all)" if p.get("replace_all") else ""
        return f"Edit: {p.get('file_path', '?')}{tag}", (old + "\n" + new).strip("\n")
    if name in ("Grep", "Glob"):
        bits = [f"pattern={p.get('pattern', '')!r}"]
        for k in ("path", "glob", "type", "output_mode", "-i", "-n", "head_limit"):
            if p.get(k) not in (None, False, ""):
                bits.append(f"{k}={p[k]!r}")
        return f"{name}: " + ", ".join(bits), ""
    if name in ("Task", "Agent"):
        head = f"Agent[{p.get('subagent_type', 'general')}]: {p.get('description', '')}"
        return head, clip(p.get("prompt", ""), 0 if full else 15)
    if name == "TodoWrite":
        todos = p.get("todos") or []
        lines = [f"[{t.get('status', '?')[:4]}] {t.get('content', '')}" for t in todos if isinstance(t, dict)]
        return "TodoWrite", clip("\n".join(lines), 0 if full else 20)
    if name in ("WebFetch", "WebSearch"):
        return f"{name}: {p.get('url') or p.get('query', '')}", clip(p.get("prompt", ""), 0 if full else 6)
    if name == "Skill":
        return f"Skill: /{p.get('skill', '?')} {p.get('args', '')}".rstrip(), ""
    if name == "AskUserQuestion":
        rows = []
        for q in p.get("questions") or []:
            if not isinstance(q, dict):
                continue
            rows.append(f"Q: {q.get('question', '')}")
            labels = [o.get("label", "") for o in q.get("options") or [] if isinstance(o, dict)]
            if labels:
                rows.append("   options: " + " | ".join(labels))
        return "AskUserQuestion", clip("\n".join(rows), 0 if full else 20)
    if name == "ExitPlanMode":
        return "ExitPlanMode", clip(p.get("plan", ""), 0 if full else 20)
    if name in ("TaskCreate", "TaskUpdate"):
        bits = {k: v for k, v in p.items() if k in ("task_id", "content", "status", "activeForm", "description")}
        return f"{name}: " + oneline(json.dumps(bits, ensure_ascii=False), 160), ""

    dumped = json.dumps(p, indent=1, ensure_ascii=False, default=str) if p else ""
    return name, clip(dumped, 0 if full else 15)


def render_tool_result(result, block, full, max_lines):
    """Render a tool result from `toolUseResult` (rich) or the raw block (fallback)."""
    limit = 0 if full else max_lines

    if isinstance(result, dict):
        # AskUserQuestion - the chosen answer is the whole point, never clip it
        if "answers" in result and isinstance(result["answers"], dict):
            rows = [f"CHOSE: {oneline(q, 70)} -> {a}" for q, a in result["answers"].items()]
            for note in (result.get("annotations") or {}).values():
                if isinstance(note, dict) and note.get("notes"):
                    rows.append(f"  note: {note['notes']}")
            return clip("\n".join(rows), 0)
        # Bash-family
        if "stdout" in result or "stderr" in result:
            parts = []
            if result.get("interrupted"):
                parts.append("     [interrupted]")
            if result.get("backgroundTaskId"):
                parts.append(f"     [background task {result['backgroundTaskId']}]")
            out = clip(result.get("stdout", ""), limit)
            err = clip(result.get("stderr", ""), 0 if full else min(limit, 15))
            if out:
                parts.append(out)
            if err:
                parts.append("     [stderr]\n" + err)
            if not out and not err and not parts:
                parts.append("     (no output)")
            return "\n".join(parts)
        # Read
        if result.get("type") == "text" and isinstance(result.get("file"), dict):
            f = result["file"]
            head = f"     ({f.get('numLines', '?')} of {f.get('totalLines', '?')} lines from {f.get('filePath', '?')})"
            # limit == 0 means "no limit" everywhere else (see clip); keep it consistent
            # here rather than silently dropping file content.
            body = clip(f.get("content", ""), limit)
            return head + ("\n" + body if body else "")
        # Edit / Write
        if "structuredPatch" in result:
            path = result.get("filePath", "?")
            lines = []
            for hunk in result.get("structuredPatch") or []:
                lines.append(f"@@ -{hunk.get('oldStart')},{hunk.get('oldLines')} +{hunk.get('newStart')},{hunk.get('newLines')} @@")
                lines.extend(hunk.get("lines") or [])
            if not lines:
                return f"     (wrote {path})"
            return f"     (patched {path})\n" + clip("\n".join(lines), limit)
        if result.get("type") == "create":
            return f"     (created {result.get('filePath', '?')})"
        dumped = json.dumps(result, ensure_ascii=False, default=str)
        return clip(dumped, limit)

    if isinstance(result, str):
        return clip(result, limit)

    # fall back to the tool_result content block
    content = block.get("content") if isinstance(block, dict) else None
    if isinstance(content, str):
        return clip(content, limit)
    if isinstance(content, list):
        chunks = []
        for c in content:
            if not isinstance(c, dict):
                continue
            if c.get("type") == "text":
                chunks.append(c.get("text", ""))
            elif c.get("type") == "image":
                chunks.append("[image]")
            elif c.get("type") == "tool_reference":
                chunks.append(f"[tool loaded: {c.get('tool_name')}]")
            else:
                chunks.append(f"[{c.get('type')}]")
        return clip("\n".join(chunks), limit)
    return ""


# --------------------------------------------------------------------------
# turn construction
# --------------------------------------------------------------------------


class Turn:
    __slots__ = (
        "idx", "kind", "ts", "uuid", "parent", "sidechain", "meta", "payload",
        "abandoned", "blocks", "msg_id", "uuids",
    )

    def __init__(self, idx, kind, rec):
        self.idx = idx
        self.kind = kind  # user | assistant | command | attachment | system | error
        self.ts = parse_ts(rec.get("timestamp"))
        self.uuid = rec.get("uuid")
        self.parent = rec.get("parentUuid")
        self.sidechain = bool(rec.get("isSidechain"))
        self.meta = bool(rec.get("isMeta"))
        self.payload = rec
        self.abandoned = False
        self.uuids = {rec["uuid"]} if rec.get("uuid") else set()
        msg = rec.get("message")
        self.msg_id = msg.get("id") if isinstance(msg, dict) else None
        # Assistant responses are written one content block per JSONL record,
        # all sharing message.id; collect them so a turn is a whole response.
        self.blocks = list(msg.get("content") or []) if kind == "assistant" and isinstance(msg, dict) else []

    def absorb(self, rec):
        """Merge another record of the same assistant response into this turn."""
        msg = rec.get("message") or {}
        self.blocks.extend(msg.get("content") or [])
        if rec.get("uuid"):
            self.uuids.add(rec["uuid"])
            self.uuid = rec["uuid"]  # keep the latest uuid as the turn's chain anchor


def open_assistant_turn(turns):
    """The assistant turn a following record can still merge into, or None.

    Harness bookkeeping can land between two content blocks of one response -
    a `read_truncation_notice` attachment, or an injected `isMeta` user record
    such as an image-rescaling notice. Those are not conversational boundaries,
    so the response has to merge across them or its tool calls get stranded from
    the text that introduced them. A real user prompt *is* a boundary and stops
    the search, so nothing merges across actual conversation.
    """
    for t in reversed(turns):
        if t.kind == "assistant":
            return t
        if t.kind == "attachment" or (t.kind == "user" and t.meta):
            continue
        return None
    return None


def build_turns(records):
    """Convert raw records into an ordered turn list, folding tool results into calls."""
    turns = []
    pending_results = {}  # tool_use_id -> (toolUseResult, block)
    idx = 0

    # First pass: collect tool results so they can be attached to their calls.
    for rec in records:
        msg = rec.get("message")
        if rec.get("type") == "user" and isinstance(msg, dict) and isinstance(msg.get("content"), list):
            for block in msg["content"]:
                if isinstance(block, dict) and block.get("type") == "tool_result":
                    pending_results[block.get("tool_use_id")] = (rec.get("toolUseResult"), block)

    for rec in records:
        rtype = rec.get("type")
        if rtype in NOISE_TYPES:
            continue

        msg = rec.get("message")

        if rtype == "user" and isinstance(msg, dict):
            content = msg.get("content")
            if isinstance(content, list) and any(
                isinstance(b, dict) and b.get("type") == "tool_result" for b in content
            ):
                continue  # folded into the assistant turn that called the tool
            idx += 1
            turns.append(Turn(idx, "user", rec))
        elif rtype == "assistant" and isinstance(msg, dict):
            prev = open_assistant_turn(turns)
            if (
                prev is not None
                and prev.kind == "assistant"
                and prev.msg_id
                and prev.msg_id == msg.get("id")
                and prev.sidechain == bool(rec.get("isSidechain"))
            ):
                prev.absorb(rec)
                continue
            idx += 1
            turns.append(Turn(idx, "assistant", rec))
        elif rtype == "system":
            sub = rec.get("subtype")
            if sub == "local_command":
                idx += 1
                turns.append(Turn(idx, "command", rec))
            elif sub in ("api_error", "away_summary", "compact_boundary"):
                idx += 1
                turns.append(Turn(idx, "system", rec))
        elif rtype == "attachment":
            atype = (rec.get("attachment") or {}).get("type")
            if atype in INTERESTING_ATTACHMENTS:
                idx += 1
                turns.append(Turn(idx, "attachment", rec))

    mark_abandoned(records, turns)
    return turns, pending_results


def is_real_prompt(rec):
    """True for an actual user prompt, as opposed to a tool result or injected context."""
    if rec.get("type") != "user" or rec.get("isMeta"):
        return False
    content = (rec.get("message") or {}).get("content")
    if isinstance(content, list) and any(
        isinstance(b, dict) and b.get("type") == "tool_result" for b in content
    ):
        return False
    return True


def chain_parent(rec):
    """The record's parent in the conversation tree.

    A `compact_boundary` resets `parentUuid` to null and records the
    pre-compaction record in `logicalParentUuid`. Following that keeps the chain
    connected across a compaction, so a rewind before the boundary is still
    measured against a live branch that reaches it.
    """
    return rec.get("parentUuid") or rec.get("logicalParentUuid")


def mark_abandoned(records, turns):
    """Flag turns that sit on a rewound/abandoned conversation branch.

    A parent with several children is normal, not a fork: an assistant record's
    next content block and the tool_result answering its tool call both hang off
    the same parent. A real rewind is narrower - two actual user prompts sharing
    one parent - so that is the only signal used here.
    """
    by_uuid = {r["uuid"]: r for r in records if r.get("uuid")}
    children = {}
    for r in records:
        parent = chain_parent(r)
        if r.get("uuid") and parent:
            children.setdefault(parent, []).append(r["uuid"])

    forks = [
        kids for parent, kids in children.items()
        if sum(1 for k in kids if is_real_prompt(by_uuid.get(k, {}))) > 1
    ]
    if not forks:
        return

    # The live branch is the one the final record descends from.
    leaf = next((r["uuid"] for r in reversed(records) if r.get("uuid")), None)
    live = set()
    cur = leaf
    while cur and cur in by_uuid and cur not in live:
        live.add(cur)
        cur = chain_parent(by_uuid[cur])

    # Any subtree hanging off a fork that the live chain never enters is dead.
    dead = set()
    stack = [k for kids in forks for k in kids if k not in live]
    while stack:
        node = stack.pop()
        if node in dead:
            continue
        dead.add(node)
        stack.extend(children.get(node, ()))

    for t in turns:
        if t.uuids and t.uuids <= dead:
            t.abandoned = True


def has_branches(turns):
    return any(t.abandoned for t in turns)


# --------------------------------------------------------------------------
# rendering
# --------------------------------------------------------------------------


def turn_text(turn, results, opts):
    """Render one turn as a text block. Returns '' if it should be skipped."""
    rec = turn.payload
    msg = rec.get("message") or {}
    lines = []
    tag = " [abandoned branch]" if turn.abandoned else ""
    if turn.sidechain:
        tag += " [subagent]"

    if turn.kind == "user":
        content = msg.get("content")
        if isinstance(content, list):
            text = "\n".join(
                b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"
            )
            if any(isinstance(b, dict) and b.get("type") == "image" for b in content):
                text += "\n[image attached]"
        else:
            text = content or ""
        cmd = slash_command(text)
        if cmd:
            lines.append(f"## [{turn.idx}] USER (slash command) · {fmt_ts(turn.ts)}{tag}")
            lines.append(f"  /{cmd.lstrip('/')}")
            return "\n".join(lines)
        out = command_stdout(text)
        if out is not None:
            lines.append(f"## [{turn.idx}] COMMAND OUTPUT · {fmt_ts(turn.ts)}{tag}")
            lines.append(clip(out, 0 if opts.full else 10, indent="  "))
            return "\n".join(lines)
        text = strip_reminders(text, opts.full)
        if turn.meta:
            if not opts.meta:
                return ""
            lines.append(f"## [{turn.idx}] SYSTEM-INJECTED · {fmt_ts(turn.ts)}{tag}")
            lines.append(clip(text, 0 if opts.full else 10, indent="  "))
            return "\n".join(lines)
        origin = (rec.get("origin") or {}).get("kind")
        who = "USER" if origin in (None, "human") else f"USER ({origin})"
        lines.append(f"## [{turn.idx}] {who} · {fmt_ts(turn.ts)}{tag}")
        lines.append(clip(text, 0 if opts.full else opts.max_lines * 4, indent="  "))
        return "\n".join(lines).rstrip()

    if turn.kind == "assistant":
        model = msg.get("model", "?")
        header = f"## [{turn.idx}] ASSISTANT · {fmt_ts(turn.ts)} · {model}{tag}"
        body = []
        for block in turn.blocks:
            if not isinstance(block, dict):
                continue
            btype = block.get("type")
            if btype == "text" and block.get("text", "").strip():
                body.append(clip(block["text"], 0 if opts.full else opts.max_lines * 4, indent="  "))
            elif btype == "thinking":
                think = (block.get("thinking") or "").strip()
                if not think:
                    body.append("  [thinking: not stored]")
                    continue
                if opts.thinking:
                    body.append("  [thinking]\n" + clip(think, 0 if opts.full else opts.max_lines * 2, indent="  | "))
                else:
                    body.append(f"  [thinking: {len(think)} chars, use --thinking]")
            elif btype == "tool_use":
                name = block.get("name", "?")
                head, detail = render_tool_input(name, block.get("input"), opts.full)
                body.append(f"  ▶ {head}")
                if detail:
                    body.append(detail)
                res = results.get(block.get("id"))
                if res is not None:
                    rendered = render_tool_result(res[0], res[1], opts.full, opts.max_lines)
                    if rendered.strip():
                        body.append("  ⤷ result:")
                        body.append(rendered)
                    else:
                        body.append("  ⤷ (empty result)")
                else:
                    body.append("  ⤷ (no result recorded)")
        if not body:
            return ""
        return header + "\n" + "\n".join(body)

    if turn.kind == "command":
        raw = rec.get("content", "")
        cmd = slash_command(raw)
        if cmd:
            return f"## [{turn.idx}] LOCAL COMMAND · {fmt_ts(turn.ts)}{tag}\n  /{cmd.lstrip('/')}"
        # A local_command record is usually just a <local-command-stdout> envelope
        # with no <command-name>; render the payload, never the wrapper.
        out = command_stdout(raw)
        if out is not None:
            return (
                f"## [{turn.idx}] COMMAND OUTPUT · {fmt_ts(turn.ts)}{tag}\n"
                + clip(out, 0 if opts.full else 10, indent="  ")
            )
        return f"## [{turn.idx}] LOCAL COMMAND · {fmt_ts(turn.ts)}{tag}\n" + clip(
            raw, 0 if opts.full else 10, indent="  "
        )

    if turn.kind == "system":
        sub = rec.get("subtype")
        if sub == "compact_boundary":
            cm = rec.get("compactMetadata") or {}
            bits = []
            if cm.get("trigger"):
                bits.append(str(cm["trigger"]))
            if cm.get("preTokens") is not None or cm.get("postTokens") is not None:
                bits.append(f"{cm.get('preTokens', '?')} -> {cm.get('postTokens', '?')} tokens")
            detail = f" ({', '.join(bits)})" if bits else ""
            return (
                f"## [{turn.idx}] CONTEXT COMPACTED · {fmt_ts(turn.ts)}{detail}{tag}\n"
                "  turns above here were summarized out of the model's context"
            )
        text = rec.get("content") or rec.get("error") or json.dumps(
            {k: v for k, v in rec.items() if k not in ("uuid", "parentUuid", "sessionId")},
            ensure_ascii=False, default=str,
        )
        return f"## [{turn.idx}] SYSTEM/{sub} · {fmt_ts(turn.ts)}{tag}\n" + clip(text, 0 if opts.full else 10, indent="  ")

    if turn.kind == "attachment":
        att = rec.get("attachment") or {}
        atype = att.get("type")
        detail = json.dumps({k: v for k, v in att.items() if k != "type"}, ensure_ascii=False, default=str)
        return f"## [{turn.idx}] ATTACHMENT/{atype} · {fmt_ts(turn.ts)}{tag}\n" + clip(detail, 0 if opts.full else 8, indent="  ")

    return ""


def turn_summary(turn):
    """One-line summary of a turn, for outline mode."""
    rec = turn.payload
    msg = rec.get("message") or {}
    flag = "!" if turn.abandoned else " "

    if turn.kind == "user":
        content = msg.get("content")
        text = content if isinstance(content, str) else "\n".join(
            b.get("text", "") for b in content or [] if isinstance(b, dict) and b.get("type") == "text"
        )
        cmd = slash_command(text)
        if cmd:
            return f"{flag}[{turn.idx:>4}] {fmt_ts(turn.ts)} CMD    /{oneline(cmd, 90).lstrip('/')}"
        out = command_stdout(text)
        if out is not None:
            return f"{flag}[{turn.idx:>4}] {fmt_ts(turn.ts)} CMDOUT {oneline(out, 110)}"
        label = "META  " if turn.meta else "USER  "
        return f"{flag}[{turn.idx:>4}] {fmt_ts(turn.ts)} {label} {oneline(strip_reminders(text), 110)}"

    if turn.kind == "assistant":
        text_bits, tools, think = [], [], 0
        for block in turn.blocks:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "text" and block.get("text", "").strip():
                text_bits.append(block["text"])
            elif block.get("type") == "tool_use":
                tools.append(block.get("name", "?"))
            elif block.get("type") == "thinking" and (block.get("thinking") or "").strip():
                think += 1
        parts = []
        if think:
            parts.append("[think]")
        if text_bits:
            parts.append(oneline(" ".join(text_bits), 90))
        if tools:
            counts = {}
            for t in tools:
                counts[t] = counts.get(t, 0) + 1
            parts.append("{" + ", ".join(f"{k}x{v}" if v > 1 else k for k, v in counts.items()) + "}")
        return f"{flag}[{turn.idx:>4}] {fmt_ts(turn.ts)} ASST   " + " ".join(parts)

    if turn.kind == "command":
        raw = rec.get("content", "")
        cmd = slash_command(raw)
        if cmd:
            return f"{flag}[{turn.idx:>4}] {fmt_ts(turn.ts)} CMD    /{oneline(cmd, 90).lstrip('/')}"
        out = command_stdout(raw)
        if out is not None:
            return f"{flag}[{turn.idx:>4}] {fmt_ts(turn.ts)} CMDOUT {oneline(out, 110)}"
        return f"{flag}[{turn.idx:>4}] {fmt_ts(turn.ts)} CMD    {oneline(raw, 110)}"

    if turn.kind == "system" and rec.get("subtype") == "compact_boundary":
        cm = rec.get("compactMetadata") or {}
        return (
            f"{flag}[{turn.idx:>4}] {fmt_ts(turn.ts)} COMPCT context compacted "
            f"({cm.get('trigger', '?')}, {cm.get('preTokens', '?')} -> {cm.get('postTokens', '?')} tokens)"
        )

    return f"{flag}[{turn.idx:>4}] {fmt_ts(turn.ts)} {turn.kind.upper()[:6]:<6} {oneline(json.dumps(rec.get('attachment') or rec.get('content') or '', default=str), 90)}"


def session_header(meta, turns, opts):
    lines = []
    title = meta.title or meta.first_prompt or "(untitled)"
    lines.append(f"# Session {meta.session_id}")
    lines.append(f"Title:    {title}")
    lines.append(f"Project:  {meta.cwd or '?'}" + (f"   branch: {meta.branch}" if meta.branch else ""))
    if meta.start:
        dur = (meta.end - meta.start).total_seconds() if meta.end else None
        lines.append(
            f"When:     {fmt_ts(meta.start, True)} -> {fmt_ts(meta.end, True)}  ({fmt_duration(dur)})"
        )
    lines.append(
        f"Volume:   {meta.n_user} user / {meta.n_assistant} assistant msgs, "
        f"{meta.n_tools} tool calls, {len(turns)} turns"
    )
    if meta.models:
        lines.append(f"Models:   {', '.join(sorted(meta.models))}")
    top = sorted(meta.tools.items(), key=lambda kv: -kv[1])[:8]
    if top:
        lines.append("Tools:    " + ", ".join(f"{k}({v})" for k, v in top))
    lines.append(f"File:     {meta.path}")
    if has_branches(turns):
        n = sum(1 for t in turns if t.abandoned)
        lines.append(
            f"Note:     session was rewound; {n} turns are on abandoned branches "
            f"(marked '[abandoned branch]'; hide with --main-branch)"
        )
    if meta.compacted:
        lines.append(
            "Note:     session was compacted; turns above a 'CONTEXT COMPACTED' marker "
            "were summarized out of the model's context"
        )
    mode = []
    if not opts.thinking:
        mode.append("thinking hidden")
    if not opts.full:
        mode.append(f"tool output clipped to {opts.max_lines} lines")
    if mode:
        lines.append("Rendering:" + " " + "; ".join(mode))
    return "\n".join(lines)


def load_session(path, opts):
    records = list(iter_records(path))
    turns, results = build_turns(records)
    if getattr(opts, "main_branch", False):
        turns = [t for t in turns if not t.abandoned]
    if not getattr(opts, "sidechains", True):
        turns = [t for t in turns if not t.sidechain]
    return records, turns, results


def select_range(turns, opts):
    if getattr(opts, "range", None):
        m = re.match(r"^(\d+)?-(\d+)?$|^(\d+)$", opts.range)
        if not m:
            die("--range expects N, N-M, N- or -M")
        if m.group(3):
            lo = hi = int(m.group(3))
        else:
            lo = int(m.group(1)) if m.group(1) else 1
            hi = int(m.group(2)) if m.group(2) else 10**9
        turns = [t for t in turns if lo <= t.idx <= hi]
    if getattr(opts, "last", None):
        turns = turns[-opts.last:]
    if getattr(opts, "grep", None):
        pat = re.compile(opts.grep, re.I)
        keep = set()
        for i, t in enumerate(turns):
            if pat.search(turn_blob(t.payload)):
                for j in range(max(0, i - opts.context), min(len(turns), i + opts.context + 1)):
                    keep.add(j)
        turns = [t for i, t in enumerate(turns) if i in keep]
    return turns


# --------------------------------------------------------------------------
# commands
# --------------------------------------------------------------------------


def drop_current(files, opts):
    """Remove the calling session's own transcript. Returns (files, dropped)."""
    if getattr(opts, "include_current", False) or not CURRENT_SESSION_ID:
        return files, 0
    kept = [f for f in files if f.stem != CURRENT_SESSION_ID]
    return kept, len(files) - len(kept)


def note_dropped(dropped):
    if dropped:
        print(
            f"(hid the current session, {CURRENT_SESSION_ID[:8]}; --include-current shows it)",
            file=sys.stderr,
        )


def cmd_projects(opts):
    if not PROJECTS_DIR.is_dir():
        die(f"{PROJECTS_DIR} does not exist")
    rows = []
    for d in sorted(PROJECTS_DIR.iterdir()):
        if not d.is_dir():
            continue
        files = list(d.glob("*.jsonl"))
        if not files:
            continue
        newest = max(f.stat().st_mtime for f in files)
        cwd = None
        for rec in iter_records(max(files, key=lambda f: f.stat().st_mtime)):
            if rec.get("cwd"):
                cwd = rec["cwd"]
                break
        size = sum(f.stat().st_size for f in files)
        rows.append((newest, d.name, cwd or d.name, len(files), size))
    rows.sort(reverse=True)
    print(f"{'LAST ACTIVE':<17} {'SESSIONS':>8} {'SIZE':>8}  PROJECT")
    for newest, dirname, cwd, n, size in rows:
        when = datetime.fromtimestamp(newest).strftime("%Y-%m-%d %H:%M")
        print(f"{when:<17} {n:>8} {size // 1024:>7}K  {cwd}")
    print(f"\n{len(rows)} projects under {PROJECTS_DIR}")


def cmd_list(opts):
    project = None if opts.all else (opts.project or os.getcwd())
    files, dropped = drop_current(list(all_session_files(project)), opts)
    if not files and not opts.all:
        print(f"no transcripts for project {project}", file=sys.stderr)
        print("(try --all, or: transcript.py projects)", file=sys.stderr)
        return
    metas = [read_meta(f) for f in files]
    metas = [m for m in metas if m.n_user or m.n_assistant] if not opts.include_empty else metas

    if opts.since:
        cutoff = parse_since(opts.since)
        metas = [m for m in metas if m.end and m.end >= cutoff]
    if opts.grep:
        pat = re.compile(opts.grep, re.I)
        metas = [
            m for m in metas
            if pat.search(m.title or "") or pat.search(m.first_prompt or "") or pat.search(m.session_id)
        ]

    metas.sort(key=lambda m: m.end or datetime.fromtimestamp(0, tz=timezone.utc), reverse=True)
    if opts.limit:
        metas = metas[: opts.limit]

    if not metas:
        print("no matching sessions", file=sys.stderr)
        return

    for m in metas:
        when = fmt_ts(m.end, True)
        dur = fmt_duration((m.end - m.start).total_seconds()) if m.start and m.end else "?"
        # Flag the two things that change how the transcript should be read, so
        # they are visible during triage rather than only after opening a session.
        marks = ("  [rewound]" if m.rewound else "") + ("  [compacted]" if m.compacted else "")
        print(f"{m.session_id}  {when}  {dur:>7}  {m.n_user}u/{m.n_assistant}a/{m.n_tools}t{marks}")
        print(f"  title: {m.title or '(none)'}")
        if opts.all or opts.project is None:
            print(f"  cwd:   {m.cwd or '?'}")
        if m.first_prompt:
            print(f"  first: {m.first_prompt}")
        print()
    sys.stdout.flush()
    note_dropped(dropped)
    print(f"{len(metas)} sessions. Next: transcript.py outline <session-id>", file=sys.stderr)


def parse_since(text):
    text = text.strip().lower()
    m = re.match(r"^(\d+)\s*([dhwm])$", text)
    now = datetime.now(timezone.utc)
    if m:
        n = int(m.group(1))
        secs = {"h": 3600, "d": 86400, "w": 604800, "m": 2592000}[m.group(2)]
        return datetime.fromtimestamp(now.timestamp() - n * secs, tz=timezone.utc)
    dt = parse_ts(text) or parse_ts(text + "T00:00:00+00:00")
    if dt is None:
        die(f"cannot parse --since {text!r} (use 7d, 24h, 2w, or YYYY-MM-DD)")
    return dt


def cmd_outline(opts):
    path = resolve_session(opts.session, opts.project)
    meta = read_meta(path)
    _, turns, _ = load_session(path, opts)
    print(session_header(meta, turns, opts))
    print()
    for t in select_range(turns, opts):
        print(turn_summary(t))
    sys.stdout.flush()
    print(f"\n{len(turns)} turns. Next: transcript.py show {meta.session_id} --range 10-30", file=sys.stderr)


def cmd_show(opts):
    path = resolve_session(opts.session, opts.project)
    meta = read_meta(path)
    _, turns, results = load_session(path, opts)
    selected = select_range(turns, opts)

    if not opts.no_header:
        print(session_header(meta, turns, opts))
        if len(selected) != len(turns):
            print(f"Showing:  turns {selected[0].idx if selected else '-'}..{selected[-1].idx if selected else '-'} of {len(turns)}")
        print("\n" + "=" * 78 + "\n")

    for t in selected:
        text = turn_text(t, results, opts)
        if text:
            print(text)
            print()


def cmd_search(opts):
    pat = re.compile(opts.pattern, 0 if opts.case_sensitive else re.I)
    project = None if opts.all else (opts.project or os.getcwd())
    files, dropped = drop_current(list(all_session_files(project)), opts)
    if not files:
        die(f"no transcripts for {project} (try --all)")
    note_dropped(dropped)

    cutoff = parse_since(opts.since) if opts.since else None
    hits = 0
    sessions_hit = 0
    for path in sorted(files, key=lambda f: f.stat().st_mtime, reverse=True):
        if cutoff and datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc) < cutoff:
            continue
        try:
            _, turns, results = load_session(path, opts)
        except OSError:
            continue
        local = []
        for t in turns:
            if pat.search(turn_blob(t.payload)) or pat.search(turn_summary(t)):
                local.append(t)
                if len(local) >= opts.max_per_session:
                    break
        if not local:
            continue
        sessions_hit += 1
        meta = read_meta(path)
        # search is the usual entry point, so the triage flags have to appear here
        # too - a reader who goes straight from search to outline never sees `list`.
        marks = ("  [rewound]" if meta.rewound else "") + ("  [compacted]" if meta.compacted else "")
        print(f"\n=== {meta.session_id}  {fmt_ts(meta.end, True)}  {meta.cwd or ''}{marks}")
        print(f"    {meta.title or meta.first_prompt or ''}")
        for t in local:
            hits += 1
            if opts.render:
                print(turn_text(t, results, opts))
            else:
                print("  " + turn_summary(t))
                for line in matching_lines(t, pat, opts.context):
                    print("        " + line)
        if opts.limit and sessions_hit >= opts.limit:
            break
    sys.stdout.flush()
    print(
        f"\n{hits} matching turns in {sessions_hit} sessions. "
        f"Next: transcript.py show <id> --range <n>",
        file=sys.stderr,
    )


def content_strings(payload):
    """Every string in a record that is actual content, skipping identifiers.

    Searching the raw JSON matches base64 `thinking` signatures and uuids, which
    both produces false hits and prints unreadable blobs as if they were matching
    lines.
    """
    haystack = []

    def walk(node):
        if isinstance(node, str):
            haystack.append(node)
        elif isinstance(node, dict):
            for k, v in node.items():
                if k not in NON_CONTENT_KEYS:
                    walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(payload)
    return haystack


def turn_blob(payload):
    """One searchable string per turn, identifiers excluded."""
    return "\n".join(content_strings(payload))


def matching_lines(turn, pat, context):
    """Pull the actual matching text lines out of a turn payload."""
    out = []
    for chunk in content_strings(turn.payload):
        for line in chunk.split("\n"):
            if pat.search(line):
                out.append(oneline(line, 160))
                if len(out) >= max(1, context):
                    return out
    return out


# --------------------------------------------------------------------------
# cli
# --------------------------------------------------------------------------


def add_render_flags(p):
    p.add_argument("--full", action="store_true", help="no truncation anywhere (can be huge)")
    p.add_argument("--thinking", action="store_true", help="include assistant thinking blocks")
    p.add_argument("--meta", action="store_true", help="include system-injected user messages")
    p.add_argument("--max-lines", type=int, default=20, help="max lines per tool result (default 20)")
    p.add_argument("--main-branch", action="store_true", help="hide turns from rewound/abandoned branches")
    p.add_argument("--no-sidechains", dest="sidechains", action="store_false", help="hide subagent turns")
    p.add_argument("--project", help="project cwd or encoded dir name")


def add_select_flags(p):
    p.add_argument("--range", help="turn range: N, N-M, N-, -M")
    p.add_argument("--last", type=int, help="only the last N turns")
    p.add_argument("--grep", help="only turns matching this regex")
    p.add_argument("--context", "-C", type=int, default=2, help="turns of context around --grep hits")


def force_utf8_output():
    """Emit UTF-8 whatever the platform's default encoding is.

    Rendered output uses ▶, ⤷, ·, — and …. On Windows the default encoding is
    the ANSI code page (cp1252 on a Western install) whenever stdout is a pipe
    rather than a console — which is exactly how a tool harness reads it. Two
    different failures follow from that, and the quiet one is the worse of the
    pair: `show` dies outright on ▶ (U+25B6, absent from cp1252), while
    `outline` and `list` exit 0 having written bytes no UTF-8 reader can decode.

    Python 3.15 makes UTF-8 the default and this becomes a no-op. Until then it
    is the difference between working and not on every Windows host.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:  # already replaced, e.g. by a test harness
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):  # detached or otherwise not reconfigurable
            pass


def main():
    force_utf8_output()
    ap = argparse.ArgumentParser(
        prog="transcript.py",
        description="Read Claude Code session transcripts as readable text.",
    )
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("projects", help="list projects that have transcripts")
    p.set_defaults(func=cmd_projects)

    p = sub.add_parser("list", help="list sessions, newest first")
    p.add_argument("--all", action="store_true", help="all projects, not just the current cwd")
    p.add_argument("--project", help="project cwd or encoded dir name")
    p.add_argument("--limit", type=int, default=20)
    p.add_argument("--since", help="7d, 24h, 2w or YYYY-MM-DD")
    p.add_argument("--grep", help="filter on title / first prompt / id")
    p.add_argument("--include-empty", action="store_true")
    p.add_argument("--include-current", action="store_true",
                   help="include the session you are running in (hidden by default)")
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("outline", help="one line per turn - read this before show")
    p.add_argument("session", help="session id, id prefix, path, or 'latest'")
    add_render_flags(p)
    add_select_flags(p)
    p.set_defaults(func=cmd_outline)

    p = sub.add_parser("show", help="render a session or a slice of it")
    p.add_argument("session", help="session id, id prefix, path, or 'latest'")
    p.add_argument("--no-header", action="store_true")
    add_render_flags(p)
    add_select_flags(p)
    p.set_defaults(func=cmd_show)

    p = sub.add_parser("search", help="regex search across transcripts")
    p.add_argument("pattern")
    p.add_argument("--all", action="store_true", help="search every project")
    p.add_argument("--case-sensitive", action="store_true")
    p.add_argument("--since", help="7d, 24h, 2w or YYYY-MM-DD")
    p.add_argument("--limit", type=int, default=10, help="max sessions to report")
    p.add_argument("--max-per-session", type=int, default=5)
    p.add_argument("--render", action="store_true", help="render full turns instead of one-liners")
    p.add_argument("--include-current", action="store_true",
                   help="include the session you are running in (hidden by default)")
    add_render_flags(p)
    p.set_defaults(func=cmd_search, range=None, last=None, grep=None)

    opts = ap.parse_args()
    for name, default in (
        ("full", False), ("thinking", False), ("meta", False), ("max_lines", 20),
        ("main_branch", False), ("sidechains", True), ("project", None),
        ("range", None), ("last", None), ("grep", None), ("context", 2),
        ("include_current", False),
    ):
        if not hasattr(opts, name):
            setattr(opts, name, default)
    try:
        opts.func(opts)
    except BrokenPipeError:
        try:
            sys.stdout.close()
        except Exception:
            pass
    except KeyboardInterrupt:
        sys.exit(130)


if __name__ == "__main__":
    main()
