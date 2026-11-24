"""Load one literal .env before application imports, without shell execution."""
from __future__ import annotations

import os
import re
import shlex
import sys
from pathlib import Path

ENV_KEYS = frozenset({
    'PAPER_FIREHOSE_DATA_DIR', 'PAPER_COMPASS_HOST', 'PAPER_COMPASS_PUBLIC_URL',
    'PAPER_COMPASS_DOCS_DIR', 'PAPER_COMPASS_API_URL', 'GITHUB_CLIENT_ID',
    'GITHUB_CLIENT_SECRET', 'PAPER_COMPASS_DEV_LOGIN', 'PAPER_COMPASS_EVENTS_ENABLED',
    'PAPER_COMPASS_EVENTS_RETENTION_DAYS', 'PAPER_COMPASS_EVENTS_DAILY_LIMIT',
    'PAPER_COMPASS_CLASSIFIER', 'PAPER_COMPASS_CLASSIFICATION_API_KEY',
    'PAPER_COMPASS_CLASSIFICATION_BASE_URL', 'PAPER_COMPASS_CLASSIFICATION_MODEL',
    'PAPER_COMPASS_CLASSIFICATION_REASONING_EFFORT', 'PAPER_COMPASS_CLASSIFICATION_TIMEOUT',
    'PAPER_COMPASS_CLASSIFICATION_MAX_TOKENS', 'PAPER_COMPASS_CLASSIFICATION_LIMIT',
    'PAPER_COMPASS_CLASSIFICATION_DAILY_BUDGET_CNY', 'PAPER_COMPASS_CLASSIFICATION_DAILY_REQUEST_LIMIT',
    'PAPER_COMPASS_CLASSIFICATION_DAILY_TOKEN_LIMIT', 'PAPER_COMPASS_CLASSIFICATION_PRICE_INPUT',
    'PAPER_COMPASS_CLASSIFICATION_PRICE_CACHED_INPUT', 'PAPER_COMPASS_CLASSIFICATION_PRICE_OUTPUT',
    'OPENALEX_API_KEY', 'OPENAI_API_KEY', 'MAILTO', 'SMTP_PASSWORD',
    'PAPER_COMPASS_MEILI_URL', 'PAPER_COMPASS_MEILI_SEARCH_KEY', 'PAPER_COMPASS_MEILI_WRITE_KEY',
})
PATH_KEYS = {'PAPER_FIREHOSE_DATA_DIR', 'PAPER_COMPASS_DOCS_DIR'}


def default_env_path():
    root = Path(__file__).resolve().parents[3]
    # Editable/source installs have a fixed project root. For wheel installs,
    # use only the explicit working directory, never search parent directories.
    if (root / 'pyproject.toml').is_file() and (root / 'src/paper_firehose').is_dir():
        return root / '.env'
    return Path.cwd() / '.env'


def read_environment(path):
    path = Path(path)
    try:
        if path.stat().st_size > 1024 * 1024:
            raise ValueError('Environment file exceeds 1 MiB: ' + str(path))
        text = path.read_text(encoding='utf-8-sig')
    except FileNotFoundError:
        return {}
    except (OSError, UnicodeError):
        raise ValueError('Cannot read environment file: ' + str(path)) from None
    values = {}
    for number, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        if line.startswith('export '):
            line = line[7:].lstrip()
        match = re.fullmatch(r'([A-Z][A-Z0-9_]*)\s*=\s*(.*)', line)
        if not match or match[1] not in ENV_KEYS or match[1] in values:
            raise ValueError(f'Invalid, unknown or duplicate setting in {path}, line {number}')
        key, value = match.groups()
        if value.startswith(('"', "'")):
            try:
                parts = shlex.split(value, comments=True, posix=True)
            except ValueError:
                raise ValueError(f'Invalid quoted value in {path}, line {number}') from None
            if len(parts) != 1:
                raise ValueError(f'Invalid quoted value in {path}, line {number}')
            value = parts[0]
        else:
            value = re.split(r'\s+#', value, maxsplit=1)[0].rstrip()
        if '\x00' in value:
            raise ValueError(f'Invalid value in {path}, line {number}')
        # Dollar expansion and command substitution are deliberately unsupported.
        if key in PATH_KEYS and value:
            candidate = Path(value).expanduser()
            value = str(candidate if candidate.is_absolute() else (path.resolve().parent / candidate).resolve())
        values[key] = value
    return values


def load_environment(path=None, *, environ=None):
    """Existing process variables win, including explicitly empty values."""
    target = os.environ if environ is None else environ
    values = read_environment(path if path is not None else default_env_path())
    applied = {}
    for key, value in values.items():
        if key not in target:
            target[key] = value
            applied[key] = value
    return applied


if __name__ == '__main__':
    # Startup scripts capture this output; never print it in logs or diagnostics.
    # shlex.quote makes even $(...) and backticks literal shell values.
    if sys.argv[1:] != ['--shell']:
        raise SystemExit('Usage: environment.py --shell')
    try:
        loaded = load_environment()
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1) from None
    print('\n'.join('export ' + key + '=' + shlex.quote(value) for key, value in loaded.items()))
