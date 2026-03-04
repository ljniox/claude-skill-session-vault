# Session Vault

Persistent memory for Claude Code sessions across all machines. Every session is automatically exported as clean, searchable Markdown to a shared Google Drive vault.

## Problem

- Claude Code sessions start from zero every time
- 700+ sessions across multiple machines — context lost between sessions
- Grepping JSONL files doesn't scale

## Solution

A SessionEnd hook converts each session's JSONL transcript to scrubbed Markdown, writes it to a per-hostname vault, and syncs to Google Drive via rclone. A `/recall` skill searches across all machines.

## Quick Start

### Prerequisites

- **rclone** with `jss:` remote configured (`rclone config`)
- **Python 3.8+** (stdlib only, no pip dependencies)
- **Node.js >= 22** or **Bun >= 1.0** (for QMD, optional but recommended)

### Install

```bash
# From Google Drive (or wherever session-vault/ lives):
cd "DIRECTEUR TECHNIQUE/AI CONTINUOUS S DELIVERY/DEV STACK/session-vault"
bash setup.sh

# Include bulk export of all existing sessions:
bash setup.sh --bulk-export
```

### What setup.sh does

1. Verifies `rclone` and `jss:` remote
2. Creates local cache at `~/.session-vault/`
3. Installs QMD (`npm install -g @tobilu/qmd`) if not present
4. Configures QMD `sessions` collection pointing at the vault
5. Writes `config.env` with local paths and hostname
6. Symlinks `session-vault/` → `~/.claude/skills/session-vault`
7. Registers `SessionEnd` hook in `~/.claude/settings.json`
8. Makes all scripts executable
9. Adds rclone bisync cron (every 15 min)
10. Adds QMD re-index cron (every 30 min)
11. Pulls existing sessions from Google Drive
12. Optionally bulk-exports all local sessions

### Verify

```bash
# Check hook is registered
cat ~/.claude/settings.json | python3 -m json.tool | grep export-session

# Check cron
crontab -l | grep session-vault

# Check vault
ls ~/.session-vault/vault/$(hostname -s)/
```

## Usage

### Automatic export

Every time you exit a Claude Code session, the hook fires and:
1. Finds the session JSONL file
2. Converts it to Markdown (scrubbing secrets)
3. Writes to `~/.session-vault/vault/{hostname}/sessions/{date}/{id}.md`
4. Appends metadata to `index.jsonl`

### Recall past sessions

In any Claude Code session:

```
/recall yesterday               # all sessions from yesterday
/recall today                   # today's sessions
/recall last week deployment    # temporal + keyword filter
/recall 3d                      # last 3 days
/recall rclone                  # keyword search across all time
/recall 2026-02-17              # specific date
/recall --semantic "auth flow"  # semantic search via QMD
```

### Manual operations

```bash
# Force export current session
~/.claude/skills/session-vault/scripts/export-session.sh

# Manual sync
rclone bisync ~/.session-vault/vault/ "jss:DIRECTEUR TECHNIQUE/AI CONTINUOUS S DELIVERY/DEV STACK/session-vault/vault/" --create-empty-src-dirs --resilient

# Re-index QMD
qmd index sessions
```

## Architecture

```
~/.claude/projects/*/*.jsonl          # source: Claude Code session files
        │
        ▼
export-session.sh (SessionEnd hook)
        │
        ├── jsonl-to-md.py            # JSONL → Markdown (streaming)
        │       │
        │       └── scrub-patterns.py  # Secret redaction
        │
        └── build-index.py            # Append to index.jsonl
                │
                ▼
~/.session-vault/vault/{hostname}/    # local cache
        │
        ▼ (rclone bisync, every 15 min)
        │
jss:session-vault/vault/              # Google Drive (shared)
```

## File Reference

| File | Purpose |
|------|---------|
| `SKILL.md` | Claude Code skill definition + /recall docs |
| `README.md` | This file |
| `setup.sh` | One-command install |
| `config.env.example` | Config template |
| `scripts/jsonl-to-md.py` | JSONL → Markdown converter |
| `scripts/scrub-patterns.py` | Secret scrubbing patterns |
| `scripts/export-session.sh` | SessionEnd hook entry point |
| `scripts/build-index.py` | Index builder |
| `scripts/recall.py` | /recall search engine |

## Security

- All sessions pass through secret scrubbing before writing
- Patterns catch: API keys, tokens, AWS credentials, JWTs, connection strings, SSH keys, passwords
- Raw JSONL files are never synced — only scrubbed Markdown
- Google Drive access controlled by rclone `jss:` remote permissions

## Multi-machine

- Each machine writes only to `vault/{hostname}/`, eliminating write conflicts
- rclone bisync shares sessions between machines
- `/recall` searches across all hostnames
- Run `setup.sh` on each new machine to join the vault
