"""页面解析：自然/广告/特殊块分离、同 URL 多位置保留、完整性证据。"""
from serptrack.models import PagePayload
from serptrack.serp_parser import parse_pages
from serptrack import models


def _page(blocks, *, complete=True, truncated=False, async_pending=None, page=1):
    return PagePayload("g", "k", "US", "en", "desktop", page, complete,
                       truncated, async_pending or [], blocks)


def test_block_types_are_separated_with_independent_positions():
    pages = [_page([
        {"type": "ad", "url": "https://a.example/p", "title": "ad1"},
        {"type": "organic", "url": "https://acme.example/1", "title": "o1"},
        {"type": "ad", "url": "https://acme.example/2", "title": "ad2"},
        {"type": "special", "kind": "knowledge_panel", "url": None, "title": "kp"},
        {"type": "organic", "url": "https://acme.example/1", "title": "o2"},
    ])]
    result = parse_pages(pages)
    ads = [b for b in result.blocks if b.block_type == models.AD]
    orgs = [b for b in result.blocks if b.block_type == models.ORGANIC]
    specials = [b for b in result.blocks if b.block_type == models.SPECIAL]
    assert [b.position for b in ads] == [1, 2]
    assert [b.position for b in orgs] == [1, 2]
    assert specials[0].kind == "knowledge_panel"
    # 全块原始序号保留，广告插在自然结果之前也不会挪动自然位
    assert [b.ordinal for b in result.blocks] == [1, 2, 3, 4, 5]


def test_same_url_multiple_positions_are_all_kept():
    pages = [_page([
        {"type": "organic", "url": "https://nimbus.example/x", "title": "a"},
        {"type": "organic", "url": "https://other.example/", "title": "b"},
        {"type": "organic", "url": "https://www.nimbus.example/x", "title": "c"},
    ]), _page([
        {"type": "organic", "url": "https://nimbus.example/y", "title": "d"},
    ], page=2)]
    result = parse_pages(pages)
    nimbus = [b for b in result.blocks
              if b.url and "nimbus.example" in b.url and b.block_type == models.ORGANIC]
    # 跨页连续编号，同主机不同 URL/位置全部保留
    assert [b.position for b in nimbus] == [1, 3, 4]
    assert {b.page for b in nimbus} == {1, 2}
    assert result.completeness.pages_fetched == 2
    assert any(e.startswith("paginated") for e in result.completeness.evidence)


def test_truncated_and_async_pending_mark_incomplete_with_evidence():
    pages = [_page([
        {"type": "organic", "url": "https://x.example/", "title": "x"},
    ], complete=False, truncated=True, async_pending=["lazy-feed"])]
    result = parse_pages(pages)
    assert result.completeness.is_complete is False
    ev = " ".join(result.completeness.evidence)
    assert "truncated" in ev and "async_pending:lazy-feed" in ev
    assert "complete" not in result.completeness.evidence


def test_complete_single_page_carries_full_evidence():
    result = parse_pages([_page([
        {"type": "organic", "url": "https://x.example/", "title": "x"}])])
    assert result.completeness.is_complete is True
    assert "complete" in result.completeness.evidence
    assert any(e.startswith("single_page") for e in result.completeness.evidence)


def test_unknown_block_type_is_quarantined_as_special():
    result = parse_pages([_page([
        {"type": "mystery", "url": "https://x.example/", "title": "?"}])])
    block = result.blocks[0]
    assert block.block_type == models.SPECIAL
    assert block.kind == "unknown:mystery"
