#!/usr/bin/env python3
"""Secret scrubbing patterns for Session Vault.

Applies regex-based redaction to session content before writing to disk.
Catches API keys, tokens, credentials, and other sensitive material.
No external dependencies — uses only Python stdlib re module.
"""

import re
from typing import List, Tuple

# Each pattern: (compiled regex, replacement label)
# Order matters — more specific patterns first to avoid partial matches
SCRUB_PATTERNS: List[Tuple[re.Pattern, str]] = [
    # OpenAI / Anthropic API keys
    (re.compile(r'sk-[a-zA-Z0-9_-]{20,}'), '[REDACTED:api-key]'),
    (re.compile(r'sk-ant-[a-zA-Z0-9_-]{20,}'), '[REDACTED:anthropic-key]'),

    # Groq API keys
    (re.compile(r'gsk_[a-zA-Z0-9]{20,}'), '[REDACTED:groq-key]'),

    # GitHub tokens
    (re.compile(r'ghp_[a-zA-Z0-9]{36,}'), '[REDACTED:github-pat]'),
    (re.compile(r'gho_[a-zA-Z0-9]{36,}'), '[REDACTED:github-oauth]'),
    (re.compile(r'ghs_[a-zA-Z0-9]{36,}'), '[REDACTED:github-server]'),
    (re.compile(r'ghr_[a-zA-Z0-9]{36,}'), '[REDACTED:github-refresh]'),
    (re.compile(r'github_pat_[a-zA-Z0-9_]{22,}'), '[REDACTED:github-fine-pat]'),

    # GitLab tokens
    (re.compile(r'glpat-[a-zA-Z0-9_-]{20,}'), '[REDACTED:gitlab-pat]'),

    # AWS keys
    (re.compile(r'AKIA[0-9A-Z]{16}'), '[REDACTED:aws-access-key]'),
    (re.compile(r'(?:aws_secret_access_key|AWS_SECRET_ACCESS_KEY)\s*[=:]\s*\S{20,}'),
     '[REDACTED:aws-secret-key]'),

    # Bearer tokens in headers
    (re.compile(r'[Bb]earer\s+[a-zA-Z0-9_\-.]{20,}'), 'Bearer [REDACTED:token]'),
    (re.compile(r'[Aa]uthorization:\s*\S+\s+[a-zA-Z0-9_\-.]{20,}'),
     'Authorization: [REDACTED:auth-header]'),

    # JWT tokens (three base64 segments separated by dots)
    (re.compile(r'eyJ[a-zA-Z0-9_-]{10,}\.eyJ[a-zA-Z0-9_-]{10,}\.[a-zA-Z0-9_-]{10,}'),
     '[REDACTED:jwt]'),

    # Generic base64-encoded secrets in env vars
    (re.compile(r'(?:SECRET|TOKEN|PASSWORD|APIKEY|API_KEY)[\s=:]+["\']?[a-zA-Z0-9+/=]{20,}["\']?',
                re.IGNORECASE),
     '[REDACTED:env-secret]'),

    # Connection strings (PostgreSQL, MySQL, MongoDB, Redis)
    (re.compile(r'(?:postgres|mysql|mongodb|redis)(?:ql)?://[^\s<>"\']{10,}'),
     '[REDACTED:connection-string]'),

    # SSH private key blocks
    (re.compile(r'-----BEGIN (?:RSA |EC |DSA |OPENSSH )?PRIVATE KEY-----[\s\S]*?-----END (?:RSA |EC |DSA |OPENSSH )?PRIVATE KEY-----'),
     '[REDACTED:ssh-private-key]'),

    # PGP private key blocks
    (re.compile(r'-----BEGIN PGP PRIVATE KEY BLOCK-----[\s\S]*?-----END PGP PRIVATE KEY BLOCK-----'),
     '[REDACTED:pgp-private-key]'),

    # Slack tokens
    (re.compile(r'xox[bpoas]-[a-zA-Z0-9-]{10,}'), '[REDACTED:slack-token]'),

    # Stripe keys
    (re.compile(r'(?:sk|pk)_(?:test|live)_[a-zA-Z0-9]{20,}'), '[REDACTED:stripe-key]'),

    # SendGrid
    (re.compile(r'SG\.[a-zA-Z0-9_-]{22,}\.[a-zA-Z0-9_-]{22,}'), '[REDACTED:sendgrid-key]'),

    # Twilio
    (re.compile(r'SK[a-f0-9]{32}'), '[REDACTED:twilio-key]'),

    # Passwords in URLs
    (re.compile(r'://[^:]+:[^@\s]{8,}@'), '://[REDACTED:url-credentials]@'),

    # Generic long hex secrets (40+ chars, likely hashes/tokens)
    (re.compile(r'(?:key|token|secret|password|credential)[\s=:]+["\']?[a-f0-9]{40,}["\']?',
                re.IGNORECASE),
     '[REDACTED:hex-secret]'),
]


def scrub(text: str) -> str:
    """Apply all scrubbing patterns to text. Returns scrubbed copy."""
    for pattern, replacement in SCRUB_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


def scrub_lines(lines: list) -> list:
    """Apply scrubbing to a list of strings."""
    return [scrub(line) for line in lines]


if __name__ == '__main__':
    import sys
    for line in sys.stdin:
        sys.stdout.write(scrub(line))
