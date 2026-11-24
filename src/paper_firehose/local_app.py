"""Read-only paper catalog with independent account and activity services."""

from __future__ import annotations

import json
import gzip
import io
import logging
import mimetypes
import os
import re
import threading
import time
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

import markdown

from .commands import dashboard
from .accounts import AccountAPI
from .query_store import PublishedRepository, present_paper
from .meili_search import SearchUnavailable
from .core.paths import resolve_data_path


LOGGER = logging.getLogger(__name__)


def _accepts_gzip(value: str) -> bool:
    qualities = {}
    for item in value.lower().split(','):
        name, *parameters = item.strip().split(';')
        quality = 1.0
        for parameter in parameters:
            if parameter.strip().startswith('q='):
                try:
                    quality = float(parameter.strip()[2:])
                except ValueError:
                    quality = 0
        qualities[name] = quality
    return 0 < qualities.get('gzip', qualities.get('*', 0)) <= 1


def _immutable_asset_path(path: str) -> bool:
    parts = unquote(path).split('/')
    return (len(parts) > 3 and parts[:2] == ['', 'assets']
            and bool(re.fullmatch(r'[a-f0-9]{20}', parts[2]))
            and all(part not in ('', '.', '..') and '\\' not in part for part in parts[3:]))


class PaperRepository(PublishedRepository):
    """Load an already published read model without scanning the raw catalog."""

    def __init__(self, config_path: str):
        # Keep the existing constructor signature; publication is an admin operation.
        try:
            super().__init__()
        except FileNotFoundError as exc:
            raise RuntimeError('No published query database; run paper-firehose --config '
                               + config_path + ' build-query before starting the service') from exc
        self._bootstrap = super().bootstrap()

    def bootstrap(self):
        return self._bootstrap


class DocumentationRepository:
    """Render a small, explicit allowlist of trusted project documentation."""

    CATALOG = (
        {
            "slug": "mcp",
            "file": "user/MCP_USER_GUIDE.md",
            "title": "MCP 用户指南",
            "description": "连接 Codex、Claude Code，并让 Agent 使用 Paper Compass 调研论文。",
            "icon": "plug-zap",
        },
        {
            "slug": "account",
            "file": "user/ACCOUNT_USER_GUIDE.md",
            "title": "账号与文献库",
            "description": "登录、同步收藏、导入本机记录与使用情况设置。",
            "icon": "user-round",
        },
    )

    def __init__(self, docs_dir: Path | None = None):
        configured = os.getenv("PAPER_COMPASS_DOCS_DIR")
        self.docs_dir = docs_dir or (
            Path(configured).expanduser() if configured
            else Path(__file__).resolve().parents[2] / "docs"
        )
        self._by_slug = {item["slug"]: item for item in self.CATALOG}

    def _document_path(self, item: dict[str, str]) -> Path:
        path = self.docs_dir / item["file"]
        if path.is_file():
            return path
        # Preserve flat custom documentation directories used by older instances.
        return self.docs_dir / Path(item["file"]).name

    def list(self) -> list[dict[str, Any]]:
        return [
            {key: value for key, value in item.items() if key != "file"}
            for item in self.CATALOG
            if self._document_path(item).is_file()
        ]

    def get(self, slug: str) -> dict[str, Any] | None:
        item = self._by_slug.get(slug)
        if item is None:
            return None
        path = self._document_path(item)
        if not path.is_file():
            return None
        source = path.read_text(encoding="utf-8")
        html = markdown.markdown(
            source,
            extensions=["fenced_code", "tables", "sane_lists"],
            output_format="html5",
        )
        # Keep links between allowlisted manuals inside the product document view.
        internal_links = {Path(entry["file"]).name: entry["slug"] for entry in self.CATALOG}
        for filename, target_slug in internal_links.items():
            html = re.sub(
                rf'href="(?:\./)?{re.escape(filename)}"',
                f'href="#" data-doc-slug="{target_slug}"',
                html,
            )
        # Serve Markdown images from docs/img without copying them into runtime HTML.
        html = re.sub(
            r'src="(?:\./|\.\./)?(img/[^"?#]+)([?#][^"]*)?"',
            lambda match: (
                f'src="/api/docs/assets/{match.group(1)}{match.group(2) or ""}"'
            ),
            html,
        )
        return {
            **{key: value for key, value in item.items() if key != "file"},
            "html": html,
        }

    def asset(self, relative_path: str) -> Path | None:
        """Resolve a public documentation image while preventing path traversal."""
        asset_root = (self.docs_dir / "img").resolve()
        requested = (self.docs_dir / unquote(relative_path)).resolve()
        if not requested.is_relative_to(asset_root) or not requested.is_file():
            return None
        return requested


class LocalApplication:
    """Read-only application facade retained for the local and server CLI."""

    def __init__(self, config_path: str, html_dir: Path):
        self.config_path = config_path
        self.html_dir = html_dir
        self.repository: PaperRepository | None = None
        self.repository_error: str | None = None
        self.documentation = DocumentationRepository()
        self._reload_lock = threading.Lock()
        self._account_lock = threading.Lock()
        self.accounts: AccountAPI | None = None

    def account_api(self):
        with self._account_lock:
            if self.accounts is None:
                self.accounts = AccountAPI()
            return self.accounts

    def load_repository(self) -> None:
        """Open the published database while retaining the previous version on error."""
        try:
            self.repository = PaperRepository(self.config_path)
            self.repository_error = None
            LOGGER.info("Paper Compass query database ready: %s papers", self.repository.bootstrap()['overview']['total'])
        except Exception as exc:  # pragma: no cover - surfaced through the health API
            self.repository_error = str(exc)
            LOGGER.exception("Paper Compass paper index failed to load")

    def start_repository_loader(self) -> threading.Thread:
        def watch():
            marker = resolve_data_path('query-store', 'current.json')
            version = marker.stat().st_mtime_ns if marker.exists() else None
            self.load_repository()
            while True:
                time.sleep(15)
                try:
                    current = marker.stat().st_mtime_ns if marker.exists() else None
                    if current != version:
                        with self._reload_lock:
                            self.load_repository()
                        if self.repository_error is None:
                            version = current
                except OSError:
                    LOGGER.exception('Failed to inspect paper update marker')
                if self.accounts is not None:
                    try:
                        self.accounts.events.maintain()
                    except Exception:
                        LOGGER.exception('Activity retention maintenance failed')

        worker = threading.Thread(
            target=watch,
            name="paper-compass-index-loader",
            daemon=True,
        )
        worker.start()
        return worker


def _handler(app: LocalApplication):
    class Handler(SimpleHTTPRequestHandler):
        def __init__(self, *args: Any, **kwargs: Any):
            super().__init__(*args, directory=str(app.html_dir), **kwargs)

        def send_response(self, code, message=None):
            self._response_status = code
            super().send_response(code, message)

        def end_headers(self) -> None:
            path = urlparse(self.path).path
            if path.startswith('/assets/'):
                immutable = _immutable_asset_path(path) and self._response_status in (200, 304)
                self.send_header('Cache-Control', 'public, max-age=31536000, immutable' if immutable else 'no-cache')
            elif path in ('/', '/index.html', '/lucide.min.js'):
                self.send_header('Cache-Control', 'no-cache')
            super().end_headers()

        def send_head(self):
            """Serve negotiated compressed assets without changing legacy URLs.

            Versioned sidecars are precompressed at publication. HTML and legacy
            aliases are compressed on demand, avoiding stale mutable sidecars.
            """
            path = Path(self.translate_path(self.path))
            if not path.is_file() or path.suffix not in ('.html', '.js', '.css', '.json', '.svg', '.txt'):
                return super().send_head()
            stat = path.stat()
            compressed = stat.st_size >= 1024 and _accepts_gzip(self.headers.get('Accept-Encoding', ''))
            etag = f'"{stat.st_mtime_ns:x}-{stat.st_size:x}{"-gzip" if compressed else ""}"'
            matches = [tag.strip().removeprefix('W/') for tag in self.headers.get('If-None-Match', '').split(',')]
            if etag in matches or '*' in matches:
                self.send_response(HTTPStatus.NOT_MODIFIED)
                self.send_header('ETag', etag)
                self.send_header('Vary', 'Accept-Encoding')
                self.end_headers()
                return None
            sidecar = path.with_name(path.name + '.gz')
            immutable = _immutable_asset_path(urlparse(self.path).path)
            if compressed and immutable and sidecar.is_file():
                payload = sidecar.read_bytes()
            else:
                payload = path.read_bytes()
                if compressed:
                    payload = gzip.compress(payload, compresslevel=6, mtime=0)
            self.send_response(HTTPStatus.OK)
            self.send_header('Content-Type', self.guess_type(str(path)))
            self.send_header('Content-Length', str(len(payload)))
            self.send_header('ETag', etag)
            self.send_header('Last-Modified', self.date_time_string(stat.st_mtime))
            self.send_header('Vary', 'Accept-Encoding')
            if compressed:
                self.send_header('Content-Encoding', 'gzip')
            self.end_headers()
            return io.BytesIO(payload)

        def log_request(self, code='-', size='-'):
            if urlparse(self.path).path.startswith('/auth/'):
                self.log_message('"%s %s" %s %s', self.command, urlparse(self.path).path, code, size)
            else:
                super().log_request(code, size)

        def _user_api(self, method, parsed):
            if not (parsed.path.startswith('/api/account') or parsed.path.startswith('/auth/github/') or parsed.path == '/api/events'):
                return False
            try:
                return app.account_api().handle(self, method, parsed.path, parse_qs(parsed.query))
            except Exception:
                LOGGER.exception('Account service unavailable')
                self._json({'error': 'account service temporarily unavailable'}, HTTPStatus.SERVICE_UNAVAILABLE, cache_control='no-store')
                return True

        def _json(self, value: Any, status: HTTPStatus = HTTPStatus.OK, *, headers=(), cache_control='private, max-age=60') -> None:
            if status >= 400:
                cache_control = 'no-store'
            payload = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            # Never compress authenticated/session responses. Public paper data
            # benefits even when Nginx has no JSON gzip configuration enabled.
            compressible = cache_control != 'no-store'
            compressed = compressible and len(payload) >= 1024 and _accepts_gzip(self.headers.get('Accept-Encoding', ''))
            if compressed:
                payload = gzip.compress(payload, compresslevel=6, mtime=0)
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Cache-Control", cache_control)
            if compressible:
                self.send_header('Vary', 'Accept-Encoding')
            if compressed:
                self.send_header('Content-Encoding', 'gzip')
            for key, value in headers:
                self.send_header(key, value)
            self.end_headers()
            self.wfile.write(payload)

        def _file(self, path: Path) -> None:
            payload = path.read_bytes()
            content_type, _ = mimetypes.guess_type(path.name)
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", content_type or "application/octet-stream")
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Cache-Control", "public, max-age=3600")
            self.end_headers()
            self.wfile.write(payload)

        def _paper_repository(self) -> PaperRepository | None:
            repository = app.repository
            if repository is not None:
                return repository
            if app.repository_error:
                self._json(
                    {"error": "paper index failed", "detail": app.repository_error},
                    HTTPStatus.INTERNAL_SERVER_ERROR,
                )
            else:
                self._json(
                    {"error": "paper index loading", "retryAfter": 2},
                    HTTPStatus.SERVICE_UNAVAILABLE,
                )
            return None

        def do_GET(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            if self._user_api('GET', parsed):
                return
            if parsed.path.startswith('/api/v1/'):
                repository = self._paper_repository()
                if repository is None:
                    return
                try:
                    if not repository.path.is_file():
                        repository = PublishedRepository()
                    params = parse_qs(parsed.query)
                    version = params.pop('version', [''])[0]
                    if version:
                        if not re.fullmatch(r'[a-f0-9]{32}', version):
                            self._json({'error': 'invalid data version'}, HTTPStatus.BAD_REQUEST)
                            return
                        path = repository.path.parent / ('query-' + version + '.db')
                        if not path.is_file():
                            self._json({'error': 'data version expired; reload the list'}, HTTPStatus.CONFLICT)
                            return
                        if path != repository.path:
                            repository = PublishedRepository(path)
                    data_version = repository.path.stem.removeprefix('query-')
                    if parsed.path == '/api/v1/bootstrap':
                        data = repository.bootstrap()
                        data = {**data, 'overview': {**data['overview'], 'latest': [
                            present_paper(p, compact=True) for p in data['overview']['latest']]},
                            'dataVersion': data_version}
                        self._json(data)
                    elif parsed.path == '/api/v1/papers':
                        try:
                            for key in ('page', 'page_size', 'days'):
                                if key in params and int(params[key][0]) < (0 if key == 'days' else 1):
                                    raise ValueError(key)
                            if params.get('sort', ['date'])[0] not in ('date', 'score', 'title', 'similarity'):
                                raise ValueError('sort')
                            if params.get('search_field', ['all'])[0] not in ('all', 'title'):
                                raise ValueError('search_field')
                            if len(params.get('q', [''])[0].strip()) > 4096:
                                raise ValueError('q')
                        except ValueError:
                            self._json({'error': 'invalid pagination or sort parameters'}, HTTPStatus.BAD_REQUEST)
                            return
                        data = repository.query(params, pinned=True)
                        self._json({**data, 'items': [present_paper(p, compact=True) for p in data['items']],
                                    'dataVersion': data_version})
                    elif parsed.path.startswith('/api/v1/papers/'):
                        paper = repository.detail(parsed.path.removeprefix('/api/v1/papers/'), pinned=True)
                        if paper is None:
                            self._json({'error': 'paper not found'}, HTTPStatus.NOT_FOUND)
                        else:
                            self._json({'paper': paper, 'dataVersion': data_version})
                    else:
                        self._json({'error': 'not found'}, HTTPStatus.NOT_FOUND)
                except FileNotFoundError:
                    self._json({'error': 'data version expired; reload the list'}, HTTPStatus.CONFLICT)
                except SearchUnavailable:
                    self._json({'error': 'search temporarily unavailable'}, HTTPStatus.SERVICE_UNAVAILABLE)
                except ValueError:
                    self._json({'error': 'invalid search parameters'}, HTTPStatus.BAD_REQUEST)
                return
            if parsed.path == "/api/bootstrap":
                repository = self._paper_repository()
                if repository is not None:
                    self._json(repository.bootstrap())
                return
            if parsed.path == "/api/papers":
                repository = self._paper_repository()
                if repository is not None:
                    try:
                        self._json(repository.query(parse_qs(parsed.query)))
                    except SearchUnavailable:
                        self._json({'error': 'search temporarily unavailable'}, HTTPStatus.SERVICE_UNAVAILABLE)
                    except ValueError:
                        self._json({'error': 'invalid search parameters'}, HTTPStatus.BAD_REQUEST)
                return
            if parsed.path == "/api/health":
                state = "ready" if app.repository else "failed" if app.repository_error else "loading"
                self._json({"status": state}, HTTPStatus.OK if state == "ready" else HTTPStatus.SERVICE_UNAVAILABLE)
                return
            if parsed.path == "/api/docs":
                self._json({"documents": app.documentation.list()})
                return
            if parsed.path.startswith("/api/docs/assets/"):
                relative_path = parsed.path.removeprefix("/api/docs/assets/")
                asset = app.documentation.asset(relative_path)
                if asset is None:
                    self._json({"error": "document asset not found"}, HTTPStatus.NOT_FOUND)
                else:
                    self._file(asset)
                return
            if parsed.path.startswith("/api/docs/"):
                slug = parsed.path.removeprefix("/api/docs/").strip("/")
                document = app.documentation.get(slug)
                if document is None:
                    self._json({"error": "document not found"}, HTTPStatus.NOT_FOUND)
                else:
                    self._json(document)
                return
            if parsed.path.startswith("/api/"):
                self._json({"error": "not found"}, HTTPStatus.NOT_FOUND)
                return
            if parsed.path == "/":
                self.path = "/index.html"
            super().do_GET()

        def do_POST(self) -> None:  # noqa: N802
            if self._user_api('POST', urlparse(self.path)):
                return
            self._json({"error": "read-only service"}, HTTPStatus.METHOD_NOT_ALLOWED)

    return Handler


def serve(config_path: str, host: str = "127.0.0.1", port: int = 8765) -> None:
    """Generate the lightweight shell and serve read-only paper APIs."""
    html_dir = resolve_data_path("html", ensure_parent=True)
    dashboard.run(config_path, output=str(html_dir / "index.html"))
    app = LocalApplication(config_path, html_dir)
    server = ThreadingHTTPServer((host, port), _handler(app))
    app.start_repository_loader()
    LOGGER.info("Paper Compass HTTP server listening on http://%s:%s; paper index is loading", host, port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
