Usage Guide
===========

.. note::

   Audience: project developers using the Paper Compass CLI and Python package.
   This document is not published in the Paper Compass website user manual.

Installation
------------

Install from the Paper Compass source checkout. See README.md for the full
configuration, icon assets and first-run sequence:

.. code-block:: bash

   python -m pip install -e .
   npm ci

Current application entry points
-------------------------------

.. code-block:: bash

   paper-firehose status
   paper-firehose build-query
   paper-firehose serve
   paper-firehose update --scope arxiv
   paper-firehose update --scope conferences
   paper-firehose mcp

Set PAPER_FIREHOSE_DATA_DIR consistently before using these commands. Detailed
configuration is documented in configuration.md, and production scheduling in
admin/automatic_updates.md. The commands below also cover optional processing
tools; they are not all required by the website's automatic update pipeline.

Runtime Data Directory
----------------------

The application stores runtime state (SQLite databases, generated HTML,
secrets, templates) beneath the path in ``$PAPER_FIREHOSE_DATA_DIR``.
When unset, a platform-appropriate directory is created automatically.
Within that tree you will find:

* ``config/`` – YAML configuration files for global defaults and per-topic
  overrides. The ``ConfigManager`` API is the authoritative source for
  reading/writing these files.
* ``papers.db`` – current pipeline database populated by the ``filter``,
  ``rank``, ``abstracts`` and ``summarize`` commands.
* ``matched_entries_history.db`` – append-only history of matched entries.
* ``all_feed_entries.db`` – deduplicated store of every seen RSS item.


Command Line Interface
----------------------

The :mod:`paper_firehose.cli` module exposes the main pipeline commands.
Examples:

.. code-block:: bash

   # Fetch feeds and filter by topic configuration
   paper-firehose filter --topic condensed_matter

   # Rank filtered entries using sentence-transformers
   paper-firehose rank --topic condensed_matter

   # Fetch abstracts for highly-ranked entries
   paper-firehose abstracts --topic condensed_matter --mailto you@example.com

   # Generate HTML output from the current databases
   paper-firehose html


Programmatic Entry Points
-------------------------

The package exports convenience functions at the top level. They are thin
wrappers around the command implementations and make orchestration from
Python straightforward:

.. code-block:: python

   from paper_firehose import filter, rank, abstracts, summarize

   filter(topic="quantum")
   rank(topic="quantum")
   abstracts(topic="quantum", mailto="you@example.com")
   summarize(topic="quantum")

For more granular control import the modules within :mod:`paper_firehose.commands`
and :mod:`paper_firehose.core`. They expose utilities for managing configuration,
databases, HTML rendering, and email delivery.
