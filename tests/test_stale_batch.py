"""端到端批次守门：抓取期间批次被轮换时，旧响应必须被拒绝且不落快照。"""
from conftest_support import fresh_con, normalized_matrix, default_fetcher
from serptrack import ledger, keyer, models
from serptrack.fetcher import MockFetcher
from serptrack.runner import StaleDelivery, _process_job
from serptrack.queryplan import build_plan, persist_plan


class RotatingFetcher:
    """首次抓取时让作业在另一个工作流里重开批次，模拟响应在途期间批次轮换。"""

    def __init__(self, inner, con, job_id):
        self.inner = inner
        self.con = con
        self.job_id = job_id
        self.rotated = False

    def fetch(self, *args, **kwargs):
        page = self.inner.fetch(*args, **kwargs)
        if not self.rotated:
            self.rotated = True
            ledger.next_batch_id(self.con, self.job_id)
            self.con.commit()
        return page


def test_stale_response_cannot_overwrite_new_batch(tmp_path):
    con = fresh_con(tmp_path)
    matrix = normalized_matrix()
    version = keyer.config_version(matrix)
    persist_plan(con, version, matrix, build_plan(matrix, version))
    run_id = ledger.start_run(con, version, matrix["budget_total"],
                              matrix["max_concurrency"])
    job = dict(con.execute("SELECT * FROM jobs LIMIT 1").fetchone())

    fetcher = RotatingFetcher(default_fetcher(), con, job["job_id"])
    try:
        _process_job(con, matrix, fetcher, version, job["gen_id"], job, run_id)
        raised = False
    except StaleDelivery:
        raised = True
    assert raised is True
    # 旧响应没有产生快照；runner 层捕获 StaleDelivery 后负责留痕（这里复用同一入口）
    assert con.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0] == 0
    ledger.reject_delivery(
        con, batch_id=f"{job['job_id']}:b1", gen_id=job["gen_id"],
        reason="stale batch (simulated runner handler)", payload_hash="x")
    assert ledger.rejected_count(con, job["gen_id"]) == 1
    job_row = con.execute("SELECT status FROM jobs WHERE job_id=?",
                          (job["job_id"],)).fetchone()
    # 作业已开 b2，b1 响应被拒后由恢复流程接续
    assert job_row["status"] in (models.FAILED, models.RUNNING)
    con.close()
