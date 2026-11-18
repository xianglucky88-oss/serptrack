"""单一职责：解释排名变化的依据，以及不可比较的原因。

只读取 *已发布* 代次的排名点。比较严格基于实际采集条件
（geo/lang/device）与快照完整性；任一不满足即判不可比较并给出原因，
避免把地域切换、广告位变化、截断/异步未加载误报为排名下降。
"""
from . import keyer, models, rankstore


def explain(con, *, keyword: str, site_id: str, geo: str, lang: str,
            device: str) -> dict:
    series = rankstore.rank_series(
        con, keyword=keyword, site_id=site_id, geo=geo, lang=lang, device=device,
        only_published=True,
    )
    requested = {"keyword": keyword, "site_id": site_id,
                 "geo": geo, "lang": lang, "device": device}
    if not series:
        return {
            "query": requested,
            "comparable": False,
            "verdict": "no_published_data",
            "reason": "该实际条件下没有已发布的排名点（可能尚未采到或未发布）",
            "generations": [],
        }

    latest = series[-1]
    if len(series) < 2:
        return {
            "query": requested,
            "comparable": False,
            "verdict": "insufficient_history",
            "reason": f"仅有代次 {latest['gen_id']} 一个数据点，无法判断变化",
            "generations": [_point_brief(latest)],
        }

    previous = series[-2]
    blockers = _comparability_blockers(previous, latest)
    briefs = [_point_brief(previous), _point_brief(latest)]
    if blockers:
        return {
            "query": requested,
            "comparable": False,
            "verdict": "not_comparable",
            "reason": "; ".join(blockers),
            "generations": briefs,
        }

    change = _describe_change(previous, latest)
    return {
        "query": requested,
        "comparable": True,
        "verdict": change["verdict"],
        "reason": change["reason"],
        "evidence": {
            "previous_snapshot": previous["snapshot_id"],
            "latest_snapshot": latest["snapshot_id"],
            "previous_evidence": previous["evidence_json"],
            "latest_evidence": latest["evidence_json"],
            "context": {
                "actual": {"geo": latest["act_geo"], "lang": latest["act_lang"],
                           "device": latest["act_device"]},
                "requested": {"geo": latest["req_geo"], "lang": latest["req_lang"],
                              "device": latest["req_device"]},
                "fallback_applied": bool(latest["fallback_applied"]),
                "fallback_reason": latest["fallback_reason"],
            },
        },
        "generations": briefs,
    }


def _comparability_blockers(prev: dict, cur: dict) -> list[str]:
    blockers = []
    actual_prev = (prev["act_geo"], prev["act_lang"], prev["act_device"])
    actual_cur = (cur["act_geo"], cur["act_lang"], cur["act_device"])
    if actual_prev != actual_cur:
        blockers.append(
            f"采集条件不一致: {_fmt_ctx(prev)} → {_fmt_ctx(cur)}（地域/语言/设备切换不可直接比较）"
        )
    if not prev["is_complete"]:
        blockers.append(
            f"前代次 {prev['gen_id']} 快照不完整（{_incomplete_detail(prev)}），基线不可信"
        )
    if not cur["is_complete"]:
        blockers.append(
            f"当代次 {cur['gen_id']} 快照不完整（{_incomplete_detail(cur)}），无法判断是否出现"
        )
    return blockers


def _describe_change(prev: dict, cur: dict) -> dict:
    # 三态：ranked / not_appeared / unknown（unknown 在完整性闸门后不会进入比较）
    if cur["rank_state"] == models.NOT_APPEARED:
        if prev["rank_state"] == models.RANKED:
            return {"verdict": "disappeared",
                    "reason": f"完整快照中站点已从前代次自然位 {prev['organic_positions']} 消失"}
        return {"verdict": "still_absent", "reason": "两代次完整快照中均未出现"}

    if prev["rank_state"] == models.NOT_APPEARED and cur["rank_state"] == models.RANKED:
        return {"verdict": "appeared",
                "reason": f"站点新上榜，自然位 {cur['organic_positions']}"}

    prev_pos = prev["organic_positions"]
    cur_pos = cur["organic_positions"]
    prev_best, cur_best = min(prev_pos), min(cur_pos)
    ad_note = ""
    if cur["ad_positions"] or prev["ad_positions"]:
        ad_note = (f"；广告位单独计：前代次 {prev['ad_positions']} → "
                   f"当代次 {cur['ad_positions']}（广告不参与自然排名）")
    if cur_best > prev_best:
        return {"verdict": "rank_declined",
                "reason": (f"自然排名最佳位置 {prev_best} → {cur_best}（数字变大即下降），"
                           f"全部自然位 {prev_pos} → {cur_pos}{ad_note}")}
    if cur_best < prev_best:
        return {"verdict": "rank_improved",
                "reason": (f"自然排名最佳位置 {prev_best} → {cur_best}，"
                           f"全部自然位 {prev_pos} → {cur_pos}{ad_note}")}
    return {"verdict": "rank_unchanged",
            "reason": f"自然排名最佳位置仍为 {cur_best}，全部自然位 {cur_pos}{ad_note}"}


def _point_brief(row: dict) -> dict:
    return {
        "gen_id": row["gen_id"],
        "rank_state": row["rank_state"],
        "organic_positions": row["organic_positions"],
        "ad_positions": row["ad_positions"],
        "is_complete": bool(row["is_complete"]),
        "actual": {"geo": row["act_geo"], "lang": row["act_lang"],
                   "device": row["act_device"]},
        "fallback_applied": bool(row["fallback_applied"]),
        "snapshot_id": row["snapshot_id"],
    }


def _incomplete_detail(row: dict) -> str:
    return ",".join(row["evidence_json"]) or "未知原因"


def _fmt_ctx(row: dict) -> str:
    return f"{row['act_geo']}/{row['act_lang']}/{row['act_device']}"


def integrity_counts(con, *, keyword: str, site_id: str, geo: str, lang: str,
                     device: str) -> dict:
    """明确区分未出现 / 未采到 / 无法判断的计数（含未发布代次）。

    以实际条件（geo/lang/device）匹配任务计划：
    - 有已落排名点 → 按 rank_state 计数；
    - 计划里存在但还没有排名点 → not_collected（未采到）。
    """
    ctx_key = keyer.context_key(keyword, site_id, geo, lang, device)
    series = rankstore.rank_series(
        con, keyword=keyword, site_id=site_id, geo=geo, lang=lang, device=device,
        only_published=False,
    )
    counts = {models.NOT_APPEARED: 0, models.NOT_COLLECTED: 0,
              models.UNKNOWN: 0, models.RANKED: 0}
    seen_gens = set()
    for r in series:
        counts[r["rank_state"]] = counts.get(r["rank_state"], 0) + 1
        seen_gens.add(r["gen_id"])
    planned = con.execute(
        "SELECT COUNT(*) FROM tasks WHERE keyword=? AND site_id=? "
        "AND act_geo=? AND act_lang=? AND act_device=?",
        (keyword, site_id, geo, lang, device),
    ).fetchone()[0]
    counts[models.NOT_COLLECTED] = max(0, planned - len(seen_gens))
    return counts
