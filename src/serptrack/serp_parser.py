"""单一职责：解析 SERP 页面，区分自然结果/广告/特殊块并给出完整性证据。

- 三类块严格区分：organic / ad / special；special 记录 kind（knowledge_panel 等）。
- 同一 URL 出现在多个位置时全部保留：position 按同类型块跨页连续编号，
  ordinal 是全块原始序号，两者都保留以便还原页面布局。
- 完整性证据区分三种“没看到”：
  * 完整快照中缺失 → 未出现（由 rankstore 判 not_appeared）
  * truncated / async_pending → 采到但不完整（unknown），证据落库
  * 抓取阶段缺页 → 未采到（not_collected，任务保持未确认）

process() 仅为通用记录流水线保留的占位变换，SERP 域不经过该入口。
"""
from .models import Block, Completeness, ParseResult, PagePayload
from .normalizer import normalize_host
from . import models

_KNOWN_TYPES = set(models.BLOCK_TYPES)


def process(record):
    rec = dict(record)
    rec.setdefault("_stage_serp_parser", "naive")
    return rec


def parse_pages(pages: list[PagePayload]) -> ParseResult:
    if not pages:
        raise ValueError("parse_pages requires at least one page")
    ordered = sorted(pages, key=lambda p: p.page)
    blocks: list[Block] = []
    type_counters = {t: 0 for t in _KNOWN_TYPES}
    ordinal = 0
    evidence: list[str] = []
    truncated = False
    async_pending: list[str] = []
    all_complete = True

    for payload in ordered:
        if payload.truncated:
            truncated = True
            all_complete = False
            evidence.append(f"{models.EV_TRUNCATED}:page={payload.page}")
        if payload.async_pending:
            all_complete = False
            for name in payload.async_pending:
                async_pending.append(name)
                evidence.append(f"{models.EV_ASYNC_PENDING}:{name}:page={payload.page}")
        if not payload.complete:
            all_complete = False
        for raw in payload.blocks:
            btype = raw.get("type", "")
            if btype not in _KNOWN_TYPES:
                # 未知块不丢弃：归入 special 并保留 kind，避免污染自然排名
                btype = models.SPECIAL
                kind = f"unknown:{raw.get('type', '?')}"
            else:
                kind = str(raw.get("kind", ""))
            ordinal += 1
            type_counters[btype] += 1
            blocks.append(Block(
                block_type=btype,
                url=raw.get("url"),
                title=str(raw.get("title", "")),
                kind=kind,
                page=payload.page,
                position=type_counters[btype],
                ordinal=ordinal,
            ))

    pages_fetched = len(ordered)
    evidence.append(
        f"{models.EV_PAGINATED}:pages={pages_fetched}" if pages_fetched > 1
        else f"{models.EV_SINGLE_PAGE}:pages=1"
    )
    if all_complete:
        evidence.append(models.EV_FULL)
    completeness = Completeness(
        is_complete=all_complete and not truncated and not async_pending,
        evidence=evidence,
        truncated=truncated,
        async_pending=async_pending,
        pages_fetched=pages_fetched,
    )
    return ParseResult(blocks=blocks, completeness=completeness)


def hosts_of(blocks: list[Block], block_type: str) -> list[tuple[str, Block]]:
    return [(normalize_host(b.url), b) for b in blocks if b.block_type == block_type]
