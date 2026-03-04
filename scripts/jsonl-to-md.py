#!/usr/bin/env python3
"""Convert a Claude Code JSONL session file to clean Markdown.

Streams the JSONL line-by-line (handles 12MB+ sessions).
Skips 'progress' and 'file-history-snapshot' types (~80% of lines).
Applies secret scrubbing before writing.

Usage:
    python3 jsonl-to-md.py <session.jsonl> <output.md>
    python3 jsonl-to-md.py <session.jsonl>  # prints to stdout
"""

import json
import sys
import os
import argparse
from datetime import datetime
from pathlib import Path

# Import scrubbing from sibling module (filename has hyphen, can't use normal import)
import importlib.util
_scrub_spec = importlib.util.spec_from_file_location(
    'scrub_patterns', Path(__file__).parent / 'scrub-patterns.py')
_scrub_mod = importlib.util.module_from_spec(_scrub_spec)
_scrub_spec.loader.exec_module(_scrub_mod)
scrub = _scrub_mod.scrub


# Message types to skip entirely (noise / internal state)
SKIP_TYPES = {'progress', 'file-history-snapshot', 'queue-operation'}


def extract_text_from_content(content):
    """Extract readable text from a message content field.

    Content can be:
    - A plain string
    - A list of content blocks (text, tool_use, tool_result, etc.)
    """
    if isinstance(content, str):
        return content.strip()

    if not isinstance(content, list):
        return ''

    parts = []
    for block in content:
        btype = block.get('type', '')

        if btype == 'text':
            text = block.get('text', '').strip()
            if text:
                parts.append(text)

        elif btype == 'tool_use':
            tool_name = block.get('name', 'unknown')
            tool_input = block.get('input', {})
            parts.append(format_tool_use(tool_name, tool_input))

        elif btype == 'tool_result':
            tool_id = block.get('tool_use_id', '')
            result_content = block.get('content', '')
            if isinstance(result_content, list):
                for rc in result_content:
                    if rc.get('type') == 'text':
                        text = rc.get('text', '').strip()
                        if text:
                            # Truncate very long tool results
                            if len(text) > 2000:
                                text = text[:2000] + '\n... [truncated]'
                            parts.append(f'**Tool result:**\n```\n{text}\n```')
            elif isinstance(result_content, str) and result_content.strip():
                text = result_content.strip()
                if len(text) > 2000:
                    text = text[:2000] + '\n... [truncated]'
                parts.append(f'**Tool result:**\n```\n{text}\n```')

    return '\n\n'.join(parts)


def format_tool_use(name, input_data):
    """Format a tool_use block into readable Markdown."""
    lines = [f'**Tool: `{name}`**']

    if name == 'Bash':
        cmd = input_data.get('command', '')
        desc = input_data.get('description', '')
        if desc:
            lines.append(f'> {desc}')
        if cmd:
            lines.append(f'```bash\n{cmd}\n```')

    elif name in ('Read', 'Glob', 'Grep'):
        if name == 'Read':
            fp = input_data.get('file_path', '')
            lines.append(f'Reading: `{fp}`')
        elif name == 'Glob':
            pat = input_data.get('pattern', '')
            lines.append(f'Pattern: `{pat}`')
        elif name == 'Grep':
            pat = input_data.get('pattern', '')
            path = input_data.get('path', '')
            lines.append(f'Search: `{pat}`' + (f' in `{path}`' if path else ''))

    elif name in ('Edit', 'Write'):
        fp = input_data.get('file_path', '')
        if name == 'Write':
            lines.append(f'Writing: `{fp}`')
        else:
            old = input_data.get('old_string', '')
            lines.append(f'Editing: `{fp}`')
            if old:
                preview = old[:200] + ('...' if len(old) > 200 else '')
                lines.append(f'```\n{preview}\n```')

    elif name == 'Agent':
        desc = input_data.get('description', '')
        prompt = input_data.get('prompt', '')
        lines.append(f'Agent: {desc}')
        if prompt:
            preview = prompt[:300] + ('...' if len(prompt) > 300 else '')
            lines.append(f'> {preview}')

    elif name == 'WebSearch':
        query = input_data.get('query', '')
        lines.append(f'Query: `{query}`')

    elif name == 'WebFetch':
        url = input_data.get('url', '')
        lines.append(f'URL: `{url}`')

    else:
        # Generic: show input keys
        if input_data:
            keys = list(input_data.keys())[:5]
            lines.append(f'Input keys: {", ".join(keys)}')

    return '\n'.join(lines)


def strip_xml_tags(text):
    """Remove system XML tags that add noise (system-reminder, local-command-*, etc.)."""
    import re
    # Remove entire system-reminder blocks
    text = re.sub(r'<system-reminder>[\s\S]*?</system-reminder>', '', text)
    # Remove local-command wrappers but keep content
    text = re.sub(r'<local-command-(?:caveat|stdout|stderr)>([\s\S]*?)</local-command-(?:caveat|stdout|stderr)>', r'\1', text)
    # Remove command-name/command-message/command-args wrappers but keep content
    text = re.sub(r'<command-(?:name|message|args)>([\s\S]*?)</command-(?:name|message|args)>', r'\1', text)
    # Remove empty self-closing tags
    text = re.sub(r'<command-args\s*/>', '', text)
    # Remove any remaining Claude system XML tags
    text = re.sub(r'<(?:user-prompt-submit-hook|fast_mode_info|antml:[^>]*)>[\s\S]*?</(?:user-prompt-submit-hook|fast_mode_info|antml:[^>]*)>', '', text)
    return text.strip()


def parse_session_metadata(lines):
    """Extract session metadata from the first few lines."""
    metadata = {
        'session_id': '',
        'project': '',
        'start_time': '',
        'end_time': '',
        'model': '',
    }

    first_ts = None
    last_ts = None

    for line_str in lines:
        try:
            obj = json.loads(line_str)
        except json.JSONDecodeError:
            continue

        # Extract timestamp (can be int ms or ISO 8601 string)
        ts = obj.get('timestamp')
        if ts:
            # Normalize to ISO string
            if isinstance(ts, (int, float)):
                ts_str = datetime.fromtimestamp(ts / 1000).isoformat()
            elif isinstance(ts, str):
                # Try parsing as int first (epoch ms as string)
                try:
                    ts_str = datetime.fromtimestamp(int(ts) / 1000).isoformat()
                except ValueError:
                    # Already ISO string — use as-is
                    ts_str = ts.replace('Z', '+00:00')
            else:
                ts_str = None

            if ts_str:
                if first_ts is None:
                    first_ts = ts_str
                last_ts = ts_str

        # Extract session ID from the message if available
        msg = obj.get('message', {})
        if isinstance(msg, dict):
            model = msg.get('model', '')
            if model and not metadata['model']:
                metadata['model'] = model

    if first_ts:
        metadata['start_time'] = first_ts
    if last_ts:
        metadata['end_time'] = last_ts

    return metadata


def convert_jsonl_to_md(jsonl_path, output_path=None, session_id=None, project=None):
    """Convert a JSONL session file to Markdown.

    Args:
        jsonl_path: Path to the .jsonl file
        output_path: Path to write .md file (None = stdout)
        session_id: Override session ID (extracted from filename if None)
        project: Project path for metadata
    """
    jsonl_path = Path(jsonl_path)

    if not jsonl_path.exists():
        print(f'Error: {jsonl_path} not found', file=sys.stderr)
        sys.exit(1)

    # Derive session ID from filename if not provided
    if not session_id:
        session_id = jsonl_path.stem  # e.g., "3234d52c-8f5c-4d0a-b468-9db3f3be196e"

    # First pass: read all lines to get metadata (timestamps, model)
    # We stream but keep raw lines for metadata extraction
    raw_lines = []
    with open(jsonl_path, 'r', encoding='utf-8', errors='replace') as f:
        for line in f:
            line = line.strip()
            if line:
                raw_lines.append(line)

    metadata = parse_session_metadata(raw_lines[:50] + raw_lines[-10:])
    metadata['session_id'] = session_id
    if project:
        metadata['project'] = project

    # Build markdown output
    md_parts = []

    # Header
    md_parts.append(f'# Session: {session_id}\n')
    md_parts.append(f'- **Date**: {metadata["start_time"][:10] if metadata["start_time"] else "unknown"}')
    md_parts.append(f'- **Start**: {metadata["start_time"] or "unknown"}')
    md_parts.append(f'- **End**: {metadata["end_time"] or "unknown"}')
    if metadata['model']:
        md_parts.append(f'- **Model**: {metadata["model"]}')
    if metadata['project']:
        md_parts.append(f'- **Project**: `{metadata["project"]}`')
    md_parts.append('')
    md_parts.append('---\n')

    # Second pass: convert messages
    msg_count = 0
    for line_str in raw_lines:
        try:
            obj = json.loads(line_str)
        except json.JSONDecodeError:
            continue

        msg_type = obj.get('type', '')

        # Skip noise
        if msg_type in SKIP_TYPES:
            continue

        msg = obj.get('message', {})
        if not isinstance(msg, dict):
            continue

        role = msg.get('role', msg_type)
        content = msg.get('content', '')

        if not content:
            continue

        # Extract text
        text = extract_text_from_content(content)
        if not text:
            continue

        # Clean up XML tags
        text = strip_xml_tags(text)
        if not text:
            continue

        # Apply secret scrubbing
        text = scrub(text)

        msg_count += 1

        # Format by role
        if role == 'user':
            md_parts.append(f'## User\n')
            md_parts.append(text)
            md_parts.append('')
        elif role == 'assistant':
            md_parts.append(f'## Assistant\n')
            md_parts.append(text)
            md_parts.append('')
        elif msg_type == 'system':
            # System messages — include but mark them
            md_parts.append(f'## System\n')
            md_parts.append(f'> {text[:500]}')
            md_parts.append('')
        else:
            # Other types (shouldn't happen after filtering)
            md_parts.append(f'## {msg_type.title()}\n')
            md_parts.append(text)
            md_parts.append('')

    # Footer
    md_parts.append('---')
    md_parts.append(f'*Exported by Session Vault — {msg_count} messages*')

    final_md = '\n'.join(md_parts)

    if output_path:
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(final_md)
        print(f'Exported: {output_path} ({msg_count} messages, {len(final_md)} bytes)',
              file=sys.stderr)
    else:
        sys.stdout.write(final_md)

    return metadata, msg_count


def main():
    parser = argparse.ArgumentParser(description='Convert Claude Code JSONL session to Markdown')
    parser.add_argument('jsonl', help='Path to the .jsonl session file')
    parser.add_argument('output', nargs='?', help='Output .md path (stdout if omitted)')
    parser.add_argument('--session-id', help='Override session ID')
    parser.add_argument('--project', help='Project path for metadata')
    parser.add_argument('--json-meta', action='store_true',
                        help='Print metadata as JSON to stdout after conversion')

    args = parser.parse_args()

    metadata, msg_count = convert_jsonl_to_md(
        args.jsonl,
        args.output,
        session_id=args.session_id,
        project=args.project,
    )

    if args.json_meta:
        metadata['message_count'] = msg_count
        metadata['source_file'] = str(args.jsonl)
        print(json.dumps(metadata))


if __name__ == '__main__':
    main()
