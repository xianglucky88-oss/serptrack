"""单一职责：快照持久化与可追溯性（领域入口 save_snapshot/trace_snapshot）。

- 快照按 代次×关键词×实际条件 幂等写入：重复采集不新增快照、不新增排名点。
- 页面原始块、解析条目、完整性证据、批次号全部随快照保存。
- 快照携带 content_hash；批次过旧时 runner 拒绝交付（旧响应不能覆盖新批次）。
- 提供双向追溯：rank_history.snapshot_id → 快照；快照 → pages/parsed_entries。

process() 仅为通用记录流水线保留的占位变换。
"""
import json

from . import keyer
from .models import ParseResult
from .normalizer import normalize_host


def process(record):
    rec = dict(record)
    rec.setdefault("_stage_snapshot", "ok")
    return rec


def compute_content_hash(pages) -> str:
    payload = [
        {"page": p.page, "complete": p.complete, "truncated": p.truncated,
         "async_pending": p.async_pending, "blocks": p.blocks}
        for p in sorted(pages, key=lambda x: x.page)
    ]
    return keyer.short_hash(payload, 32)


def find_snapshot(con, gen_id: str, keyword: str, geo: str, lang: str,
                  device: str) -> str | None:
    row = con.execute(
        "SELECT snapshot_id FROM snapshots WHERE gen_id=? AND keyword=? "
        "AND geo=? AND lang=? AND device=?",
        (gen_id, keyword, geo, lang, device),
    ).fetchone()
    return row[0] if row else None


def save_snapshot(con, *, gen_id, keyword, requested, actual, fallback_applied,
                  fallback_reason, pages, parsed: ParseResult, batch_id) -> str:
    """幂等保存。返回 snapshot_id；已存在时原样返回且不写任何新行。"""
    snap_id = keyer.snapshot_key(gen_id, keyword, actual.geo, actual.lang, actual.device)
    existing = con.execute(
        "SELECT snapshot_id, content_hash FROM snapshots WHERE snapshot_id=?",
        (snap_id,),
    ).fetchone()
    content_hash = compute_content_hash(pages)
    if existing:
        return snap_id

    con.execute(
        "INSERT INTO snapshots(snapshot_id, gen_id, keyword, geo, lang, device, "
        "req_geo, req_lang, req_device, fallback_applied, fallback_reason, "
        "is_complete, evidence_json, pages_fetched, content_hash, batch_id) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (snap_id, gen_id, keyword, actual.geo, actual.lang, actual.device,
         requested.geo, requested.lang, requested.device, int(fallback_applied),
         fallback_reason, int(parsed.completeness.is_complete),
         json.dumps(parsed.completeness.evidence, ensure_ascii=False),
         parsed.completeness.pages_fetched, content_hash, batch_id),
    )
    for p in sorted(pages, key=lambda x: x.page):
        pid = keyer.page_key(gen_id, keyword, actual.geo, actual.lang, actual.device, p.page)
        con.execute(
            "INSERT OR IGNORE INTO pages(page_id, snapshot_id, gen_id, keyword, "
            "geo, lang, device, page_no, complete, truncated, async_pending, raw_blocks) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (pid, snap_id, gen_id, keyword, actual.geo, actual.lang, actual.device,
             p.page, int(p.complete), int(p.truncated),
             json.dumps(p.async_pending, ensure_ascii=False),
             json.dumps(p.blocks, ensure_ascii=False)),
        )
    for b in parsed.blocks:
        eid = keyer.entry_id(snap_id, normalize_host(b.url), b.block_type,
                             b.position, b.ordinal)
        con.execute(
            "INSERT OR IGNORE INTO parsed_entries(entry_id, snapshot_id, gen_id, "
            "block_type, host, url, title, kind, page_no, position, ordinal) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (eid, snap_id, gen_id, b.block_type, normalize_host(b.url), b.url,
             b.title, b.kind, b.page, b.position, b.ordinal),
        )
    return snap_id


def entries_for(con, snapshot_id: str):
    return [dict(r) for r in con.execute(
        "SELECT * FROM parsed_entries WHERE snapshot_id=? ORDER BY ordinal",
        (snapshot_id,),
    )]


def trace_snapshot(con, snapshot_id: str) -> dict:
    snap = con.execute("SELECT * FROM snapshots WHERE snapshot_id=?",
                       (snapshot_id,)).fetchone()
    if not snap:
        return {}
    pages = [dict(r) for r in con.execute(
        "SELECT page_no, complete, truncated, async_pending, raw_blocks FROM pages "
        "WHERE snapshot_id=? ORDER BY page_no", (snapshot_id,))]
    ranks = [dict(r) for r in con.execute(
        "SELECT rank_id, gen_id, context_key, site_id, rank_state FROM rank_history "
        "WHERE snapshot_id=?", (snapshot_id,))]
    return {"snapshot": dict(snap), "pages": pages, "rank_points": ranks}
