#!/usr/bin/env bash
# Session Vault — One-command setup for any new machine
#
# Usage:
#   bash setup.sh                    # interactive setup
#   bash setup.sh --bulk-export      # also export all existing sessions
#
# What it does:
#   1. Verify rclone + jss: remote
#   2. Create local cache at ~/.session-vault/
#   3. Install QMD if not present
#   4. Configure QMD collection for the vault
#   5. Write config.env
#   6. Symlink skill → ~/.claude/skills/session-vault
#   7. Register SessionEnd hook in ~/.claude/settings.json
#   8. chmod +x all scripts
#   9. Add rclone sync cron (every 15 min)
#  10. Add QMD re-index cron (every 30 min)
#  11. Initial pull from Google Drive
#  12. Optional: bulk export existing sessions

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VAULT_LOCAL="$HOME/.session-vault"
HOSTNAME_SHORT="$(hostname -s)"
CLAUDE_DIR="$HOME/.claude"
SETTINGS_FILE="${CLAUDE_DIR}/settings.json"
SKILLS_DIR="${CLAUDE_DIR}/skills"
RCLONE_REMOTE="jss:DIRECTEUR TECHNIQUE/AI CONTINUOUS S DELIVERY/DEV STACK/session-vault/vault/"
BULK_EXPORT=false

# Parse args
for arg in "$@"; do
    case "$arg" in
        --bulk-export) BULK_EXPORT=true ;;
    esac
done

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
BLUE='\033[0;34m'
NC='\033[0m'

info() { echo -e "${BLUE}[session-vault]${NC} $*"; }
ok()   { echo -e "${GREEN}[session-vault]${NC} $*"; }
warn() { echo -e "${YELLOW}[session-vault]${NC} $*"; }
err()  { echo -e "${RED}[session-vault]${NC} $*" >&2; }

# --- Step 1: Verify rclone ---

info "Step 1/12: Checking rclone..."
if ! command -v rclone &>/dev/null; then
    err "rclone not found. Install it: https://rclone.org/install/"
    exit 1
fi

if ! rclone listremotes 2>/dev/null | grep -q '^jss:$'; then
    err "rclone remote 'jss:' not configured."
    err "Run: rclone config"
    exit 1
fi
ok "rclone found with jss: remote"

# --- Step 2: Create local cache ---

info "Step 2/12: Creating local cache at ${VAULT_LOCAL}..."
mkdir -p "${VAULT_LOCAL}/vault/${HOSTNAME_SHORT}/sessions"
ok "Local cache ready: ${VAULT_LOCAL}"

# --- Step 3: Install QMD ---

info "Step 3/12: Checking QMD..."
if command -v qmd &>/dev/null; then
    ok "QMD already installed: $(qmd --version 2>/dev/null || echo 'unknown version')"
else
    info "Installing QMD..."
    if command -v npm &>/dev/null; then
        npm install -g @tobilu/qmd
    elif command -v bun &>/dev/null; then
        bun install -g @tobilu/qmd
    else
        warn "Neither npm nor bun found. QMD will not be installed."
        warn "Install Node.js >= 22 or Bun >= 1.0, then run: npm install -g @tobilu/qmd"
    fi

    if command -v qmd &>/dev/null; then
        ok "QMD installed: $(qmd --version 2>/dev/null || echo 'ok')"
    else
        warn "QMD installation failed. /recall will fall back to index-only search."
    fi
fi

# --- Step 4: Configure QMD collection ---

info "Step 4/12: Configuring QMD collection..."
if command -v qmd &>/dev/null; then
    # Add collection pointing at the vault
    qmd add sessions "${VAULT_LOCAL}/vault" --glob "**/*.md" 2>/dev/null || true

    # Initial indexing
    info "Running initial QMD index (this may take a moment)..."
    qmd index sessions 2>/dev/null || warn "QMD indexing skipped (vault may be empty)"
    ok "QMD collection 'sessions' configured"
else
    warn "QMD not available — skipping collection setup"
fi

# --- Step 5: Write config.env ---

info "Step 5/12: Writing config.env..."
CONFIG_FILE="${SCRIPT_DIR}/config.env"
if [[ -f "$CONFIG_FILE" ]]; then
    warn "config.env already exists — preserving existing config"
else
    cat > "$CONFIG_FILE" << ENVEOF
# Session Vault — Machine: ${HOSTNAME_SHORT}
# Generated: $(date -Iseconds)

VAULT_LOCAL="${VAULT_LOCAL}"
VAULT_HOSTNAME="${HOSTNAME_SHORT}"
RCLONE_REMOTE="${RCLONE_REMOTE}"
CLAUDE_PROJECTS_DIR="\$HOME/.claude/projects"
CLAUDE_HISTORY="\$HOME/.claude/history.jsonl"
ENVEOF
    ok "config.env created"
fi

# --- Step 6: Symlink skill ---

info "Step 6/12: Creating skill symlink..."
mkdir -p "$SKILLS_DIR"
SYMLINK_TARGET="${SKILLS_DIR}/session-vault"

if [[ -L "$SYMLINK_TARGET" ]]; then
    # Remove existing symlink
    rm "$SYMLINK_TARGET"
fi

if [[ -d "$SYMLINK_TARGET" ]]; then
    warn "Non-symlink directory exists at ${SYMLINK_TARGET} — skipping"
else
    ln -s "$SCRIPT_DIR" "$SYMLINK_TARGET"
    ok "Symlinked: ${SYMLINK_TARGET} → ${SCRIPT_DIR}"
fi

# --- Step 7: Register SessionEnd hook ---

info "Step 7/12: Registering SessionEnd hook..."

EXPORT_SCRIPT="${SCRIPT_DIR}/scripts/export-session.sh"

# Use Python for safe JSON merging
python3 << PYEOF
import json
import os

settings_file = "${SETTINGS_FILE}"

# Read existing settings
settings = {}
if os.path.exists(settings_file):
    with open(settings_file, 'r') as f:
        settings = json.load(f)

# Ensure hooks structure exists
if 'hooks' not in settings:
    settings['hooks'] = {}

# Build the hook entry
hook_entry = {
    "matcher": "",
    "hooks": [{
        "type": "command",
        "command": "${EXPORT_SCRIPT}",
        "timeout": 120
    }]
}

# Check if SessionEnd already has our hook
session_end_hooks = settings['hooks'].get('SessionEnd', [])
already_registered = False
for existing in session_end_hooks:
    for h in existing.get('hooks', []):
        if 'export-session.sh' in h.get('command', ''):
            already_registered = True
            # Update the command path in case it changed
            h['command'] = "${EXPORT_SCRIPT}"
            break
    if already_registered:
        break

if not already_registered:
    session_end_hooks.append(hook_entry)

settings['hooks']['SessionEnd'] = session_end_hooks

# Write back
with open(settings_file, 'w') as f:
    json.dump(settings, f, indent=2)
    f.write('\n')

print('done')
PYEOF

ok "SessionEnd hook registered in ${SETTINGS_FILE}"

# --- Step 8: chmod +x all scripts ---

info "Step 8/12: Making scripts executable..."
chmod +x "${SCRIPT_DIR}/scripts/"*.sh "${SCRIPT_DIR}/scripts/"*.py
chmod +x "${SCRIPT_DIR}/setup.sh"
ok "Scripts are executable"

# --- Step 9: Add rclone sync cron ---

info "Step 9/12: Setting up rclone sync cron (every 15 min)..."

CRON_TAG="# session-vault-rclone-sync"
RCLONE_CRON="*/15 * * * * rclone bisync \"${VAULT_LOCAL}/vault/\" \"${RCLONE_REMOTE}\" --create-empty-src-dirs --resilient 2>/dev/null ${CRON_TAG}"

# Get existing crontab (empty string if none)
EXISTING_CRON="$(crontab -l 2>/dev/null || true)"

# Check if already installed
if echo "$EXISTING_CRON" | grep -q "session-vault-rclone-sync"; then
    warn "rclone cron already installed — updating"
    EXISTING_CRON="$(echo "$EXISTING_CRON" | grep -v "session-vault-rclone-sync" || true)"
fi

# Add new entry
printf '%s\n%s\n' "$EXISTING_CRON" "$RCLONE_CRON" | crontab -
ok "rclone bisync cron installed (every 15 min)"

# --- Step 10: Add QMD re-index cron ---

info "Step 10/12: Setting up QMD re-index cron (every 30 min)..."

if command -v qmd &>/dev/null; then
    QMD_CRON_TAG="# session-vault-qmd-reindex"
    QMD_PATH="$(which qmd)"
    QMD_CRON="*/30 * * * * ${QMD_PATH} index sessions 2>/dev/null ${QMD_CRON_TAG}"

    EXISTING_CRON="$(crontab -l 2>/dev/null || true)"

    if echo "$EXISTING_CRON" | grep -q "session-vault-qmd-reindex"; then
        warn "QMD reindex cron already installed — updating"
        EXISTING_CRON="$(echo "$EXISTING_CRON" | grep -v "session-vault-qmd-reindex" || true)"
    fi

    printf '%s\n%s\n' "$EXISTING_CRON" "$QMD_CRON" | crontab -
    ok "QMD reindex cron installed (every 30 min)"
else
    warn "QMD not available — skipping reindex cron"
fi

# --- Step 11: Initial pull from Google Drive ---

info "Step 11/12: Pulling existing sessions from Google Drive..."
rclone copy "${RCLONE_REMOTE}" "${VAULT_LOCAL}/vault/" --create-empty-src-dirs 2>/dev/null || {
    warn "Initial pull failed (remote may be empty — this is fine for first machine)"
}
ok "Initial sync complete"

# --- Step 12: Optional bulk export ---

if [[ "$BULK_EXPORT" == "true" ]]; then
    info "Step 12/12: Bulk exporting existing sessions..."

    PROJECTS_DIR="$HOME/.claude/projects"
    EXPORT_COUNT=0

    if [[ -d "$PROJECTS_DIR" ]]; then
        # Find all JSONL files
        while IFS= read -r jsonl_file; do
            session_id="$(basename "$jsonl_file" .jsonl)"

            # Skip non-session filenames (accept UUID and agent-*)
            if [[ ! "$session_id" =~ ^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$ ]] && \
               [[ ! "$session_id" =~ ^agent-[a-f0-9]{7,} ]]; then
                continue
            fi

            # Get file modification date for directory structure
            if [[ "$(uname)" == "Darwin" ]]; then
                file_date=$(stat -f '%Sm' -t '%Y-%m-%d' "$jsonl_file")
            else
                file_date=$(date -r "$jsonl_file" '+%Y-%m-%d')
            fi

            output_dir="${VAULT_LOCAL}/vault/${HOSTNAME_SHORT}/sessions/${file_date}"
            output_file="${output_dir}/${session_id}.md"

            # Skip if already exported
            if [[ -f "$output_file" ]]; then
                continue
            fi

            mkdir -p "$output_dir"

            # Extract project from path
            parent_dir="$(basename "$(dirname "$jsonl_file")")"
            project="$(echo "$parent_dir" | sed 's/^-/\//;s/-/\//g')"

            # Convert
            meta_file=$(mktemp /tmp/sv-bulk-XXXXXX.json)
            python3 "${SCRIPT_DIR}/scripts/jsonl-to-md.py" \
                "$jsonl_file" "$output_file" \
                --session-id "$session_id" \
                --project "$project" \
                --json-meta > "$meta_file" 2>/dev/null || {
                    rm -f "$meta_file"
                    continue
                }

            # Index
            python3 "${SCRIPT_DIR}/scripts/build-index.py" \
                "$output_file" \
                --meta-file "$meta_file" \
                --index-path "${VAULT_LOCAL}/vault/${HOSTNAME_SHORT}/index.jsonl" \
                --hostname "$HOSTNAME_SHORT" 2>/dev/null || true

            rm -f "$meta_file"
            EXPORT_COUNT=$((EXPORT_COUNT + 1))

        done < <(find "$PROJECTS_DIR" -name "*.jsonl" -type f 2>/dev/null)
    fi

    ok "Bulk export: ${EXPORT_COUNT} sessions exported"

    # Re-index QMD after bulk export
    if command -v qmd &>/dev/null && [[ $EXPORT_COUNT -gt 0 ]]; then
        info "Re-indexing QMD after bulk export..."
        qmd index sessions 2>/dev/null || true
        ok "QMD re-indexed"
    fi
else
    info "Step 12/12: Skipping bulk export (pass --bulk-export to enable)"
fi

echo ""
ok "========================================="
ok " Session Vault installed successfully!"
ok "========================================="
echo ""
info "Machine:     ${HOSTNAME_SHORT}"
info "Local vault: ${VAULT_LOCAL}"
info "Remote:      ${RCLONE_REMOTE}"
info "Hook:        SessionEnd → export-session.sh"
info ""
info "Test it:"
info "  1. Start a Claude Code session, do something, exit"
info "  2. Check: ls ${VAULT_LOCAL}/vault/${HOSTNAME_SHORT}/sessions/"
info "  3. Use: /recall today"
echo ""
