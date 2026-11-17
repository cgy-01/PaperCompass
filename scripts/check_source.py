#!/usr/bin/env python3
"""Validate tracked source without importing the application or reading runtime data."""
from __future__ import annotations

import ast
import re
import subprocess
from pathlib import Path, PurePosixPath


PRIVATE_PARTS = {'.git', '.aws', '.ssh', '.codex', '.agents', '.venv', 'venv',
                 'node_modules', '__pycache__', '.pytest_cache'}
SECRET = re.compile(rb'-----BEGIN (?:OPENSSH |RSA |EC |DSA )?PRIVATE KEY-----|'
                    rb'\bgh[pousr]_[A-Za-z0-9]{30,}|\bgithub_pat_[A-Za-z0-9_]{40,}|'
                    rb'\bsk-proj-[A-Za-z0-9_-]{40,}')


def unsafe_path(name: str) -> bool:
    path = PurePosixPath(name)
    if path.is_absolute() or '..' in path.parts or '\\' in name:
        return True
    if any(part in PRIVATE_PARTS or part.startswith(('.runtime', '.paper_firehose'))
           or part.endswith('.egg-info') for part in path.parts):
        return True
    if 'secrets' in path.parts and name not in {
            'secrets/README.md', 'src/paper_firehose/system/config/secrets/README.md',
            'src/paper_firehose/system/config/secrets/mailing_lists.yaml'}:
        return True
    lower = path.name.lower()
    return (lower.endswith(('.db', '.db-wal', '.db-shm', '.db-journal', '.db.gz',
                            '.sqlite', '.sqlite3', '.sqlite-wal', '.sqlite-shm', '.sqlite3-wal',
                            '.sqlite3-shm', '.sqlite.gz', '.sqlite3.gz', '.env', '.log', '.pem', '.key'))
            or (lower.startswith('.env') and not lower.endswith('.example')))


def tracked_files(root: Path) -> list[str]:
    result = subprocess.check_output(['git', 'ls-files', '-z'], cwd=root)
    return sorted(result.decode().rstrip('\0').split('\0')) if result else []


def validate_source(root: Path) -> list[str]:
    names = tracked_files(root)
    for name in names:
        path = root / name
        if unsafe_path(name) or path.is_symlink() or not path.is_file():
            raise ValueError('Unsafe or missing tracked file: ' + name)
        data = path.read_bytes()
        if SECRET.search(data):
            raise ValueError('Possible credential in tracked file: ' + name)
        if path.suffix == '.py':
            ast.parse(data, filename=name)
    return names


if __name__ == '__main__':
    root = Path(__file__).resolve().parents[1]
    names = validate_source(root)
    print(f'Checked {len(names)} tracked files: Python syntax and private-data exclusions passed.')
