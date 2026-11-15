"""Publish immutable frontend releases using the existing locked Lucide package."""
from __future__ import annotations

import gzip
import hashlib
import json
import os
import re
import shutil
import tempfile
from pathlib import Path


def publish(frontend: Path, template: str, package: Path, output: Path,
            topic_icons=()) -> str:
    """Prepare the entire release before exposing its content-addressed URL.

    Relative ESM imports stay inside one release, including lazy-loaded routes.
    Previous releases remain available to tabs opened before a deployment.
    """
    files = {p.relative_to(frontend).as_posix(): p.read_bytes()
             for p in sorted(frontend.rglob('*')) if p.is_file()}
    text = template + '\n' + '\n'.join(
        value.decode('utf-8') for name, value in files.items() if name.endswith('.js'))
    candidates = set(re.findall(r'''["']([a-z][a-z0-9-]*)["']''', text)) | set(topic_icons)
    esm = package / 'dist' / 'esm'
    if not esm.is_dir():
        raise RuntimeError('Lucide assets are missing; run npm ci before starting the dashboard')
    aliases = {}
    exports = (esm / 'iconsAndAliases.js').read_text(encoding='utf-8')
    for match in re.finditer(r"export \{([^}]+)\} from './icons/([^']+)';", exports):
        for alias in re.findall(r'default as (\w+)', match[1]):
            aliases[alias] = match[2]
    nodes = {}
    for name in sorted(candidates):
        pascal = ''.join(part[:1].upper() + part[1:] for part in name.split('-'))
        source = esm / 'icons' / aliases.get(pascal, name + '.js')
        if not source.is_file():
            continue
        match = re.search(r'const \w+ = (\[.*?\]);', source.read_text(encoding='utf-8'), re.S)
        if match is None:
            raise ValueError('Unsupported Lucide icon format: ' + name)
        # Copy official icon data unchanged; never synthesize SVG geometry.
        # Lucide's ESM serializes simple SVG attribute names as JS identifiers.
        # Normalize keys to JSON; never execute package code or use eval.
        literal = re.sub(r'([{,]\s*)([A-Za-z_$][\w$]*)(\s*:)', r'\1"\2"\3', match[1])
        nodes[name] = json.loads(literal)
    required = (set(topic_icons) | set(re.findall(r'''data-lucide=["']([a-z][a-z0-9-]*)["']''', text))
                | set(re.findall(r'''\bicon\(["']([a-z][a-z0-9-]*)["']''', text)))
    if not nodes or required - nodes.keys():
        raise ValueError('The installed Lucide package is missing required icons: ' + ', '.join(sorted(required - nodes.keys())))
    files['icon-data.js'] = (
        '// Lucide — ISC license; see vendor/lucide/LICENSE. Generated at publication.\n'
        'export default ' + json.dumps(nodes, separators=(',', ':')) + ';\n').encode()
    for name in ('replaceElement.js', 'createElement.js', 'defaultAttributes.js'):
        files['vendor/lucide/' + name] = (esm / name).read_bytes()
    files['vendor/lucide/LICENSE'] = (package / 'LICENSE').read_bytes()
    digest = hashlib.sha256()
    for name, value in sorted(files.items()):
        digest.update(name.encode() + b'\0' + value + b'\0')
    version = digest.hexdigest()[:20]
    assets = output / 'assets'
    assets.mkdir(parents=True, exist_ok=True)
    release = assets / version
    if not release.exists():
        staging = Path(tempfile.mkdtemp(prefix='.build-', dir=assets))
        try:
            for name, value in files.items():
                destination = staging / name
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(value)
                if name.endswith(('.js', '.css')) and len(value) >= 1024:
                    destination.with_name(destination.name + '.gz').write_bytes(
                        gzip.compress(value, compresslevel=6, mtime=0))
            try:
                os.rename(staging, release)
            except OSError:  # Another publisher may have completed the same release.
                if not release.is_dir():
                    raise
        finally:
            if staging.exists():
                shutil.rmtree(staging)
    # Keep old unversioned URLs working for existing integrations and local tools.
    # They are revalidated; only content-addressed release URLs are immutable.
    shutil.copytree(release, assets, dirs_exist_ok=True)
    return version
