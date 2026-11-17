#!/usr/bin/env python3
"""Build a checked, reproducible source application bundle; never package runtime data."""
from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import os
import re
import subprocess
import tarfile
from pathlib import Path

from check_source import SECRET, unsafe_path, validate_source

VERSION = re.compile(r'v(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)(?:-[A-Za-z0-9]+(?:[.-][A-Za-z0-9]+)*)?\Z')
ROOT_FILES = {'pyproject.toml', 'requirements.txt', 'package.json', 'package-lock.json',
              'README.md', 'LICENSE', 'THIRD_PARTY_NOTICES.md', '.env.example',
              'start_dashboard.sh', 'start_frontend_dev.sh'}
PREFIXES = ('src/', 'docs/', 'deploy/', 'scripts/')


def git(root: Path, *args: str) -> str:
    return subprocess.check_output(['git', *args], cwd=root, text=True).strip()


def validate_tag(root: Path, tag: str) -> dict:
    if not VERSION.fullmatch(tag):
        raise ValueError('Expected a version tag such as v1.2.0 or v1.2.0-rc.1')
    sha = git(root, 'rev-parse', '--verify', 'refs/tags/' + tag + '^{commit}')
    subprocess.run(['git', 'merge-base', '--is-ancestor', sha, 'refs/remotes/origin/main'],
                   cwd=root, check=True)
    return {'tag': tag, 'sha': sha, 'prerelease': str('-' in tag).lower()}


def build(root: Path, output: Path, version: str = '') -> Path:
    names = validate_source(root)
    if git(root, 'status', '--porcelain', '--untracked-files=normal'):
        raise ValueError('Build requires a clean checkout; commit the intended source first')
    sha = git(root, 'rev-parse', 'HEAD')
    version = version or 'dev-' + sha[:12]
    if not VERSION.fullmatch(version) and version != 'dev-' + sha[:12]:
        raise ValueError('Invalid release version')
    files = {}
    for name in names:
        if name in ROOT_FILES or name.startswith(PREFIXES):
            files[name] = ((root / name).read_bytes(), 0o755 if os.access(root / name, os.X_OK) else 0o644)
    package = root / 'node_modules/lucide'
    lock = json.loads((root / 'package-lock.json').read_text())
    installed = json.loads((package / 'package.json').read_text())
    expected = lock['packages']['node_modules/lucide']['version']
    if installed['version'] != expected:
        raise ValueError('Installed Lucide differs from package-lock.json; run npm ci')
    required = ['package.json', 'LICENSE', 'dist/esm/iconsAndAliases.js', 'dist/umd/lucide.min.js']
    for name in required:
        if not (package / name).is_file():
            raise ValueError('Missing Lucide asset: ' + name)
    for path in sorted(package.rglob('*')):
        relative = path.relative_to(package).as_posix()
        if path.is_symlink():
            raise ValueError('Symlink in Lucide package: ' + relative)
        if path.is_file() and (relative in required or relative.startswith('dist/esm/')):
            files['node_modules/lucide/' + relative] = (path.read_bytes(), 0o644)
    manifest = {
        'schema_version': 1, 'project': 'Paper Compass', 'version': version, 'commit': sha,
        'source_date_epoch': int(git(root, 'show', '-s', '--format=%ct', 'HEAD')),
        'lucide_version': expected,
        'files': {name: hashlib.sha256(value[0]).hexdigest() for name, value in sorted(files.items())},
    }
    encoded = (json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + '\n').encode()
    files['RELEASE_MANIFEST.json'] = (encoded, 0o644)
    output.mkdir(parents=True, exist_ok=True)
    prefix = 'paper-compass-' + version
    archive = output / (prefix + '.tar.gz')
    if archive.exists() or (output / 'RELEASE_MANIFEST.json').exists():
        raise ValueError('Output already contains a release; use a fresh output directory')
    with archive.open('wb') as target, gzip.GzipFile(fileobj=target, filename='', mode='wb', mtime=0) as compressed:
        with tarfile.open(fileobj=compressed, mode='w') as tar:
            for name, (data, mode) in sorted(files.items()):
                # Only the reviewed, locked dependency may enter node_modules.
                if unsafe_path(name) and not name.startswith('node_modules/lucide/'):
                    raise ValueError('Unsafe archive entry: ' + name)
                if SECRET.search(data):
                    raise ValueError('Possible credential in archive entry: ' + name)
                info = tarfile.TarInfo(prefix + '/' + name)
                info.size, info.mode, info.mtime = len(data), mode, manifest['source_date_epoch']
                tar.addfile(info, io.BytesIO(data))
    (output / 'RELEASE_MANIFEST.json').write_bytes(encoded)
    checksums = ''.join(hashlib.sha256(path.read_bytes()).hexdigest() + '  ' + path.name + '\n'
                        for path in (archive, output / 'RELEASE_MANIFEST.json'))
    (output / 'SHA256SUMS').write_text(checksums, encoding='utf-8')
    return archive


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=Path('dist'))
    parser.add_argument('--version', default='')
    parser.add_argument('--validate-tag')
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    if args.validate_tag:
        values = validate_tag(root, args.validate_tag)
        if os.getenv('GITHUB_OUTPUT'):
            with open(os.environ['GITHUB_OUTPUT'], 'a', encoding='utf-8') as output:
                output.write(''.join(key + '=' + value + '\n' for key, value in values.items()))
        print(json.dumps(values))
    else:
        print(build(root, args.output.resolve(), args.version))


if __name__ == '__main__':
    main()
