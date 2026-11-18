"""单一职责：采集台账——租约、预算、站点公平调度、崩溃恢复与批次守门。

只做状态机与 SQL，不做抓取/解析。进程重启后：
- 过期 RUNNING 租约回收为 pending（只接续未确认工作，DONE/INCOMPLETE 不动）；
- 预算按 run 累计持久化；
- 批次号单调递增，关闭/未知批次的交付写入 rejected_deliveries 并被拒绝。
"""
import json
import sqlite3
import time
import uuid

from . import keyer, models


def start_run(con, config_version: str, budget_total: int, max_concurrency: int) -> str:
    run_id = uuid.uuid4().hex
    # 预算属于配置版本：记录该配置的总预算，所有 run 的实际消耗累计到 budget_used，
    # 跨进程重启后不重复发放。
    con.execute(
        "INSERT INTO runs(run_id, config_version, budget_total, max_concurrency) "
        "VALUES(?,?,?,?)",
        (run_id, config_version, budget_total, max_concurrency),
    )
    con.commit()
    return run_id


def finish_run(con, run_id: str) -> None:
    con.execute("UPDATE runs SET finished_at=datetime('now') WHERE run_id=?", (run_id,))
    con.commit()


def budget_total_for(con, config_version: str) -> int:
    # 配置更新可调整预算：以最近一个 run 声明的总预算为准，已消耗量仍累计保留。
    row = con.execute(
        "SELECT budget_total FROM runs WHERE config_version=? "
        "ORDER BY started_at DESC, rowid DESC LIMIT 1",
        (config_version,),
    ).fetchone()
    return int(row[0]) if row else 0


def budget_used(con, config_version: str) -> int:
    row = con.execute(
        "SELECT COALESCE(SUM(budget_used),0) FROM runs WHERE config_version=?",
        (config_version,),
    ).fetchone()
    return int(row[0])


def charge(con, run_id: str, amount: int = 1) -> None:
    con.execute("UPDATE runs SET budget_used=budget_used+? WHERE run_id=?",
                (amount, run_id))


def budget_left(con, config_version: str) -> int:
    return budget_total_for(con, config_version) - budget_used(con, config_version)


def reclaim_expired_leases(con, lease_seconds: int, now: float | None = None) -> int:
    """进程重启/新一轮运行时回收未确认工作。

    - 过期 RUNNING 租约（崩溃在途）→ pending；
    - failed / budget_exhausted（请求失败或预算中断）→ pending，等待接续；
    - 终态 DONE/INCOMPLETE 永不动。
    """
    now = time.time() if now is None else now
    cutoff = _iso(now - lease_seconds)
    cur = con.execute(
        "UPDATE tasks SET status=?, lease_owner=NULL, lease_expires_at=NULL "
        "WHERE status=? AND lease_expires_at IS NOT NULL AND lease_expires_at < ?",
        (models.PENDING, models.RUNNING, cutoff),
    )
    cur2 = con.execute(
        "UPDATE tasks SET status=?, lease_owner=NULL, lease_expires_at=NULL "
        "WHERE status IN (?, ?)",
        (models.PENDING, models.FAILED, models.BUDGET_EXHAUSTED),
    )
    # 未确认作业同步回到 pending，保证批次/尝试计数从同一状态接续
    con.execute(
        "UPDATE jobs SET status=?, updated_at=datetime('now') "
        "WHERE status IN (?, ?, ?)",
        (models.PENDING, models.RUNNING, models.FAILED, models.BUDGET_EXHAUSTED),
    )
    con.commit()
    return cur.rowcount + cur2.rowcount


def claim_tasks(con, *, gen_id: str, owner: str, lease_seconds: int,
                max_concurrency: int, now: float | None = None,
                retry_attempts: int = 1) -> list[dict]:
    """站点公平的轮询认领：每轮每个站点最多取一个任务，轮转直到并发上限。

    仅认领 attempts 未超过本轮重试上限的作业，保证持久失败不会在单次运行内
    死循环；跨进程重启后 reclaim 会把 failed 重新挂回 pending 并允许再次尝试。
    """
    now = time.time() if now is None else now
    rows = con.execute(
        "SELECT t.* FROM tasks t JOIN jobs j ON t.job_id=j.job_id "
        "WHERE t.gen_id=? AND t.status=? AND j.attempts<=? "
        "ORDER BY t.site_id, t.keyword, t.req_geo",
        (gen_id, models.PENDING, retry_attempts),
    ).fetchall()
    by_site: dict[str, list[sqlite3.Row]] = {}
    for r in rows:
        by_site.setdefault(r["site_id"], []).append(r)
    claimed: list[dict] = []
    queues = {s: list(v) for s, v in by_site.items()}
    while len(claimed) < max_concurrency and queues:
        for site in sorted(queues):
            if len(claimed) >= max_concurrency:
                break
            queue = queues[site]
            if not queue:
                del queues[site]
                continue
            row = queue.pop(0)
            expires = _iso(now + lease_seconds)
            con.execute(
                "UPDATE tasks SET status=?, lease_owner=?, lease_expires_at=? "
                "WHERE task_id=? AND status=?",
                (models.RUNNING, owner, expires, row["task_id"], models.PENDING),
            )
            claimed.append(dict(row))
    con.commit()
    return claimed


def complete_task(con, task_id: str, status: str, snapshot_id: str | None,
                  rank_state: str, organic_positions, ad_positions) -> None:
    con.execute(
        "UPDATE tasks SET status=?, snapshot_id=?, rank_state=?, "
        "organic_positions=?, ad_positions=?, lease_owner=NULL, "
        "lease_expires_at=NULL, updated_at=datetime('now') WHERE task_id=?",
        (status, snapshot_id, rank_state,
         json.dumps(organic_positions),
         json.dumps(ad_positions), task_id),
    )


def fail_task(con, task_id: str, status: str, error: str) -> None:
    con.execute(
        "UPDATE tasks SET status=?, lease_owner=NULL, lease_expires_at=NULL, "
        "last_error=?, updated_at=datetime('now') WHERE task_id=?",
        (status, error, task_id),
    )


def open_jobs_for_tasks(con, tasks: list[dict]) -> dict[str, dict]:
    if not tasks:
        return {}
    ids = tuple({t["job_id"] for t in tasks})
    placeholders = ",".join("?" * len(ids))
    return {r["job_id"]: dict(r) for r in con.execute(
        f"SELECT * FROM jobs WHERE job_id IN ({placeholders})", ids)}


def job_status(con, gen_id: str) -> dict:
    rows = con.execute(
        "SELECT status, COUNT(*) AS n FROM tasks WHERE gen_id=? GROUP BY status",
        (gen_id,),
    )
    counts = {r["status"]: r["n"] for r in rows}
    total = sum(counts.values())
    terminal = sum(counts.get(s, 0) for s in models.TERMINAL)
    return {"total": total, "terminal": terminal, "counts": counts,
            "all_confirmed": total > 0 and terminal == total}


def next_batch_id(con, job_id: str) -> str:
    """批次号在作业内单调递增，旧响应无法覆盖新批次。"""
    row = con.execute("SELECT batch_id, attempts FROM jobs WHERE job_id=?",
                      (job_id,)).fetchone()
    attempts = int(row["attempts"]) + 1
    batch_id = f"{job_id}:b{attempts}"
    con.execute(
        "UPDATE jobs SET batch_id=?, attempts=?, status=?, updated_at=datetime('now') "
        "WHERE job_id=?",
        (batch_id, attempts, models.RUNNING, job_id),
    )
    return batch_id


def current_batch_id(con, job_id: str) -> str | None:
    row = con.execute("SELECT batch_id FROM jobs WHERE job_id=?", (job_id,)).fetchone()
    return row[0] if row else None


def reject_delivery(con, *, batch_id: str, gen_id: str, reason: str,
                    payload_hash: str) -> str:
    rid = keyer.short_hash(["reject", batch_id, reason, payload_hash], 20)
    con.execute(
        "INSERT OR IGNORE INTO rejected_deliveries(reject_id, batch_id, gen_id, "
        "reason, payload_hash) VALUES(?,?,?,?,?)",
        (rid, batch_id, gen_id, reason, payload_hash),
    )
    return rid


def rejected_count(con, gen_id: str | None = None) -> int:
    if gen_id:
        return con.execute(
            "SELECT COUNT(*) FROM rejected_deliveries WHERE gen_id=?", (gen_id,),
        ).fetchone()[0]
    return con.execute("SELECT COUNT(*) FROM rejected_deliveries").fetchone()[0]


def mark_job(con, job_id: str, status: str, snapshot_id: str | None = None,
             error: str = "") -> None:
    con.execute(
        "UPDATE jobs SET status=?, snapshot_id=?, last_error=?, "
        "updated_at=datetime('now') WHERE job_id=?",
        (status, snapshot_id, error, job_id),
    )


def release_job_leases(con, job_id: str, status: str, error: str = "") -> None:
    """作业未能确认时，释放该作业下仍被占用的全部站点任务租约，
    使其回到可接续状态（终态任务不受影响）。"""
    con.execute(
        "UPDATE tasks SET status=?, lease_owner=NULL, lease_expires_at=NULL, "
        "last_error=?, updated_at=datetime('now') "
        "WHERE job_id=? AND status=?",
        (status, error, job_id, models.RUNNING),
    )


def _iso(ts: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(ts))
