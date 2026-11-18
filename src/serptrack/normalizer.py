"""单一职责：字段规整/规范化。

- process(): 保留原有通用记录规整逻辑。
- normalize_matrix(): 规整查询矩阵（去空白、默认值、去重保序）。
- normalize_host(): URL → 可比较主机名，用于同 URL/同站点判定。
"""
from urllib.parse import urlparse


def process(record):
    rec = dict(record)
    rec["src"] = str(rec.get("src", "")).strip().lower()
    rec["name"] = str(rec.get("name", "")).strip()
    rec["value"] = int(rec.get("value", 0))
    return rec


def _uniq(seq):
    seen = set()
    out = []
    for item in seq:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out


def normalize_matrix(matrix: dict) -> dict:
    m = dict(matrix)
    m["matrix_id"] = str(m.get("matrix_id", "")).strip()
    m["keywords"] = _uniq(str(k).strip() for k in m.get("keywords", []))
    sites = []
    for s in m.get("sites", []):
        sites.append({
            "site_id": str(s["site_id"]).strip(),
            "host": normalize_host(s["host"]),
        })
    m["sites"] = sites
    contexts = []
    for c in m.get("contexts", []):
        contexts.append({
            "geo": str(c["geo"]).strip().upper(),
            "lang": str(c["lang"]).strip().lower(),
            "device": str(c["device"]).strip().lower(),
        })
    m["contexts"] = contexts
    m["fallback"] = m.get("fallback", []) or []
    m["budget_total"] = int(m.get("budget_total", 100))
    m["max_concurrency"] = int(m.get("max_concurrency", 4))
    m["lease_seconds"] = int(m.get("lease_seconds", 30))
    m["generations"] = m.get("generations", []) or []
    return m


def normalize_host(url: str | None) -> str:
    if not url:
        return ""
    raw = url.strip().lower()
    if "://" not in raw:
        raw = "http://" + raw
    host = urlparse(raw).netloc
    if host.startswith("www."):
        host = host[4:]
    return host.split(":")[0]
