"""Unified configuration precedence, literal parsing and startup isolation."""
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from unittest.mock import Mock

import pytest
from click.testing import CliRunner

from paper_firehose.core import environment as env
from paper_firehose.processors.emailer import SMTPSender
import paper_firehose.cli as cli_module

ROOT = Path(__file__).resolve().parents[1]


def test_all_business_keys_are_in_one_template():
    names = re.findall(r'(?m)^#?\s*([A-Z][A-Z0-9_]+)=', (ROOT / '.env.example').read_text())
    assert len(names) == len(set(names)) == 29
    assert set(names) == env.ENV_KEYS


def test_missing_file_preserves_existing_behavior(tmp_path):
    existing = {'PAPER_COMPASS_HOST': '127.0.0.1'}
    assert env.load_environment(tmp_path / '.env', environ=existing) == {}
    assert existing == {'PAPER_COMPASS_HOST': '127.0.0.1'}


def test_literal_values_comments_quotes_and_relative_paths(tmp_path):
    path = tmp_path / '.env'
    path.write_text('\ufeff# comment\nPAPER_FIREHOSE_DATA_DIR=./runtime\n'
                    'PAPER_COMPASS_DOCS_DIR="my docs"\n'
                    'PAPER_COMPASS_HOST=127.0.0.1 # comment\n'
                    'GITHUB_CLIENT_SECRET=abc#def\n'
                    'export SMTP_PASSWORD=\'quoted # $value\' # comment\n'
                    'PAPER_COMPASS_CLASSIFICATION_API_KEY="literal$KEY"\n'
                    'OPENAI_API_KEY=\n')
    parsed = env.read_environment(path)
    assert parsed['PAPER_FIREHOSE_DATA_DIR'] == str(tmp_path / 'runtime')
    assert parsed['PAPER_COMPASS_DOCS_DIR'] == str(tmp_path / 'my docs')
    assert parsed['PAPER_COMPASS_HOST'] == '127.0.0.1'
    assert parsed['GITHUB_CLIENT_SECRET'] == 'abc#def'
    assert parsed['SMTP_PASSWORD'] == 'quoted # $value'
    assert parsed['PAPER_COMPASS_CLASSIFICATION_API_KEY'] == 'literal$KEY'
    assert parsed['OPENAI_API_KEY'] == ''


def test_process_environment_wins_including_explicitly_empty_credentials(tmp_path):
    path = tmp_path / '.env'
    path.write_text('PAPER_COMPASS_HOST=file-host\nGITHUB_CLIENT_ID=file-id\n'
                    'PAPER_COMPASS_CLASSIFICATION_MODEL=file-model\n')
    existing = {'PAPER_COMPASS_HOST': 'process-host', 'GITHUB_CLIENT_ID': ''}
    applied = env.load_environment(path, environ=existing)
    assert applied == {'PAPER_COMPASS_CLASSIFICATION_MODEL': 'file-model'}
    assert existing['PAPER_COMPASS_HOST'] == 'process-host'
    assert existing['GITHUB_CLIENT_ID'] == ''


@pytest.mark.parametrize('line', [
    'not-an-assignment', 'UNKNOWN_SETTING=sensitive-value',
    'SMTP_PASSWORD="sensitive-value', 'SMTP_PASSWORD="sensitive-value" trailing',
    'SMTP_PASSWORD=sensitive-value\nSMTP_PASSWORD=duplicate',
    'SMTP_PASSWORD=sensitive-value\x00',
])
def test_invalid_file_is_atomic_and_error_does_not_expose_values(tmp_path, line):
    path = tmp_path / '.env'
    path.write_text('PAPER_COMPASS_HOST=valid-host\n' + line)
    existing = {}
    with pytest.raises(ValueError) as error:
        env.load_environment(path, environ=existing)
    assert existing == {}
    assert 'sensitive-value' not in str(error.value)
    assert 'line' in str(error.value)


def test_no_parent_directory_search_for_installed_package(tmp_path, monkeypatch):
    fake_file = tmp_path / 'venv/lib/python3.11/site-packages/paper_firehose/core/environment.py'
    monkeypatch.setattr(env, '__file__', str(fake_file))
    child = tmp_path / 'working'
    child.mkdir()
    (tmp_path / '.env').write_text('PAPER_COMPASS_CLASSIFIER=api\n')
    monkeypatch.chdir(child)
    assert env.default_env_path() == child / '.env'
    assert env.load_environment(environ={}) == {}


def test_shell_bootstrap_treats_commands_as_literal_and_respects_empty_values(tmp_path):
    root = tmp_path / 'project'
    module = root / 'src/paper_firehose/core/environment.py'
    module.parent.mkdir(parents=True)
    shutil.copyfile(env.__file__, module)
    (root / 'pyproject.toml').write_text('[project]\nname="fixture"\n')
    marker = tmp_path / 'must-not-exist'
    literal = '$(touch ' + str(marker) + '); `touch ' + str(marker) + '`'
    (root / '.env').write_text('SMTP_PASSWORD=' + literal + '\nGITHUB_CLIENT_ID=from-file\n')
    child_env = {k: v for k, v in os.environ.items() if k not in env.ENV_KEYS}
    child_env['GITHUB_CLIENT_ID'] = ''
    script = '''set -e
exports="$($1 "$2" --shell)"
eval "$exports"
"$1" -c 'import os,json; print(json.dumps([os.environ["SMTP_PASSWORD"],os.environ["GITHUB_CLIENT_ID"]]))'
'''
    result = subprocess.run(['bash', '-c', script, 'fixture', sys.executable, str(module)],
                            env=child_env, capture_output=True, text=True, check=True)
    assert json.loads(result.stdout) == [literal, '']
    assert not marker.exists()


def test_fresh_package_import_loads_paths_before_default_config_constants():
    child_env = {k: v for k, v in os.environ.items() if k not in env.ENV_KEYS}
    # Printing only paths, never any credentials from the user's real .env.
    script = ('import json; from paper_firehose.core.config import DEFAULT_CONFIG_PATH; '
              'from paper_firehose.core.paths import get_data_dir; '
              'print(json.dumps([str(get_data_dir()),str(DEFAULT_CONFIG_PATH)]))')
    result = subprocess.run([sys.executable, '-c', script], cwd=ROOT, env=child_env,
                            capture_output=True, text=True, check=True)
    values = env.read_environment(ROOT / '.env')
    expected = Path(values.get('PAPER_FIREHOSE_DATA_DIR', str(Path.home() / '.paper_firehose')))
    assert json.loads(result.stdout) == [str(expected), str(expected / 'config/config.yaml')]


def test_unified_smtp_password_precedes_old_file_and_inline_values(tmp_path, monkeypatch):
    password = tmp_path / 'legacy.env'
    password.write_text('legacy-file-value')
    sender = SMTPSender({'password': 'legacy-inline', 'password_file': str(password)})
    monkeypatch.setenv('SMTP_PASSWORD', 'unified-value')
    assert sender._load_password() == 'unified-value'
    monkeypatch.delenv('SMTP_PASSWORD')
    assert sender._load_password() == 'legacy-inline'
    assert SMTPSender({'password_file': str(password)})._load_password() == 'legacy-file-value'


def test_cli_host_flag_wins_over_unified_environment(monkeypatch):
    serve = Mock()
    monkeypatch.setattr(cli_module, 'serve_local_app', serve)
    monkeypatch.setenv('PAPER_COMPASS_HOST', '0.0.0.0')
    runner = CliRunner()
    assert runner.invoke(cli_module.cli, ['serve']).exit_code == 0
    assert serve.call_args.kwargs['host'] == '0.0.0.0'
    assert runner.invoke(cli_module.cli, ['serve', '--host', '127.0.0.1']).exit_code == 0
    assert serve.call_args.kwargs['host'] == '127.0.0.1'
