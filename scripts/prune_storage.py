#!/usr/bin/env python3
"""Keep one predecessor and one backup per database. Dry-run unless --apply."""
from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import shutil
import sqlite3
from contextlib import ExitStack, closing
from pathlib import Path

QUERY = re.compile(r'query-[0-9a-f]{32}\.db\Z')
DEPLOY = re.compile(r'20\d{6}-[0-9a-f]{7,40}\Z')
AUTO = re.compile(r'(.+)\.\d{8}-\d{6}\.backup\.db\Z')


def regular(path):
    if path.is_symlink():
        raise ValueError('Refusing symlink: ' + str(path))
    return path.is_file()


def inventory(data, backups):
    """No writes. Only exact managed filenames are candidates for deletion."""
    query = data / 'query-store'
    if query.is_symlink():
        raise ValueError('Refusing symlink query directory')
    current_name = json.loads((query / 'current.json').read_text())['database']
    if not QUERY.fullmatch(current_name):
        raise ValueError('Invalid current query manifest')
    current = query / current_name
    if not regular(current):
        raise ValueError('Current query database is missing')
    versions = sorted((p for p in query.glob('query-*.db') if QUERY.fullmatch(p.name) and regular(p)),
                      key=lambda p: (p.stat().st_mtime_ns, p.name), reverse=True)
    history = [p for p in versions if p != current][:1]
    keep_query = [current, *history]
    delete = set(versions) - set(keep_query)
    deployments = []
    if backups.exists():
        if backups.is_symlink():
            raise ValueError('Refusing symlink backup directory')
        for p in backups.iterdir():
            if DEPLOY.fullmatch(p.name):
                if p.is_symlink():
                    raise ValueError('Refusing symlink deployment')
                if p.is_dir():
                    if any(item.is_symlink() for item in p.rglob('*')):
                        raise ValueError('Refusing symlink in deployment backup')
                    deployments.append(p)
    deployments.sort(key=lambda p: (p.name[:8], p.stat().st_mtime_ns, p.name), reverse=True)
    latest = deployments[0] if deployments else None
    groups = {}
    for p in data.glob('*.backup.db'):
        match = AUTO.fullmatch(p.name)
        if match and regular(p):
            groups.setdefault(match[1] + '.db', []).append(p)
    for directory in deployments:
        database_dir = directory / 'databases'
        if database_dir.exists():
            for p in database_dir.rglob('*.db'):
                if regular(p):
                    key = str(p.relative_to(database_dir))
                    groups.setdefault(key, []).append(p)
        # A rollback code/config bundle does not need another query DB copy.
        for p in (directory / 'query-store').glob('query-*.db'):
            if QUERY.fullmatch(p.name) and regular(p):
                delete.add(p)
        obsolete_manifest = directory / 'query-store/current.json'
        if regular(obsolete_manifest):
            delete.add(obsolete_manifest)
    moves, keep_backups = [], []
    for key, candidates in groups.items():
        # Prefer the automatic backup on equal timestamps, then stable paths.
        winner = max(candidates, key=lambda p: (p.stat().st_mtime_ns, p.parent == data, str(p)))
        delete.update(set(candidates) - {winner})
        target = winner
        if latest and winner.parent != data and not winner.is_relative_to(latest):
            target = latest / 'databases' / key
            moves.append((winner, target))
        keep_backups.append((winner, target))
    targets = {target for _, target in moves}
    delete.difference_update(targets)
    return {'keep_query': keep_query, 'keep_backups': keep_backups, 'moves': moves,
            'delete_files': sorted(delete), 'delete_directories': deployments[1:],
            'keep_deployment': latest}


def check_database(path):
    with closing(sqlite3.connect(path.resolve().as_uri() + '?mode=ro&immutable=1', uri=True)) as conn:
        conn.execute('PRAGMA cache_size=-2048')
        if conn.execute('PRAGMA quick_check(1)').fetchone()[0] != 'ok':
            raise ValueError('Retained database failed validation: ' + str(path))


def execute(plan):
    # Validate every retained copy before the first destructive operation.
    if plan['delete_files'] or plan['delete_directories'] or plan['moves']:
        for path in dict.fromkeys([*plan['keep_query'], *(p for p, _ in plan['keep_backups'])]):
            check_database(path)
    for source, target in plan['moves']:
        target.parent.mkdir(parents=True, exist_ok=True)
        os.replace(source, target)
    for path in plan['delete_files']:
        path.unlink(missing_ok=True)
    for path in plan['delete_directories']:
        shutil.rmtree(path)
    if plan['keep_deployment']:
        marker = plan['keep_deployment'] / 'retention.json'
        marker.write_text(json.dumps({
            'note': 'One backup per database globally; query history lives in the active query-store only.',
            'database_backups': [str(target) for _, target in plan['keep_backups']],
            'query_files': [str(p) for p in plan['keep_query']],
        }, ensure_ascii=False, indent=2) + '\n')
        marker.chmod(0o600)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir', required=True, type=Path)
    parser.add_argument('--backup-dir', required=True, type=Path)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    if args.data_dir.is_symlink() or args.data_dir.resolve() == Path('/'):
        raise ValueError('Invalid data directory')
    # Maintenance never creates missing runtime data or reads business secrets.
    with ExitStack() as stack:
        for path in [args.data_dir / 'update.lock', args.data_dir / 'query-store/build.lock']:
            lock = stack.enter_context(path.open('a'))
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                print(json.dumps({'status': 'skipped_busy', 'lock': str(path)}))
                return 75
        plan = inventory(args.data_dir, args.backup_dir)
        report = {'apply': args.apply, 'keep_query': [str(p) for p in plan['keep_query']],
                  'keep_backups': [str(target) for _, target in plan['keep_backups']],
                  'move_unique_backups': [[str(a), str(b)] for a, b in plan['moves']],
                  'delete_files': [str(p) for p in plan['delete_files']],
                  'delete_directories': [str(p) for p in plan['delete_directories']]}
        if args.apply:
            execute(plan)
        print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
