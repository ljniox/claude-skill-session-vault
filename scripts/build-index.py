#!/usr/bin/env python3
"""Build/append session metadata to the per-hostname index.jsonl.

Each line in index.jsonl is a JSON object with:
  - session_id: UUID of the session
  - date: YYYY-MM-DD
  - start_time: ISO timestamp
  - end_time: ISO timestamp
  - project: project path
  - model: model used
  - message_count: number of messages
  - md_path: relative path to the .md file within the vault
  - first_user_message: first substantive user message (for summary)
  - source_file: original JSONL path
  - hostname: machine hostname

Usage:
    python3 build-index.py <session.md> --meta '{"session_id":"...","start_time":"..."}' \
        --index-path <vault/hostname/index.jsonl>
    python3 build-index.py <session.md> --meta-file /tmp/meta.json \
        --index-path <vault/hostname/index.jsonl>
"""

import json
import sys
import os
import argparse
import socket
from pathlib import Path
from datetime import datetime


def extract_first_user_message(md_path):
    """Extract the first substantive user message from the exported Markdown."""
    # Noise patterns to skip when looking for first real user message
    noise_patterns = [
        'caveat:', 'login', 'Login successful',
        'the messages below were generated',
        'DO NOT respond',
    ]

    try:
        with open(md_path, 'r', encoding='utf-8') as f:
            in_user_section = False
            user_section_count = 0
            for line in f:
                if line.strip() == '## User':
                    user_section_count += 1
                    in_user_section = True
                    lines = []
                    continue
                elif line.startswith('## ') and in_user_section:
                    # End of user section — check if it's useful
                    text = ' '.join(lines).strip()
                    if text and len(text) > 5:
                        is_noise = any(p.lower() in text.lower() for p in noise_patterns)
                        if not is_noise:
                            if len(text) > 300:
                                text = text[:297] + '...'
                            return text
                    in_user_section = False
                    lines = []
                elif in_user_section:
                    stripped = line.strip()
                    if stripped and not stripped.startswith('<') and not stripped.startswith('>'):
                        lines.append(stripped)

                # Stop after checking first 10 user sections
                if user_section_count > 10:
                    break

            return ''
    except Exception:
        return ''


def build_index_entry(md_path, metadata, hostname=None):
    """Build an index entry dict from metadata and the exported Markdown."""
    md_path = Path(md_path)

    if not hostname:
        hostname = socket.gethostname()

    # Compute relative path within the vault
    # vault/{hostname}/sessions/YYYY-MM-DD/{session_id}.md
    # We store just sessions/YYYY-MM-DD/{session_id}.md (relative to hostname dir)
    try:
        parts = md_path.parts
        sessions_idx = parts.index('sessions')
        rel_path = '/'.join(parts[sessions_idx:])
    except (ValueError, IndexError):
        rel_path = md_path.name

    date = metadata.get('start_time', '')[:10] or 'unknown'

    entry = {
        'session_id': metadata.get('session_id', md_path.stem),
        'date': date,
        'start_time': metadata.get('start_time', ''),
        'end_time': metadata.get('end_time', ''),
        'project': metadata.get('project', ''),
        'model': metadata.get('model', ''),
        'message_count': metadata.get('message_count', 0),
        'md_path': rel_path,
        'first_user_message': extract_first_user_message(md_path),
        'source_file': metadata.get('source_file', ''),
        'hostname': hostname,
        'indexed_at': datetime.now().isoformat(),
    }

    return entry


def append_to_index(index_path, entry):
    """Append an entry to index.jsonl, skipping duplicates by session_id."""
    index_path = Path(index_path)
    index_path.parent.mkdir(parents=True, exist_ok=True)

    # Check for duplicates
    existing_ids = set()
    if index_path.exists():
        with open(index_path, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        obj = json.loads(line)
                        existing_ids.add(obj.get('session_id', ''))
                    except json.JSONDecodeError:
                        continue

    if entry['session_id'] in existing_ids:
        print(f'Skipping duplicate: {entry["session_id"]}', file=sys.stderr)
        return False

    with open(index_path, 'a', encoding='utf-8') as f:
        f.write(json.dumps(entry, ensure_ascii=False) + '\n')

    print(f'Indexed: {entry["session_id"]} ({entry["date"]})', file=sys.stderr)
    return True


def main():
    parser = argparse.ArgumentParser(description='Build session index')
    parser.add_argument('md_path', help='Path to the exported .md file')
    parser.add_argument('--meta', help='Metadata as JSON string')
    parser.add_argument('--meta-file', help='Path to metadata JSON file')
    parser.add_argument('--index-path', required=True, help='Path to index.jsonl')
    parser.add_argument('--hostname', help='Override hostname')

    args = parser.parse_args()

    # Load metadata
    if args.meta:
        metadata = json.loads(args.meta)
    elif args.meta_file:
        with open(args.meta_file, 'r') as f:
            metadata = json.load(f)
    else:
        metadata = {}

    entry = build_index_entry(args.md_path, metadata, hostname=args.hostname)
    append_to_index(args.index_path, entry)


if __name__ == '__main__':
    main()
