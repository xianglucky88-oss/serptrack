"""单一职责：定义 SERP 采集域的数据结构与状态常量。"""
from dataclasses import dataclass, field, asdict
from typing import Any

# 块类型
ORGANIC = "organic"
AD = "ad"
SPECIAL = "special"
BLOCK_TYPES = (ORGANIC, AD, SPECIAL)

# 站点在一次（代次 × 关键词 × 实际条件）快照中的排名结论
RANKED = "ranked"              # 完整快照中找到站点 URL
NOT_APPEARED = "not_appeared"  # 完整快照中确认没有站点 URL
NOT_COLLECTED = "not_collected"  # 尚未采集到（没有该条件的快照）
UNKNOWN = "unknown"            # 采到了但快照不完整，无法判断是否出现

# 任务/作业状态
PENDING = "pending"
RUNNING = "running"
DONE = "done"          # 已确认：快照完整，结论已入历史
INCOMPLETE = "incomplete"  # 已确认：快照不完整（截断/异步未加载），结论为 unknown
FAILED = "failed"      # 请求失败且预算/重试耗尽前的未确认状态（可恢复重试）
BUDGET_EXHAUSTED = "budget_exhausted"  # 未确认：预算用尽，恢复后可继续
TERMINAL = (DONE, INCOMPLETE)
RESUMABLE = (RUNNING, FAILED, BUDGET_EXHAUSTED, PENDING)

# 代次状态
GEN_OPEN = "open"
GEN_PUBLISHED = "published"

# 完整性证据标记
EV_FULL = "complete"
EV_TRUNCATED = "truncated"
EV_ASYNC_PENDING = "async_pending"
EV_PAGINATED = "paginated"
EV_SINGLE_PAGE = "single_page"


@dataclass
class Context:
    geo: str
    lang: str
    device: str

    def as_tuple(self):
        return (self.geo, self.lang, self.device)

    def to_dict(self):
        return asdict(self)


@dataclass
class ResolvedContext:
    requested: Context
    actual: Context
    fallback_applied: bool
    reason: str = ""


@dataclass
class Record:
    """通用流水线记录（保留原有合法输入链路）。"""
    key: str
    data: dict[str, Any] = field(default_factory=dict)


@dataclass
class Block:
    block_type: str
    url: str | None
    title: str
    kind: str = ""          # special 块的子类（knowledge_panel/faq/...）
    page: int = 1
    position: int = 0       # 同类型块在快照内的连续序号
    ordinal: int = 0        # 原始块序号（所有块统一计数，含广告/特殊块）

    def to_dict(self):
        return asdict(self)


@dataclass
class Completeness:
    is_complete: bool
    evidence: list[str] = field(default_factory=list)
    truncated: bool = False
    async_pending: list[str] = field(default_factory=list)
    pages_fetched: int = 1
    expected_pages: int | None = None

    def to_dict(self):
        return asdict(self)


@dataclass
class PagePayload:
    """离线页面载体（mock fetch 的返回结构）。"""
    gen_id: str
    keyword: str
    geo: str
    lang: str
    device: str
    page: int
    complete: bool
    truncated: bool
    async_pending: list[str]
    blocks: list[dict[str, Any]]


@dataclass
class ParseResult:
    blocks: list[Block]
    completeness: Completeness
