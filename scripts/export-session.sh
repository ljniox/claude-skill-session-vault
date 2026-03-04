#!/usr/bin/env bash
# Session Vault — SessionEnd hook entry point
#
# Called by Claude Code's SessionEnd hook. Finds the most recently modified
# JSONL session file, converts it to Markdown, and indexes it.
#
# Environment provided by Claude Code hooks:
#   SESSION_ID — current session UUID (if available)
#   CLAUDE_PROJECT_DIR — project directory (if available)
#
# This script is idempotent — re-running it on the same session is safe
# (the indexer skips duplicates).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SKILL_DIR="$(dirname "$SCRIPT_DIR")"

# Load config
CONFIG_FILE="${SKILL_DIR}/config.env"
if [[ ! -f "$CONFIG_FILE" ]]; then
    echo "[session-vault] ERROR: config.env not found at $CONFIG_FILE" >&2
    exit 1
fi
# shellcheck source=/dev/null
source "$CONFIG_FILE"

# Resolve hostname
HOSTNAME="${VAULT_HOSTNAME:-$(hostname -s)}"
VAULT_DIR="${VAULT_LOCAL}/vault/${HOSTNAME}"
INDEX_FILE="${VAULT_DIR}/index.jsonl"

# Claude projects directory
PROJECTS_DIR="${CLAUDE_PROJECTS_DIR:-$HOME/.claude/projects}"

# --- Find the session JSONL file ---

find_session_jsonl() {
    local session_id="${SESSION_ID:-}"

    # Strategy 1: SESSION_ID env var (set by Claude Code hook)
    if [[ -n "$session_id" ]]; then
        # Search all project dirs for this session ID
        local found
        found=$(find "$PROJECTS_DIR" -name "${session_id}.jsonl" -type f 2>/dev/null | head -1)
        if [[ -n "$found" ]]; then
            echo "$found"
            return 0
        fi
    fi

    # Strategy 2: Most recently modified JSONL in any project dir
    local latest
    latest=$(find "$PROJECTS_DIR" -name "*.jsonl" -type f -not -name ".*" \
        -newer "$PROJECTS_DIR" 2>/dev/null | \
        xargs ls -t 2>/dev/null | head -1)

    if [[ -n "$latest" ]]; then
        echo "$latest"
        return 0
    fi

    # Strategy 3: Absolute fallback — any JSONL modified in last 5 minutes
    latest=$(find "$PROJECTS_DIR" -name "*.jsonl" -type f -mmin -5 2>/dev/null | \
        xargs ls -t 2>/dev/null | head -1)

    if [[ -n "$latest" ]]; then
        echo "$latest"
        return 0
    fi

    return 1
}

# --- Extract project path from the JSONL parent directory name ---

extract_project_from_path() {
    local jsonl_path="$1"
    local parent_dir
    parent_dir="$(basename "$(dirname "$jsonl_path")")"

    # The directory name is a munged path like:
    #   -Users-ljniox-dev-myproject
    # Convert back to a readable path
    echo "$parent_dir" | sed 's/^-/\//;s/-/\//g'
}

# --- Main ---

main() {
    echo "[session-vault] Starting export..." >&2

    # Find the session file
    local jsonl_file
    jsonl_file=$(find_session_jsonl) || {
        echo "[session-vault] No session JSONL found to export" >&2
        exit 0
    }

    echo "[session-vault] Found: $jsonl_file" >&2

    # Extract session ID from filename
    local session_id
    session_id="$(basename "$jsonl_file" .jsonl)"

    # Skip if it's not a session filename (UUID or agent-*)
    if [[ ! "$session_id" =~ ^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$ ]] && \
       [[ ! "$session_id" =~ ^agent-[a-f0-9]{7,} ]]; then
        echo "[session-vault] Skipping non-session file: $session_id" >&2
        exit 0
    fi

    # Determine date and output path
    local today
    today="$(date +%Y-%m-%d)"
    local output_dir="${VAULT_DIR}/sessions/${today}"
    local output_file="${output_dir}/${session_id}.md"

    # Skip if already exported
    if [[ -f "$output_file" ]]; then
        echo "[session-vault] Already exported: $output_file" >&2
        exit 0
    fi

    # Extract project context
    local project
    project="$(extract_project_from_path "$jsonl_file")"

    # Create output directory
    mkdir -p "$output_dir"

    # Convert JSONL → Markdown with metadata output
    local meta_file
    meta_file=$(mktemp /tmp/session-vault-meta.XXXXXX.json)

    python3 "${SCRIPT_DIR}/jsonl-to-md.py" \
        "$jsonl_file" \
        "$output_file" \
        --session-id "$session_id" \
        --project "$project" \
        --json-meta > "$meta_file"

    echo "[session-vault] Exported: $output_file" >&2

    # Index the session
    python3 "${SCRIPT_DIR}/build-index.py" \
        "$output_file" \
        --meta-file "$meta_file" \
        --index-path "$INDEX_FILE" \
        --hostname "$HOSTNAME"

    # Cleanup
    rm -f "$meta_file"

    echo "[session-vault] Done. Session $session_id exported and indexed." >&2
}

main "$@"
