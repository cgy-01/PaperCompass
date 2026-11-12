from paper_firehose.commands.conference_metadata import _MetadataParser


def _parse(html: str):
    parser = _MetadataParser()
    parser.feed(html)
    return parser.result()


def test_parses_citation_authors_and_abstract_container():
    authors, abstract = _parse(
        '<meta name="citation_author" content="Ada Lovelace">'
        '<meta name="citation_author" content="Alan Turing">'
        '<div id="abstract"><p>This is a sufficiently long official abstract containing methods, '
        'experiments, evidence, findings, and limitations for the paper.</p></div>'
    )
    assert authors == "Ada Lovelace, Alan Turing"
    assert abstract.startswith("This is a sufficiently long official abstract")


def test_parses_heading_followed_by_paragraph():
    _, abstract = _parse(
        "<h4>Abstract</h4><p>This abstract follows a heading and contains enough useful text "
        "to be retained as conference metadata for display in the dashboard.</p>"
    )
    assert abstract.startswith("This abstract follows a heading")


def test_parses_usenix_visible_authors_without_affiliations():
    authors, _ = _parse(
        '<div class="field field-name-field-paper-people-text">'
        '<div><p>Anuj Kalia, <em>Microsoft;</em> Nikita Lazarev, <em>MIT;</em> '
        'Xenofon Foukas and Bozidar Radunovic, <em>Microsoft</em></p></div></div>'
    )
    assert authors == "Anuj Kalia, Nikita Lazarev, Xenofon Foukas and Bozidar Radunovic"
