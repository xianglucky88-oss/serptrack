"""可恢复性：预算、并发租约、站点公平、请求失败、进程重启、旧批次守门、配置更新。"""
import time

from conftest_support import fresh_con, normalized_matrix, default_fetcher
from serptrack import ledger, models, keyer
from serptrack.fetcher import MockFetcher
from serptrack.loader import load_pages
from serptrack.runner import run_domain


def _run(con, matrix=None, fetcher=None, **kw):
    return run_domain(con, matrix or normalized_matrix(),
                      fetcher or default_fetcher(), **kw)


def test_budget_stops_progress_and_resumes_afterwards(tmp_path):
    matrix = normalized_matrix()
    matrix["budget_total"] = 3  # 只够 3 次页面请求
    con = fresh_con(tmp_path)
    stats = _run(con, matrix=matrix)
    used = ledger.budget_used(con, keyer.config_version(matrix))
    assert used == 3
    assert not all(g["status"]["all_confirmed"] for g in stats["generations"])
    # 未完成代次不得发布
    assert con.execute(
        "SELECT COUNT(*) FROM rank_history WHERE published=1").fetchone()[0] == 0
    # 提高预算后续跑：只接续未确认工作，最终全部确认并发布
    matrix2 = normalized_matrix()
    matrix2["budget_total"] = 100
    _run(con, matrix=matrix2)
    remaining = con.execute(
        "SELECT COUNT(*) FROM tasks WHERE status NOT IN (?,?)",
        models.TERMINAL).fetchone()[0]
    assert remaining == 0
    assert con.execute(
        "SELECT COUNT(*) FROM rank_history WHERE published=1").fetchone()[0] == 36
    con.close()


def test_fair_scheduling_rotates_sites(tmp_path):
    matrix = normalized_matrix()
    matrix["max_concurrency"] = 3
    con = fresh_con(tmp_path)
    now = 1_000_000.0
    from serptrack.queryplan import build_plan, persist_plan
    version = keyer.config_version(matrix)
    persist_plan(con, version, matrix, build_plan(matrix, version))
    claimed = ledger.claim_tasks(
        con, gen_id="g1", owner="t", lease_seconds=30,
        max_concurrency=3, now=now)
    # 首轮三个站点各一个任务（站点公平），而不是同站点连占
    assert sorted(t["site_id"] for t in claimed) == ["acme", "nimbus", "zephyr"]
    con.close()


def test_expired_lease_is_reclaimed_after_restart(tmp_path):
    con = fresh_con(tmp_path)
    matrix = normalized_matrix()
    from serptrack.queryplan import build_plan, persist_plan
    version = keyer.config_version(matrix)
    persist_plan(con, version, matrix, build_plan(matrix, version))
    ledger.claim_tasks(con, gen_id="g1", owner="dead-runner",
                       lease_seconds=30, max_concurrency=2, now=1000.0)
    running = con.execute(
        "SELECT COUNT(*) FROM tasks WHERE status=?", (models.RUNNING,)).fetchone()[0]
    assert running == 2
    # 租约未过期不回收
    assert ledger.reclaim_expired_leases(con, 30, now=1010.0) == 0
    # 模拟进程重启、时间越过租约期
    reclaimed = ledger.reclaim_expired_leases(con, 30, now=2000.0)
    assert reclaimed == 2
    assert con.execute(
        "SELECT COUNT(*) FROM tasks WHERE status=?",
        (models.RUNNING,)).fetchone()[0] == 0
    # 终态任务永不被回收
    con.execute("UPDATE tasks SET status=? WHERE task_id=(SELECT task_id FROM tasks LIMIT 1)",
                (models.DONE,))
    assert ledger.reclaim_expired_leases(con, 30, now=9999.0) == 0
    con.close()


def test_request_failure_keeps_work_unconfirmed_then_succeeds(tmp_path):
    fail_key = "g1|best vpn|US|en|desktop|1"
    matrix = normalized_matrix()
    # fail_keys：首次运行期间持续失败，作业必须保持未确认而不是被当作“未出现”
    con = fresh_con(tmp_path)
    _run(con, matrix=matrix, fetcher=MockFetcher(load_pages(), fail_keys={fail_key}))
    assert con.execute(
        "SELECT COUNT(*) FROM tasks WHERE status=?",
        (models.FAILED,)).fetchone()[0] >= 1
    # 失败作业未产出任何快照/排名点（未采到不等于未出现）；其他作业的点正常存在
    failed_job = con.execute(
        "SELECT job_id FROM jobs WHERE status=?", (models.FAILED,)).fetchone()[0]
    assert con.execute(
        "SELECT COUNT(*) FROM tasks WHERE job_id=? AND snapshot_id IS NOT NULL",
        (failed_job,)).fetchone()[0] == 0
    assert con.execute(
        "SELECT COUNT(*) FROM rank_history WHERE gen_id='g1'").fetchone()[0] == 15
    # 恢复后（故障解除）只接续未确认工作，最终全部确认并发布
    _run(con, matrix=matrix, fetcher=MockFetcher(load_pages()))
    assert con.execute(
        "SELECT COUNT(*) FROM tasks WHERE status NOT IN (?,?)",
        models.TERMINAL).fetchone()[0] == 0
    assert con.execute(
        "SELECT COUNT(*) FROM rank_history WHERE published=1").fetchone()[0] == 36
    con.close()


def test_transient_failure_is_retried_within_same_run(tmp_path):
    fail_key = "g1|best vpn|US|en|desktop|1"
    matrix = normalized_matrix()
    # fail_once：只失败一次，滚动接续时重试成功，终态全部确认
    con = fresh_con(tmp_path)
    _run(con, matrix=matrix,
         fetcher=MockFetcher(load_pages(), fail_once={fail_key}))
    assert con.execute(
        "SELECT COUNT(*) FROM tasks WHERE status NOT IN (?,?)",
        models.TERMINAL).fetchone()[0] == 0
    con.close()


def test_stale_batch_delivery_is_rejected_and_recorded(tmp_path):
    con = fresh_con(tmp_path)
    matrix = normalized_matrix()
    from serptrack.queryplan import build_plan, persist_plan
    version = keyer.config_version(matrix)
    persist_plan(con, version, matrix, build_plan(matrix, version))
    b1 = ledger.next_batch_id(con, con.execute(
        "SELECT job_id FROM jobs LIMIT 1").fetchone()[0])
    b2 = ledger.current_batch_id(con, con.execute(
        "SELECT job_id FROM jobs LIMIT 1").fetchone()[0])
    assert b1 == b2
    # 作业重新开批后，旧批次号不再是当前批次
    job_id = b1.split(":b")[0]
    ledger.next_batch_id(con, job_id)
    stale = b1
    current = ledger.current_batch_id(con, job_id)
    assert stale != current
    rid = ledger.reject_delivery(con, batch_id=stale, gen_id="g1",
                                 reason="stale batch", payload_hash="h")
    assert ledger.rejected_count(con, "g1") == 1
    row = con.execute("SELECT * FROM rejected_deliveries WHERE reject_id=?",
                      (rid,)).fetchone()
    assert row["batch_id"] == stale and row["reason"] == "stale batch"
    con.close()


def test_config_update_does_not_reset_confirmed_work(tmp_path):
    con = fresh_con(tmp_path)
    _run(con)  # 首版矩阵全部完成
    done_before = con.execute(
        "SELECT COUNT(*) FROM tasks WHERE status=?", (models.DONE,)).fetchone()[0]
    # 矩阵业务内容不变 → 同 config_version，计划幂等，状态原样保留
    matrix = normalized_matrix()
    _run(con, matrix=matrix)
    done_after = con.execute(
        "SELECT COUNT(*) FROM tasks WHERE status=?", (models.DONE,)).fetchone()[0]
    assert done_before == done_after
    con.close()


def test_restart_resumes_only_unconfirmed_work(tmp_path):
    # 预算截断 → 模拟重启 → 新 run 只动未确认任务
    matrix = normalized_matrix()
    matrix["budget_total"] = 2
    con = fresh_con(tmp_path)
    _run(con, matrix=matrix)
    open_tasks = con.execute(
        "SELECT COUNT(*) FROM tasks WHERE status NOT IN (?,?)",
        models.TERMINAL).fetchone()[0]
    assert open_tasks > 0
    full = normalized_matrix()
    full["budget_total"] = 100
    _run(con, matrix=full)
    assert con.execute(
        "SELECT COUNT(*) FROM tasks WHERE status NOT IN (?,?)",
        models.TERMINAL).fetchone()[0] == 0
    # 所有 run 的预算累计持久化（跨进程重启可恢复推进）
    runs = con.execute("SELECT COUNT(*) AS n FROM runs").fetchone()["n"]
    assert runs >= 2
    con.close()
