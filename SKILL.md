---
name: session-vault
description: Search and recall past Claude Code sessions across all machines. Use /recall to find previous sessions by date, keyword, or semantic meaning.
license: MIT
metadata:
  author: ljniox
  version: "1.0.0"
---

# Session Vault

Automatically exports every Claude Code session as clean, searchable Markdown to a shared vault (Google Drive via rclone). Sessions are partitioned by hostname and indexed for fast recall.

## /recall — Search past sessions

When the user invokes `/recall`, run the recall search engine and present results.

### Usage patterns

```
/recall yesterday              → all sessions from yesterday, all machines
/recall today                  → today's sessions
/recall last week              → sessions from last week
/recall last week deployment   → temporal + keyword filter
/recall 3d                     → last 3 days
/recall 2026-02-17             → specific date
/recall rclone                 → keyword search (QMD BM25)
/recall --semantic "ideas I never acted on"  → semantic search
```

### How to execute

Run the recall script and present the results to the user:

```bash
python3 ~/.claude/skills/session-vault/scripts/recall.py <query>
```

For semantic search:
```bash
python3 ~/.claude/skills/session-vault/scripts/recall.py --semantic "<query>"
```

Options:
- `--limit N` — max results (default: 10)
- `--json` — raw JSON output (for programmatic use)

### Presenting results

1. Run the recall script with the user's query
2. Present results in a clean, scannable format:
   - Date and session ID
   - Machine hostname
   - Project context
   - First user message (summary)
3. If the user wants to read a specific session, read the `.md` file from the vault path shown in results

### Reading a session

When the user asks to see details of a recalled session, read the markdown file:

```bash
cat ~/.session-vault/vault/{hostname}/sessions/{date}/{session_id}.md
```

## Architecture

```
Session ends → export-session.sh hook fires
  → jsonl-to-md.py converts JSONL → clean Markdown (with secret scrubbing)
  → writes to ~/.session-vault/vault/{hostname}/sessions/{date}/{id}.md
  → build-index.py appends to vault/{hostname}/index.jsonl
  → rclone cron syncs ~/.session-vault/ ↔ jss:{remote} (every 15 min)

/recall → recall.py searches via QMD BM25/semantic + index.jsonl temporal fallback
```

## Secret scrubbing

All exported sessions pass through scrub-patterns.py which redacts:
- API keys (sk-*, gsk_*, ghp_*, gho_*)
- Bearer tokens and Authorization headers
- AWS access keys and secrets
- JWT tokens
- Connection strings (postgres://, mongodb://, etc.)
- SSH/PGP private keys
- Stripe, Slack, SendGrid, Twilio tokens
- Passwords in URLs
- Generic env var secrets

Never store raw session files. Always export through the converter.

## Manual operations

### Force export current session
```bash
~/.claude/skills/session-vault/scripts/export-session.sh
```

### Bulk export all existing sessions
```bash
bash ~/.claude/skills/session-vault/setup.sh --bulk-export
```

### Manual sync
```bash
rclone bisync ~/.session-vault/vault/ "jss:DIRECTEUR TECHNIQUE/AI CONTINUOUS S DELIVERY/DEV STACK/session-vault/vault/" --create-empty-src-dirs --resilient
```

### Re-index QMD
```bash
qmd index sessions
```

## Vault structure

```
~/.session-vault/vault/
├── MacBook-Pro-de-James/
│   ├── sessions/2026-03-04/
│   │   ├── 3234d52c-....md
│   │   └── fe7d9d4d-....md
│   └── index.jsonl
├── server-prod-01/
│   └── ...
└── mac-mini/
    └── ...
```

## Setup on a new machine

```bash
bash /path/to/session-vault/setup.sh
```

This installs the hook, creates the vault, sets up cron sync, and pulls existing sessions from other machines.
