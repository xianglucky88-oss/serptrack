"""单一职责：SERP 采集域编排（对应通用 pipeline 的领域执行器）。

顺序：计划 → 租约回收 → 公平认领 → 抓取分页 → 解析 → 快照 → 去重归并 →
排名历史（未发布）→ 整代次确认后发布。任何一步失败都保持未确认状态，
配置更新/请求失败/进程重启后只接续未确认工作。
"""
import uuid

from . import keyer, ledger, models, rankstore, snapshot
from .dedup import classify_state, summarize_host
from .fetcher import FetchError
from .serp_parser import parse_pages


class StaleDelivery(Exception):
    """旧批次/未知批次响应：拒绝并留痕，不能覆盖新批次。"""


def run_domain(con, matrix, fetcher, *, run_id: str | None = None,
               now: float | None = None) -> dict:
    from .queryplan import build_plan, persist_plan

    version = keyer.config_version(matrix)
    plan = build_plan(matrix, version)
    persist_plan(con, version, matrix, plan)

    if run_id is None:
        run_id = ledger.start_run(con, version, matrix["budget_total"],
                                  matrix["max_concurrency"])

    ledger.reclaim_expired_leases(con, matrix["lease_seconds"], now=now)
    stats = {"run_id": run_id, "config_version": version, "generations": []}

    for gen in matrix["generations"]:
        gen_id = str(gen["gen_id"])
        gen_stats = None
        # 单进程内按并发上限滚动接续，直到没有可认领任务或预算耗尽；
        # 进程中断后下一次运行从租约回收处继续（只接续未确认工作）。
        while True:
            gen_stats = _run_generation(con, matrix, fetcher, version, gen_id,
                                        run_id, now=now)
            if gen_stats["claimed"] == 0 or ledger.budget_left(con, version) <= 0:
                break
        stats["generations"].append(gen_stats)
    ledger.finish_run(con, run_id)
    con.commit()
    return stats


def _run_generation(con, matrix, fetcher, version, gen_id, run_id, now=None) -> dict:
    concurrency = matrix["max_concurrency"]
    lease_seconds = matrix["lease_seconds"]
    owner = f"runner-{uuid.uuid4().hex[:8]}"

    claimed = ledger.claim_tasks(
        con, gen_id=gen_id, owner=owner, lease_seconds=lease_seconds,
        max_concurrency=concurrency, now=now,
        retry_attempts=matrix.get("retry_attempts", 2),
    )
    jobs = ledger.open_jobs_for_tasks(con, claimed)
    seen_jobs: set[str] = set()
    processed = 0
    for task in claimed:
        job = jobs[task["job_id"]]
        if job["job_id"] in seen_jobs:
            # 同一作业的后续站点任务由首个任务的快照一次性结算
            continue
        seen_jobs.add(job["job_id"])
        try:
            _process_job(con, matrix, fetcher, version, gen_id, job, run_id, now=now)
            processed += 1
        except StaleDelivery as exc:
            ledger.reject_delivery(
                con, batch_id=getattr(exc, "batch_id", "?"), gen_id=gen_id,
                reason=str(exc), payload_hash=getattr(exc, "payload_hash", ""),
            )
            ledger.release_job_leases(con, job["job_id"], models.FAILED, str(exc))
            con.commit()
        except FetchError as exc:
            # 失败尝试已在 _process_job 内计入预算；作业保持未确认，等待后续运行重试
            ledger.mark_job(con, job["job_id"], models.FAILED, error=str(exc))
            ledger.release_job_leases(con, job["job_id"], models.FAILED, str(exc))
            con.commit()
        except _BudgetStop:
            ledger.mark_job(con, job["job_id"], models.BUDGET_EXHAUSTED)
            ledger.release_job_leases(con, job["job_id"], models.BUDGET_EXHAUSTED)
            con.commit()

    status = ledger.job_status(con, gen_id)
    published = 0
    # 所有任务均已确认（done=完整结论，incomplete=不完整结论 unknown）即整代次发布；
    # pending/running/failed/budget_exhausted 属于未确认，代次保持 open，下次接续。
    if status["all_confirmed"]:
        published = rankstore.publish_generation(con, gen_id)
        con.commit()
    return {"gen_id": gen_id, "claimed": len(claimed), "jobs_processed": processed,
            "status": status, "published_points": published}


def _process_job(con, matrix, fetcher, version, gen_id, job, run_id, now=None):
    batch_id = ledger.next_batch_id(con, job["job_id"])
    con.commit()

    pages = []
    page_no = 1
    while True:
        if ledger.budget_left(con, version) <= 0:
            raise _BudgetStop("budget exhausted before page fetch")
        try:
            payload = fetcher.fetch(
                gen_id, job["keyword"], job["act_geo"], job["act_lang"],
                job["act_device"], page_no,
            )
        except StopIteration:
            break
        except FetchError:
            ledger.charge(con, run_id, 1)  # 失败的尝试也计入预算，防止重试风暴
            con.commit()
            raise
        ledger.charge(con, run_id, 1)
        con.commit()
        pages.append(payload)
        if payload.truncated or not payload.complete:
            break  # 不完整页面不继续翻页，完整性证据随快照留存
        page_no += 1

    # 批次守门：交付时批次必须仍是该作业的当前批次，否则拒绝（旧响应不覆盖新批次）
    current = ledger.current_batch_id(con, job["job_id"])
    if current != batch_id:
        exc = StaleDelivery(f"stale batch {batch_id} != current {current}")
        exc.batch_id = batch_id
        exc.payload_hash = snapshot.compute_content_hash(pages)
        raise exc

    parsed = parse_pages(pages)
    snap_id = snapshot.save_snapshot(
        con, gen_id=gen_id, keyword=job["keyword"],
        requested=models.Context(job["req_geo"], job["req_lang"], job["req_device"]),
        actual=models.Context(job["act_geo"], job["act_lang"], job["act_device"]),
        fallback_applied=bool(job["fallback_applied"]),
        fallback_reason=job["fallback_reason"],
        pages=pages, parsed=parsed, batch_id=batch_id,
    )
    content_hash = snapshot.compute_content_hash(pages)

    sibling_tasks = con.execute(
        "SELECT * FROM tasks WHERE job_id=? ORDER BY site_id", (job["job_id"],),
    ).fetchall()
    for t in sibling_tasks:
        t = dict(t)
        summary = summarize_host(parsed.blocks, t["host"])
        state = classify_state(summary, parsed.completeness.is_complete)
        rankstore.upsert_rank(
            con, task_row={**t, "rank_state": state}, snapshot_id=snap_id,
            is_complete=parsed.completeness.is_complete,
            evidence=parsed.completeness.evidence,
            organic_positions=summary["organic_positions"],
            ad_positions=summary["ad_positions"],
            occurrences=summary["occurrences"], content_hash=content_hash,
            config_version=version,
        )
        task_status = models.DONE if parsed.completeness.is_complete else models.INCOMPLETE
        ledger.complete_task(
            con, t["task_id"], task_status, snap_id, state,
            summary["organic_positions"], summary["ad_positions"],
        )
    ledger.mark_job(con, job["job_id"], models.DONE, snapshot_id=snap_id)
    con.commit()


class _BudgetStop(Exception):
    pass
