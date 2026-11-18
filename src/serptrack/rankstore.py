"""单一职责：排名历史的幂等写入、发布与查询。

- 排名点按 UNIQUE(gen_id, context_key) 幂等：重复采集不新增排名点，
  同代次同内容重放为 no-op。
- 历史只按 *实际条件* 的 context_key 串联；请求条件仅作证据列保存，
  回退产生的数据不会进入原请求条件的历史。
- 完整代次整体发布（published=1）；解释层只读已发布代次，
  旧批次响应因在 snapshot 层被拒绝，永远无法覆盖新批次排名点。
"""
import json

from . import keyer, models


def upsert_rank(con, *, task_row, snapshot_id, is_complete, evidence,
                organic_positions, ad_positions, occurrences, content_hash,
                config_version) -> str:
    ctx_key = keyer.context_key(
        task_row["keyword"], task_row["site_id"],
        task_row["act_geo"], task_row["act_lang"], task_row["act_device"],
    )
    rid = keyer.rank_id(task_row["gen_id"], ctx_key)
    state = task_row["rank_state"]
    con.execute(
        "INSERT INTO rank_history(rank_id, gen_id, config_version, context_key, "
        "keyword, site_id, host, act_geo, act_lang, act_device, "
        "req_geo, req_lang, req_device, fallback_applied, fallback_reason, "
        "rank_state, organic_positions, ad_positions, occurrences_json, "
        "is_complete, evidence_json, snapshot_id, published, content_hash) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,0,?) "
        "ON CONFLICT(gen_id, context_key) DO UPDATE SET "
        "rank_state=excluded.rank_state, organic_positions=excluded.organic_positions, "
        "ad_positions=excluded.ad_positions, occurrences_json=excluded.occurrences_json, "
        "is_complete=excluded.is_complete, evidence_json=excluded.evidence_json, "
        "snapshot_id=excluded.snapshot_id, content_hash=excluded.content_hash",
        (rid, task_row["gen_id"], config_version, ctx_key,
         task_row["keyword"], task_row["site_id"], task_row["host"],
         task_row["act_geo"], task_row["act_lang"], task_row["act_device"],
         task_row["req_geo"], task_row["req_lang"], task_row["req_device"],
         int(task_row["fallback_applied"]), task_row["fallback_reason"],
         state, json.dumps(organic_positions), json.dumps(ad_positions),
         json.dumps(occurrences, ensure_ascii=False), int(is_complete),
         json.dumps(evidence, ensure_ascii=False), snapshot_id, content_hash),
    )
    return rid


def publish_generation(con, gen_id: str) -> int:
    """完整代次整体发布；返回被发布的排名点数。"""
    cur = con.execute(
        "UPDATE rank_history SET published=1 WHERE gen_id=? AND published=0",
        (gen_id,),
    )
    con.execute(
        "UPDATE generations SET status=?, published_at=datetime('now') "
        "WHERE gen_id=? AND status!=?",
        (models.GEN_PUBLISHED, gen_id, models.GEN_PUBLISHED),
    )
    return cur.rowcount


def rank_series(con, *, keyword: str, site_id: str, geo: str, lang: str,
                device: str, only_published: bool = True) -> list[dict]:
    """按实际条件读取排名历史（代次时间顺序）。"""
    ctx_key = keyer.context_key(keyword, site_id, geo, lang, device)
    sql = ("SELECT * FROM rank_history WHERE context_key=? "
           + ("AND published=1 " if only_published else "")
           + "ORDER BY created_at, gen_id")
    rows = [dict(r) for r in con.execute(sql, (ctx_key,))]
    for r in rows:
        r["organic_positions"] = json.loads(r["organic_positions"])
        r["ad_positions"] = json.loads(r["ad_positions"])
        r["occurrences_json"] = json.loads(r["occurrences_json"])
        r["evidence_json"] = json.loads(r["evidence_json"])
    return rows


def latest_generation(con) -> str | None:
    row = con.execute(
        "SELECT gen_id FROM generations WHERE status=? "
        "ORDER BY published_at DESC, rowid DESC LIMIT 1",
        (models.GEN_PUBLISHED,),
    ).fetchone()
    return row[0] if row else None
