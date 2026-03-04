#!/usr/bin/env python3
"""Session Vault — /recall search engine.

Search priority:
  1. QMD BM25 (`qmd search`) for keyword queries
  2. QMD semantic (`qmd vsearch`) for --semantic flag
  3. Temporal filtering via index.jsonl for date-based queries

Usage:
    python3 recall.py yesterday
    python3 recall.py "last week deployment"
    python3 recall.py rclone
    python3 recall.py 2026-02-17
    python3 recall.py --semantic "ideas I never acted on"
    python3 recall.py --limit 5 "error handling"
"""

import json
import sys
import os
import re
import argparse
import subprocess
import socket
from datetime import datetime, timedelta
from pathlib import Path


def load_config():
    """Load config.env from the skill directory."""
    script_dir = Path(__file__).parent
    config_file = script_dir.parent / 'config.env'

    config = {}
    if config_file.exists():
        with open(config_file, 'r') as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#') and '=' in line:
                    key, _, value = line.partition('=')
                    # Expand ~ and $HOME
                    value = value.strip('"').strip("'")
                    value = os.path.expandvars(os.path.expanduser(value))
                    config[key.strip()] = value
    return config


def parse_temporal_query(query):
    """Parse temporal expressions and return (start_date, end_date, remaining_keywords).

    Returns (None, None, query) if no temporal expression found.
    """
    now = datetime.now()
    today = now.date()
    lower = query.lower().strip()

    # Exact date: YYYY-MM-DD
    date_match = re.match(r'^(\d{4}-\d{2}-\d{2})(.*)$', lower)
    if date_match:
        try:
            d = datetime.strptime(date_match.group(1), '%Y-%m-%d').date()
            keywords = date_match.group(2).strip()
            return d, d, keywords
        except ValueError:
            pass

    # Relative expressions
    temporal_map = {
        'today': (today, today),
        'yesterday': (today - timedelta(days=1), today - timedelta(days=1)),
        'this week': (today - timedelta(days=today.weekday()), today),
        'last week': (today - timedelta(days=today.weekday() + 7),
                      today - timedelta(days=today.weekday() + 1)),
        'this month': (today.replace(day=1), today),
    }

    for expr, (start, end) in temporal_map.items():
        if lower.startswith(expr):
            keywords = lower[len(expr):].strip()
            return start, end, keywords

    # Relative days: "3d", "7d", "30d"
    days_match = re.match(r'^(\d+)d\s*(.*)', lower)
    if days_match:
        days = int(days_match.group(1))
        keywords = days_match.group(2).strip()
        return today - timedelta(days=days), today, keywords

    # "last N days"
    last_days_match = re.match(r'^last\s+(\d+)\s+days?\s*(.*)', lower)
    if last_days_match:
        days = int(last_days_match.group(1))
        keywords = last_days_match.group(2).strip()
        return today - timedelta(days=days), today, keywords

    return None, None, query


def search_index(vault_local, start_date=None, end_date=None, keywords=None, limit=10):
    """Search index.jsonl files across all hostnames."""
    vault_path = Path(vault_local) / 'vault'
    results = []

    if not vault_path.exists():
        return results

    # Find all index.jsonl files
    for index_file in vault_path.glob('*/index.jsonl'):
        hostname = index_file.parent.name

        with open(index_file, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue

                # Temporal filter
                if start_date or end_date:
                    entry_date_str = entry.get('date', '')
                    if not entry_date_str or entry_date_str == 'unknown':
                        continue
                    try:
                        entry_date = datetime.strptime(entry_date_str, '%Y-%m-%d').date()
                    except ValueError:
                        continue

                    if start_date and entry_date < start_date:
                        continue
                    if end_date and entry_date > end_date:
                        continue

                # Keyword filter (simple substring match on first_user_message + project)
                if keywords:
                    searchable = (
                        entry.get('first_user_message', '') + ' ' +
                        entry.get('project', '') + ' ' +
                        entry.get('session_id', '')
                    ).lower()

                    # All keywords must match
                    keyword_list = keywords.lower().split()
                    if not all(kw in searchable for kw in keyword_list):
                        continue

                entry['_hostname'] = hostname
                results.append(entry)

    # Sort by date descending
    results.sort(key=lambda x: x.get('start_time', ''), reverse=True)

    return results[:limit]


def qmd_search(query, limit=10):
    """Run QMD BM25 search if available."""
    try:
        result = subprocess.run(
            ['qmd', 'search', 'sessions', query, '--limit', str(limit), '--json'],
            capture_output=True, text=True, timeout=30
        )
        if result.returncode == 0 and result.stdout.strip():
            return json.loads(result.stdout)
    except (FileNotFoundError, subprocess.TimeoutExpired, json.JSONDecodeError):
        pass
    return None


def qmd_vsearch(query, limit=10):
    """Run QMD semantic search if available."""
    try:
        result = subprocess.run(
            ['qmd', 'vsearch', 'sessions', query, '--limit', str(limit), '--json'],
            capture_output=True, text=True, timeout=60
        )
        if result.returncode == 0 and result.stdout.strip():
            return json.loads(result.stdout)
    except (FileNotFoundError, subprocess.TimeoutExpired, json.JSONDecodeError):
        pass
    return None


def format_results(results, source='index'):
    """Format results for Claude Code output."""
    if not results:
        return 'No sessions found matching your query.'

    lines = []
    lines.append(f'**Found {len(results)} session(s)** (source: {source})\n')

    for i, entry in enumerate(results, 1):
        if source in ('qmd-bm25', 'qmd-semantic'):
            # QMD results have different structure
            file_path = entry.get('file', entry.get('path', ''))
            score = entry.get('score', '')
            snippet = entry.get('snippet', entry.get('content', ''))[:200]
            lines.append(f'### {i}. {Path(file_path).stem}')
            if score:
                lines.append(f'- **Score**: {score}')
            lines.append(f'- **File**: `{file_path}`')
            if snippet:
                lines.append(f'- **Preview**: {snippet}')
            lines.append('')
        else:
            # Index results
            sid = entry.get('session_id', 'unknown')
            date = entry.get('date', 'unknown')
            hostname = entry.get('_hostname', entry.get('hostname', ''))
            project = entry.get('project', '')
            model = entry.get('model', '')
            msg_count = entry.get('message_count', 0)
            summary = entry.get('first_user_message', '')
            md_path = entry.get('md_path', '')

            lines.append(f'### {i}. {date} — `{sid[:12]}...`')
            if hostname:
                lines.append(f'- **Machine**: {hostname}')
            if project:
                lines.append(f'- **Project**: `{project}`')
            if model:
                lines.append(f'- **Model**: {model}')
            lines.append(f'- **Messages**: {msg_count}')
            if summary:
                lines.append(f'- **Summary**: {summary}')
            if md_path:
                lines.append(f'- **File**: `{md_path}`')
            lines.append('')

    return '\n'.join(lines)


def main():
    parser = argparse.ArgumentParser(description='Session Vault recall')
    parser.add_argument('query', nargs='*', help='Search query (temporal + keywords)')
    parser.add_argument('--semantic', action='store_true',
                        help='Use QMD semantic search (vsearch)')
    parser.add_argument('--limit', type=int, default=10,
                        help='Maximum number of results (default: 10)')
    parser.add_argument('--json', action='store_true', dest='json_output',
                        help='Output raw JSON instead of formatted text')

    args = parser.parse_args()

    query = ' '.join(args.query) if args.query else ''

    if not query:
        parser.print_help()
        sys.exit(1)

    config = load_config()
    # Allow env var override for testing
    vault_local = os.environ.get('VAULT_LOCAL',
                                 config.get('VAULT_LOCAL', os.path.expanduser('~/.session-vault')))

    # Route 1: Semantic search via QMD
    if args.semantic:
        qmd_results = qmd_vsearch(query, limit=args.limit)
        if qmd_results:
            if args.json_output:
                print(json.dumps(qmd_results, indent=2))
            else:
                print(format_results(qmd_results, source='qmd-semantic'))
            return
        print('QMD semantic search unavailable, falling back to index...', file=sys.stderr)

    # Parse temporal component
    start_date, end_date, keywords = parse_temporal_query(query)

    # Route 2: If we have keywords (no temporal or temporal + keywords), try QMD BM25 first
    if keywords and not start_date:
        qmd_results = qmd_search(keywords, limit=args.limit)
        if qmd_results:
            if args.json_output:
                print(json.dumps(qmd_results, indent=2))
            else:
                print(format_results(qmd_results, source='qmd-bm25'))
            return

    # Route 3: Index search (temporal filter + optional keyword filter)
    results = search_index(
        vault_local,
        start_date=start_date,
        end_date=end_date,
        keywords=keywords if keywords else None,
        limit=args.limit,
    )

    if args.json_output:
        print(json.dumps(results, indent=2, default=str))
    else:
        print(format_results(results, source='index'))


if __name__ == '__main__':
    main()
