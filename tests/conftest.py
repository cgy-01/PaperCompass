"""Never let a developer's paid API credentials or runtime settings leak into tests."""
import pytest

from paper_firehose.core.environment import ENV_KEYS


@pytest.fixture(autouse=True)
def isolated_business_environment(monkeypatch, tmp_path):
    for key in ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv('PAPER_COMPASS_CLASSIFIER', 'keywords')
    monkeypatch.setenv('PAPER_FIREHOSE_DATA_DIR', str(tmp_path / 'runtime'))
