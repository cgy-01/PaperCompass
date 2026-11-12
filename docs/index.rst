Paper Compass Documentation
===========================

This Sphinx site is the internal developer-documentation entry point. Public
website documentation is controlled separately by the explicit allowlist in
``DocumentationRepository.CATALOG``.

Paper Compass provides an AI-paper workspace, incremental arXiv monitoring,
official conference ingestion, versioned SQLite read models, a read-only web API
and MCP integration. The command name remains ``paper-firehose``. Start with the
repository README and the Markdown configuration and development guides; these
Sphinx pages additionally cover optional processing commands and Python APIs.

.. toctree::
   :maxdepth: 2
   :caption: Contents

   usage
   github_actions_setup
   api

User Guides
-----------

The Markdown user-manual index is available at `docs/README.md <README.md>`_.
The Paper Compass MCP guide is available at
`docs/MCP_USER_GUIDE.md <MCP_USER_GUIDE.md>`_.
