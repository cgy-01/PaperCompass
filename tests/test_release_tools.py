"""Release delivery must preserve source identity and exclude private runtime state."""
import hashlib
import io
import json
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from build_release import build, validate_tag
from check_source import unsafe_path, validate_source
from smoke_release import extract_verified


@pytest.fixture
def project(tmp_path):
    root = tmp_path / 'source'
    root.mkdir()
    files = {
        '.gitignore': 'node_modules/\n.runtime-demo/\ndist/\n',
        'README.md': 'A source application', 'LICENSE': 'MIT',
        'pyproject.toml': '[project]\nname="example"\nversion="1.0.0"\n',
        'package.json': '{"dependencies":{"lucide":"1.0.0"}}',
        'package-lock.json': '{"packages":{"node_modules/lucide":{"version":"1.0.0"}}}',
        'src/example.py': 'value = 1\n', 'docs/MCP_USER_GUIDE.md': 'Public manual',
        'docs/img/example.png': 'public image',
    }
    for name, content in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    subprocess.run(['git', 'init', '-q', '-b', 'main'], cwd=root, check=True)
    subprocess.run(['git', 'add', '.'], cwd=root, check=True)
    commit(root)
    for name, content in {'package.json': '{"version":"1.0.0"}', 'LICENSE': 'ISC',
                          'dist/esm/iconsAndAliases.js': '// official package',
                          'dist/umd/lucide.min.js': '// compatibility'}.items():
        path = root / 'node_modules/lucide' / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    return root


def commit(root):
    subprocess.run(['git', '-c', 'user.name=CI fixture', '-c', 'user.email=ci@example.test',
                    'commit', '-qm', 'fixture'], cwd=root, check=True)


def test_reproducible_bundle_preserves_docs_and_locked_assets_without_runtime(project, tmp_path):
    private = project / '.runtime-demo'
    private.mkdir()
    (private / 'users.db').write_bytes(b'private fixture')
    first = build(project, tmp_path / 'first')
    second = build(project, tmp_path / 'second')
    assert first.read_bytes() == second.read_bytes()
    unpacked = extract_verified(first.parent, tmp_path / 'unpacked')
    assert (unpacked / 'docs/img/example.png').is_file()
    assert (unpacked / 'node_modules/lucide/dist/esm/iconsAndAliases.js').is_file()
    assert not (unpacked / '.runtime-demo').exists()
    assert not (unpacked / '.git').exists()


@pytest.mark.parametrize('name', ['.env', 'prod.env', '.runtime-demo/papers.db', 'users/users.db',
                                  'data/papers.db.gz', '.ssh/id_rsa', 'secrets/password.txt'])
def test_tracked_private_files_are_rejected(project, name):
    path = project / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('sensitive')
    subprocess.run(['git', 'add', '-f', name], cwd=project, check=True)
    with pytest.raises(ValueError, match='Unsafe'):
        validate_source(project)


def test_known_credential_format_is_rejected_without_printing_credential(project):
    path = project / 'src/credential.py'
    path.write_text('token = ' + repr('ghp_' + 'A' * 36))
    subprocess.run(['git', 'add', 'src/credential.py'], cwd=project, check=True)
    with pytest.raises(ValueError, match='credential.py') as error:
        validate_source(project)
    assert 'A' * 36 not in str(error.value)


def test_lock_mismatch_and_dirty_source_cannot_be_published(project, tmp_path):
    (project / 'README.md').write_text('uncommitted work')
    with pytest.raises(ValueError, match='clean checkout'):
        build(project, tmp_path / 'dirty')
    subprocess.run(['git', 'add', 'README.md'], cwd=project, check=True)
    commit(project)
    (project / 'node_modules/lucide/package.json').write_text('{"version":"2.0.0"}')
    with pytest.raises(ValueError, match='package-lock'):
        build(project, tmp_path / 'wrong-dependency')


def test_checksum_corruption_is_rejected(project, tmp_path):
    archive = build(project, tmp_path / 'bundle')
    archive.write_bytes(archive.read_bytes() + b'corrupt')
    with pytest.raises(ValueError, match='checksum'):
        extract_verified(archive.parent, tmp_path / 'unpacked')


def test_unsafe_tar_is_rejected_even_with_a_matching_archive_checksum(tmp_path):
    bundle = tmp_path / 'bundle'
    bundle.mkdir()
    archive = bundle / 'bad.tar.gz'
    with tarfile.open(archive, 'w:gz') as tar:
        info = tarfile.TarInfo('../escape')
        info.size = 1
        tar.addfile(info, io.BytesIO(b'x'))
    (bundle / 'SHA256SUMS').write_text(hashlib.sha256(archive.read_bytes()).hexdigest() + '  bad.tar.gz\n')
    with pytest.raises(ValueError, match='Unsafe'):
        extract_verified(bundle, tmp_path / 'unpacked')
    assert not (tmp_path / 'escape').exists()


def test_release_tag_must_exist_and_belong_to_main(project):
    subprocess.run(['git', 'update-ref', 'refs/remotes/origin/main', 'HEAD'], cwd=project, check=True)
    subprocess.run(['git', 'tag', 'v1.2.0'], cwd=project, check=True)
    assert validate_tag(project, 'v1.2.0')['prerelease'] == 'false'
    with pytest.raises(ValueError):
        validate_tag(project, 'main; echo unexpected')
    with pytest.raises(subprocess.CalledProcessError):
        validate_tag(project, 'v9.9.9')
    assert not unsafe_path('src/paper_firehose/system/config/secrets/mailing_lists.yaml')
