"""Read-only web application for browsing the Paper Compass database."""

from __future__ import annotations

import json
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
from .query_store import PublishedRepository
from .core.paths import resolve_data_path


LOGGER = logging.getLogger(__name__)


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
            "file": "MCP_USER_GUIDE.md",
            "title": "MCP 用户指南",
            "description": "连接 Codex、Claude Code，并让 Agent 使用 Paper Compass 调研论文。",
            "icon": "plug-zap",
        },
    )

    def __init__(self, docs_dir: Path | None = None):
        configured = os.getenv("PAPER_COMPASS_DOCS_DIR")
        self.docs_dir = docs_dir or (
            Path(configured).expanduser() if configured
            else Path(__file__).resolve().parents[2] / "docs"
        )
        self._by_slug = {item["slug"]: item for item in self.CATALOG}

    def list(self) -> list[dict[str, Any]]:
        return [
            {key: value for key, value in item.items() if key != "file"}
            for item in self.CATALOG
            if (self.docs_dir / item["file"]).is_file()
        ]

    def get(self, slug: str) -> dict[str, Any] | None:
        item = self._by_slug.get(slug)
        if item is None:
            return None
        path = self.docs_dir / item["file"]
        if not path.is_file():
            return None
        source = path.read_text(encoding="utf-8")
        html = markdown.markdown(
            source,
            extensions=["fenced_code", "tables", "sane_lists"],
            output_format="html5",
        )
        # Keep links between allowlisted manuals inside the product document view.
        internal_links = {entry["file"]: entry["slug"] for entry in self.CATALOG}
        for filename, target_slug in internal_links.items():
            html = re.sub(
                rf'href="{re.escape(filename)}"',
                f'href="#" data-doc-slug="{target_slug}"',
                html,
            )
        # Serve Markdown images from docs/img without copying them into runtime HTML.
        html = re.sub(
            r'src="(?:\./)?(img/[^"?#]+)([?#][^"]*)?"',
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

        def _json(self, value: Any, status: HTTPStatus = HTTPStatus.OK) -> None:
            payload = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Cache-Control", "private, max-age=60")
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
            if parsed.path == "/api/bootstrap":
                repository = self._paper_repository()
                if repository is not None:
                    self._json(repository.bootstrap())
                return
            if parsed.path == "/api/papers":
                repository = self._paper_repository()
                if repository is not None:
                    self._json(repository.query(parse_qs(parsed.query)))
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
