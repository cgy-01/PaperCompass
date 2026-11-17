# Archived workflows

These files retain the former data/Pages and manual PyPI workflows for reference.
They are outside `.github/workflows/` and have `.disabled` suffixes, so GitHub
Actions cannot run them. They are not part of application delivery.

Daily development is checked by `workflows/tests.yml`. Only a deliberately
created version tag (or a manual run selecting an existing tag) can publish a
GitHub Release through `workflows/release.yml`. Neither workflow connects to a
server, collects papers, sends email, or publishes to PyPI/Pages.
