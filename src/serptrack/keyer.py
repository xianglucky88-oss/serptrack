"""单一职责：派生稳定主键。

保留通用记录的 key_of；同时为 SERP 域提供确定性哈希，保证重复采集幂等、
快照/历史/条目之间可以互相追溯。
"""
import hashlib
import json


def _digest(payload) -> str:
    blob = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def key_of(record):
    rid = record.get("id")
    if rid:
        return str(rid)
    return _digest(record)[:16]


def short_hash(payload, length: int = 16) -> str:
    return _digest(payload)[:length]


def config_version(matrix: dict) -> str:
    """配置版本：矩阵业务内容（不含运行态字段）的确定性哈希。"""
    keys = ("matrix_id", "keywords", "sites", "contexts", "fallback",
            "budget_total", "max_concurrency", "lease_seconds")
    return short_hash({k: matrix.get(k) for k in keys}, 12)


def context_key(keyword: str, site_id: str, geo: str, lang: str, device: str) -> str:
    """排名历史的采集上下文键（实际条件，不含被回退的请求条件）。"""
    return short_hash([keyword, site_id, geo, lang, device], 20)


def snapshot_key(gen_id: str, keyword: str, geo: str, lang: str, device: str) -> str:
    return short_hash([gen_id, keyword, geo, lang, device], 20)


def page_key(gen_id: str, keyword: str, geo: str, lang: str, device: str, page: int) -> str:
    return short_hash([gen_id, keyword, geo, lang, device, page], 20)


def job_id(gen_id: str, keyword: str, requested_geo: str, lang: str, device: str) -> str:
    return short_hash(["job", gen_id, keyword, requested_geo, lang, device], 16)


def task_id(gen_id: str, keyword: str, site_id: str,
            requested_geo: str, lang: str, device: str) -> str:
    return short_hash(["task", gen_id, keyword, site_id, requested_geo, lang, device], 16)


def entry_id(snapshot_id: str, host: str, block_type: str, position: int, ordinal: int) -> str:
    return short_hash(["entry", snapshot_id, host, block_type, position, ordinal], 20)


def rank_id(gen_id: str, ctx_key: str) -> str:
    return short_hash(["rank", gen_id, ctx_key], 20)
