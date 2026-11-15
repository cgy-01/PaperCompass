"""Normalize presentation without migrating historical paper records."""
import pytest
from paper_firehose.query_store import present_paper


@pytest.mark.parametrize('prefix', [
    'arXiv:2609.12345v1 Announce Type: new Abstract: ',
    'arXiv:cs/0312020v2\nAnnounce Type: replace\nAbstract: ',
    'Announce Type: cross Abstract: ',
    'Abstract: ',
])
def test_transport_metadata_is_removed_before_preview_truncation(prefix):
    paper={'id':'rss-paper','summary':prefix+'A'*400,'insight':'a note'}
    compact=present_paper(paper,compact=True)
    assert compact['summaryPreview']=='A'*320
    assert 'summary' not in compact and 'insight' not in compact
    assert present_paper(paper)['summary']=='A'*400
    assert paper['summary']==prefix+'A'*400
    assert len(compact['publicId'])==64


@pytest.mark.parametrize('text', [
    'Abstract algebra has applications in learning.',
    'We compare arXiv datasets. Announce Type: new is a source field.',
    'arXiv:2609.12345 identifies the comparison baseline.',
    'We propose a new method.',
    '暂无摘要。',
])
def test_scientific_content_is_not_removed(text):
    assert present_paper({'id':'paper','summary':text})['summary']==text
