"""单一职责：在一次快照内按站点/URL 归并出现位置并去重排名点。

- 自然结果、广告、特殊块分别统计，互不混位。
- 同一 URL（同主机）在一个快照中的不同位置全部保留为 occurrences。
- 重复采集（同代次同上下文同内容）不新增排名点：由 rankstore 的
  UNIQUE(gen_id, context_key) 幂等 upsert 兜底，本模块只负责观测归并。
"""
from . import models
from .normalizer import normalize_host


def summarize_host(blocks: list[models.Block], host: str) -> dict:
    """返回某主机在快照中的全部出现位置（按类型分开，保留重复位置）。"""
    host = normalize_host(host)
    organic, ads, specials = [], [], []
    occurrences = []
    for b in blocks:
        if normalize_host(b.url) != host:
            continue
        item = {"block_type": b.block_type, "position": b.position,
                "ordinal": b.ordinal, "page": b.page, "url": b.url,
                "kind": b.kind}
        occurrences.append(item)
        if b.block_type == models.ORGANIC:
            organic.append(b.position)
        elif b.block_type == models.AD:
            ads.append(b.position)
        else:
            specials.append(b.position)
    return {
        "host": host,
        "found": bool(occurrences),
        "organic_positions": organic,
        "ad_positions": ads,
        "special_positions": specials,
        "occurrences": occurrences,
    }


def classify_state(summary: dict, is_complete: bool) -> str:
    """三态区分：未出现 / 无法判断（不完整）/ 已上榜。"""
    if summary["found"]:
        return models.RANKED
    if not is_complete:
        return models.UNKNOWN
    return models.NOT_APPEARED


def process(record):
    """兼容通用流水线的占位变换（SERP 域使用 summarize_host）。"""
    rec = dict(record)
    rec.setdefault("_stage_dedup", "ok")
    return rec
