"""端到端：排名变化判定、三态区分、广告位、回退、幂等、可追溯。"""
from conftest_support import run_full
from serptrack import models
from serptrack.explain import explain, integrity_counts
from serptrack import keyer, snapshot as snap_mod


def _ex(con, keyword, site, geo="US", lang="en", device="desktop"):
    return explain(con, keyword=keyword, site_id=site, geo=geo,
                   lang=lang, device=device)


def test_generations_publish_only_after_all_confirmed(tmp_path):
    con, stats = run_full(tmp_path)
    for g in stats["generations"]:
        assert g["status"]["all_confirmed"] is True
        row = con.execute("SELECT status FROM generations WHERE gen_id=?",
                          (g["gen_id"],)).fetchone()
        assert row["status"] == models.GEN_PUBLISHED
    assert con.execute("SELECT COUNT(*) FROM rank_history WHERE published=1"
                       ).fetchone()[0] == 36
    con.close()


def test_real_organic_decline_is_reported(tmp_path):
    con, _ = run_full(tmp_path)
    r = _ex(con, "best vpn", "zephyr")
    assert r["comparable"] is True and r["verdict"] == "rank_declined"
    assert "5 → 7" in r["reason"]
    # 依据可追溯到两个快照
    prev = con.execute("SELECT * FROM rank_history WHERE snapshot_id=?",
                       (r["evidence"]["previous_snapshot"],)).fetchone()
    cur = con.execute("SELECT * FROM rank_history WHERE snapshot_id=?",
                      (r["evidence"]["latest_snapshot"],)).fetchone()
    assert prev["gen_id"] == "g1" and cur["gen_id"] == "g2"
    con.close()


def test_ad_insertion_does_not_look_like_organic_decline(tmp_path):
    con, _ = run_full(tmp_path)
    r = _ex(con, "best vpn", "acme")
    assert r["comparable"] is True and r["verdict"] == "rank_unchanged"
    assert "广告不参与自然排名" in r["reason"]
    assert r["generations"][1]["ad_positions"] == [2]  # 广告位单独记录
    con.close()


def test_cross_page_movement_and_disappearance(tmp_path):
    con, _ = run_full(tmp_path)
    # nimbus cheap flights: g1 自然位 [2,4]（跨页保留两次出现），g2 完整快照中消失
    r = _ex(con, "cheap flights", "nimbus")
    assert r["verdict"] == "disappeared"
    g1 = r["generations"][0]
    assert g1["organic_positions"] == [2, 4]
    counts = integrity_counts(con, keyword="cheap flights", site_id="nimbus",
                              geo="US", lang="en", device="desktop")
    assert counts[models.NOT_APPEARED] == 1 and counts[models.RANKED] == 1
    con.close()


def test_not_appeared_vs_unknown_are_distinct(tmp_path):
    con, _ = run_full(tmp_path)
    # acme 在 vpn review：g1 上榜，g2 截断 → unknown，不可比较而非“下降”
    r = _ex(con, "vpn review", "acme")
    assert r["comparable"] is False and r["verdict"] == "not_comparable"
    assert "快照不完整" in r["reason"] and "truncated" in r["reason"]
    counts = integrity_counts(con, keyword="vpn review", site_id="acme",
                              geo="US", lang="en", device="desktop")
    assert counts[models.UNKNOWN] == 1 and counts[models.RANKED] == 1
    assert counts[models.NOT_APPEARED] == 0
    con.close()


def test_never_present_in_complete_snapshot_is_not_appeared(tmp_path):
    con, _ = run_full(tmp_path)
    r = _ex(con, "cheap flights", "acme")
    assert r["verdict"] == "still_absent"
    counts = integrity_counts(con, keyword="cheap flights", site_id="acme",
                              geo="US", lang="en", device="desktop")
    assert counts[models.NOT_APPEARED] == 2
    con.close()


def test_fallback_data_lives_under_actual_context(tmp_path):
    con, _ = run_full(tmp_path)
    # DE/de/mobile 回退到 EU/de/mobile：EU 历史有数据，DE 原条件查不到
    r_eu = _ex(con, "best vpn", "acme", geo="EU", lang="de", device="mobile")
    assert r_eu["comparable"] is True and r_eu["verdict"] == "rank_declined"
    assert r_eu["evidence"]["context"]["fallback_applied"] is True
    r_de = _ex(con, "best vpn", "acme", geo="DE", lang="de", device="mobile")
    assert r_de["verdict"] == "no_published_data"
    # 不允许回退数据混进 DE 历史键
    assert con.execute(
        "SELECT COUNT(*) FROM rank_history WHERE act_geo='DE'").fetchone()[0] == 0
    assert con.execute(
        "SELECT COUNT(*) FROM rank_history WHERE req_geo='DE' AND act_geo='EU'"
    ).fetchone()[0] == 18  # 3 关键词 × 3 站点 × 2 代次
    con.close()


def test_async_only_incomplete_baseline_blocks_comparison(tmp_path):
    con, _ = run_full(tmp_path)
    r = _ex(con, "vpn review", "acme", geo="EU", lang="de", device="mobile")
    assert r["verdict"] == "not_comparable"
    assert "infinite-feed" in r["reason"]
    con.close()


def test_rerun_is_idempotent_no_new_rank_points_or_snapshots(tmp_path):
    con, _ = run_full(tmp_path)
    snap_before = con.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0]
    rank_before = con.execute("SELECT COUNT(*) FROM rank_history").fetchone()[0]
    entry_before = con.execute("SELECT COUNT(*) FROM parsed_entries").fetchone()[0]
    from serptrack.runner import run_domain
    from conftest_support import normalized_matrix, default_fetcher
    run_domain(con, normalized_matrix(), default_fetcher())
    assert con.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0] == snap_before
    assert con.execute("SELECT COUNT(*) FROM rank_history").fetchone()[0] == rank_before
    assert con.execute("SELECT COUNT(*) FROM parsed_entries").fetchone()[0] == entry_before
    con.close()


def test_snapshot_and_rank_history_are_mutually_traceable(tmp_path):
    con, _ = run_full(tmp_path)
    r = _ex(con, "best vpn", "zephyr")
    traced = snap_mod.trace_snapshot(con, r["evidence"]["latest_snapshot"])
    assert traced["snapshot"]["keyword"] == "best vpn"
    assert traced["pages"], "快照必须能回溯原始页面"
    hosts = {p["host"] for p in snap_mod.entries_for(
        con, r["evidence"]["latest_snapshot"])}
    assert "zephyr.example" in hosts
    # 反向：该快照下存在指向它的 zephyr 排名点
    point = con.execute(
        "SELECT * FROM rank_history WHERE snapshot_id=? AND site_id='zephyr'",
        (r["evidence"]["latest_snapshot"],)).fetchone()
    assert point is not None and point["site_id"] == "zephyr"
    con.close()


def test_context_key_dimensions_are_all_persisted(tmp_path):
    con, _ = run_full(tmp_path)
    rows = con.execute("SELECT DISTINCT keyword, act_geo, act_lang, act_device, "
                       "req_geo, req_lang, req_device FROM rank_history").fetchall()
    keys = {(r["keyword"], r["act_geo"], r["act_lang"], r["act_device"]) for r in rows}
    assert ("best vpn", "US", "en", "desktop") in keys
    assert ("best vpn", "EU", "de", "mobile") in keys
    con.close()
