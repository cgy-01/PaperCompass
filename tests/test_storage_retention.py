"""Retention must never delete the live DB or the last usable backup."""
import fcntl
import importlib.util
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/prune_storage.py'
spec = importlib.util.spec_from_file_location('prune_storage', SCRIPT)
retention = importlib.util.module_from_spec(spec)
spec.loader.exec_module(retention)


def database(path, value='safe', stamp=1):
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as conn:
        conn.execute('CREATE TABLE records(value TEXT)')
        conn.execute('INSERT INTO records VALUES (?)', (value,))
    os.utime(path, (stamp, stamp))
    return path


@pytest.fixture
def layout(tmp_path):
    data, backups = tmp_path / 'runtime', tmp_path / 'backups'
    query = data / 'query-store'
    current = database(query / ('query-' + 'a' * 32 + '.db'), stamp=1)
    previous = database(query / ('query-' + 'b' * 32 + '.db'), stamp=2)
    (query / 'current.json').write_text(json.dumps({'database': current.name}))
    return data, backups, current, previous


def test_query_retention_is_one_history_and_preserves_live_data(layout):
    data, backups, current, previous = layout
    expired = database(current.parent / ('query-' + 'c' * 32 + '.db'), stamp=0)
    raw = database(data / 'matched_entries_history.db')
    before = raw.read_bytes(), current.read_bytes()
    unrelated = current.parent / 'unmanaged.db'
    unrelated.write_bytes(b'unrelated')
    plan = retention.inventory(data, backups)
    retention.execute(plan)
    assert current.exists() and previous.exists() and not expired.exists()
    assert unrelated.exists() and (raw.read_bytes(), current.read_bytes()) == before
    assert not retention.inventory(data, backups)['delete_files']


def test_global_backups_deduplicate_and_rescue_unique_old_backups(layout):
    data, backups, current, _ = layout
    old, newest = backups / '20261118-1111111', backups / '20261119-2222222'
    duplicate = database(old / 'databases/matched_entries_history.db', stamp=1)
    user = database(old / 'databases/users/users.db', value='user records', stamp=3)
    new_backup = database(data / 'matched_entries_history.20261119-063000.backup.db', stamp=4)
    database(newest / 'databases/users/users.db', value='older user copy', stamp=2)
    archived_query = database(newest / 'query-store' / current.name)
    archived_manifest = archived_query.parent / 'current.json'
    archived_manifest.write_text(json.dumps({'database': current.name}))
    (newest / 'source-before.tar.gz').write_bytes(b'code rollback')
    plan = retention.inventory(data, backups)
    retention.execute(plan)
    assert new_backup.exists() and not duplicate.exists() and not old.exists()
    assert not archived_query.exists() and not archived_manifest.exists()
    restored = newest / 'databases/users/users.db'
    with sqlite3.connect(restored) as conn:
        assert conn.execute('SELECT value FROM records').fetchone()[0] == 'user records'
    assert (newest / 'source-before.tar.gz').read_bytes() == b'code rollback'
    assert (newest / 'retention.json').is_file()
    again = retention.inventory(data, backups)
    assert not again['delete_files'] and not again['moves'] and not again['delete_directories']


def test_invalid_retained_backup_blocks_all_deletion(layout):
    data, backups, current, _ = layout
    expired = database(current.parent / ('query-' + 'c' * 32 + '.db'), stamp=0)
    backup = data / 'matched_entries_history.20261119-063000.backup.db'
    backup.write_bytes(b'corrupted backup')
    with pytest.raises(sqlite3.DatabaseError):
        retention.execute(retention.inventory(data, backups))
    assert expired.is_file() and current.is_file() and backup.is_file()


def test_missing_live_manifest_target_refuses_cleanup(layout):
    data, backups, current, previous = layout
    current.unlink()
    with pytest.raises(ValueError, match='missing'):
        retention.inventory(data, backups)
    assert previous.exists()


def test_invalid_manifest_name_cannot_select_outside_data(layout):
    data, backups, current, _ = layout
    (current.parent / 'current.json').write_text('{"database":"../papers.db"}')
    with pytest.raises(ValueError, match='Invalid'):
        retention.inventory(data, backups)


def test_symlink_backup_is_never_followed(layout, tmp_path):
    data, backups, _, _ = layout
    backups.mkdir()
    external = tmp_path / 'outside'
    external.mkdir()
    (backups / '20261119-1111111').symlink_to(external, target_is_directory=True)
    with pytest.raises(ValueError, match='symlink'):
        retention.inventory(data, backups)


def test_cli_dry_run_and_busy_locks_do_not_delete(layout):
    data, backups, current, _ = layout
    expired = database(current.parent / ('query-' + 'c' * 32 + '.db'), stamp=0)
    args = [sys.executable, '-I', str(SCRIPT), '--data-dir', str(data), '--backup-dir', str(backups)]
    result = subprocess.run(args, capture_output=True, text=True, check=True)
    assert json.loads(result.stdout)['apply'] is False and expired.exists()
    with (data / 'update.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        result = subprocess.run([*args, '--apply'], capture_output=True, text=True)
        assert result.returncode == 75
        assert json.loads(result.stdout)['status'] == 'skipped_busy'
        assert expired.exists()


def test_no_redundant_copies_does_not_rescan_large_databases(layout, monkeypatch):
    data, backups, _, _ = layout
    monkeypatch.setattr(retention, 'check_database', lambda _: pytest.fail('No deletion needs no DB scan'))
    retention.execute(retention.inventory(data, backups))
