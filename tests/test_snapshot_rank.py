"""快照、去重归并与排名存储的单元规则。"""
from conftest_support import fresh_con
from serptrack import keyer, models, rankstore, snapshot
from serptrack.dedup import classify_state, summarize_host
from serptrack.models import Block, Completeness, Context
from serptrack.serp_parser import parse_pages
from serptrack.models import PagePayload


def _blocks():
    return [
        Block(models.ORGANIC, "https://acme.example/a", "A", page=1, position=1, ordinal=1),
        Block(models.AD, "https://acme.example/landing", "Ad", page=1, position=1, ordinal=2),
        Block(models.ORGANIC, "https://www.acme.example/a", "A2", page=1, position=2, ordinal=3),
        Block(models.SPECIAL, None, "KP", kind="knowledge_panel", page=1, position=1, ordinal=4),
        Block(models.ORGANIC, "https://other.example/", "O", page=2, position=3, ordinal=5),
    ]


def test_summarize_keeps_organic_and_ad_positions_separate():
    s = summarize_host(_blocks(), "acme.example")
    assert s["found"] is True
    assert s["organic_positions"] == [1, 2]
    assert s["ad_positions"] == [1]
    assert len(s["occurrences"]) == 3  # 同 URL 不同位置全部保留
    assert all(o["block_type"] != models.SPECIAL for o in s["occurrences"])


def test_classify_three_states():
    found = summarize_host(_blocks(), "acme.example")
    missing = summarize_host(_blocks(), "ghost.example")
    assert classify_state(found, True) == models.RANKED
    assert classify_state(missing, True) == models.NOT_APPEARED
    assert classify_state(missing, False) == models.UNKNOWN


def test_snapshot_save_is_idempotent_and_traceable(tmp_path):
    con = fresh_con(tmp_path)
    pages = [PagePayload("g1", "k", "US", "en", "desktop", 1, True, False, [], [
        {"type": "organic", "url": "https://acme.example/a", "title": "A"}])]
    parsed = parse_pages(pages)
    req = Context("US", "en", "desktop")
    sid1 = snapshot.save_snapshot(
        con, gen_id="g1", keyword="k", requested=req, actual=req,
        fallback_applied=False, fallback_reason="", pages=pages,
        parsed=parsed, batch_id="b1")
    sid2 = snapshot.save_snapshot(
        con, gen_id="g1", keyword="k", requested=req, actual=req,
        fallback_applied=False, fallback_reason="", pages=pages,
        parsed=parsed, batch_id="b1")
    assert sid1 == sid2
    assert con.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0] == 1
    assert con.execute("SELECT COUNT(*) FROM pages").fetchone()[0] == 1
    assert con.execute("SELECT COUNT(*) FROM parsed_entries").fetchone()[0] == 1
    traced = snapshot.trace_snapshot(con, sid1)
    assert traced["snapshot"]["batch_id"] == "b1"
    con.close()


def test_rank_upsert_is_idempotent_per_gen_context(tmp_path):
    con = fresh_con(tmp_path)
    task = {
        "gen_id": "g1", "keyword": "k", "site_id": "acme", "host": "acme.example",
        "act_geo": "US", "act_lang": "en", "act_device": "desktop",
        "req_geo": "US", "req_lang": "en", "req_device": "desktop",
        "fallback_applied": 0, "fallback_reason": "", "rank_state": models.RANKED,
    }
    common = dict(con=con, task_row=task, snapshot_id="s1", is_complete=True,
                  evidence=["complete"], organic_positions=[1], ad_positions=[],
                  occurrences=[], content_hash="h", config_version="v1")
    rid1 = rankstore.upsert_rank(**common)
    rid2 = rankstore.upsert_rank(**common)  # 重复采集不新增排名点
    assert rid1 == rid2
    assert con.execute("SELECT COUNT(*) FROM rank_history").fetchone()[0] == 1
    # 同代次内容变化时更新而非新增
    task2 = dict(task, rank_state=models.RANKED)
    rankstore.upsert_rank(**{**common, "task_row": task2,
                             "organic_positions": [3], "content_hash": "h2"})
    assert con.execute("SELECT COUNT(*) FROM rank_history").fetchone()[0] == 1
    row = con.execute("SELECT * FROM rank_history WHERE rank_id=?", (rid1,)).fetchone()
    assert row["content_hash"] == "h2" and row["published"] == 0
    # 发布后才对解释层可见
    rankstore.publish_generation(con, "g1")
    series = rankstore.rank_series(
        con, keyword="k", site_id="acme", geo="US", lang="en", device="desktop")
    assert len(series) == 1 and series[0]["organic_positions"] == [3]
    con.close()


def test_distinct_generations_are_distinct_rank_points(tmp_path):
    con = fresh_con(tmp_path)
    for gen in ("g1", "g2"):
        task = {
            "gen_id": gen, "keyword": "k", "site_id": "acme", "host": "acme.example",
            "act_geo": "US", "act_lang": "en", "act_device": "desktop",
            "req_geo": "US", "req_lang": "en", "req_device": "desktop",
            "fallback_applied": 0, "fallback_reason": "", "rank_state": models.RANKED,
        }
        rankstore.upsert_rank(
            con, task_row=task, snapshot_id=f"s-{gen}", is_complete=True,
            evidence=["complete"], organic_positions=[1], ad_positions=[],
            occurrences=[], content_hash=f"h-{gen}", config_version="v1")
    assert con.execute("SELECT COUNT(*) FROM rank_history").fetchone()[0] == 2
    con.close()
