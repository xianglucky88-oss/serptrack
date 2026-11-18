"""地域回退与查询计划：回退归入实际条件，原条件历史不串。"""
from serptrack.geo_locale import resolve
from serptrack.models import Context
from serptrack.queryplan import build_plan
from serptrack import keyer
from conftest_support import normalized_matrix


def test_no_fallback_when_no_rule_matches():
    ctx = Context("US", "en", "desktop")
    r = resolve(ctx, [])
    assert r.fallback_applied is False
    assert r.actual.as_tuple() == ("US", "en", "desktop")
    assert r.requested.as_tuple() == ("US", "en", "desktop")


def test_geo_fallback_routes_to_actual_context():
    matrix = normalized_matrix()
    r = resolve(Context("DE", "de", "mobile"), matrix["fallback"])
    assert r.fallback_applied is True
    assert r.reason == "geo_unavailable"
    assert r.requested.geo == "DE"
    assert r.actual.geo == "EU"  # 回退后归入实际条件
    assert r.actual.lang == "de" and r.actual.device == "mobile"


def test_plan_keeps_requested_and_actual_on_every_task():
    matrix = normalized_matrix()
    version = keyer.config_version(matrix)
    gens, jobs, tasks = build_plan(matrix, version)
    de_tasks = [t for t in tasks if t["req"].geo == "DE"]
    assert de_tasks and all(t["act"].geo == "EU" for t in de_tasks)
    assert all(t["fallback_applied"] for t in de_tasks)
    us_tasks = [t for t in tasks if t["req"].geo == "US"]
    assert all(t["act"].geo == "US" and not t["fallback_applied"] for t in us_tasks)


def test_context_keys_separate_requested_from_actual_history():
    # 回退到 EU 的数据必须落在 EU 历史键下，绝不进入 DE 原条件历史
    k_eu = keyer.context_key("best vpn", "acme", "EU", "de", "mobile")
    k_de = keyer.context_key("best vpn", "acme", "DE", "de", "mobile")
    assert k_eu != k_de


def test_plan_shape_and_deterministic_ids():
    matrix = normalized_matrix()
    version = keyer.config_version(matrix)
    gens, jobs, tasks = build_plan(matrix, version)
    assert {g["gen_id"] for g in gens} == {"g1", "g2"}
    # 3 关键词 × 2 条件 = 6 作业；× 3 站点 = 18 任务/代次
    assert len(jobs) == 12 and len(tasks) == 36
    # 同矩阵两次构建得到相同键（可恢复、可重放）
    _, jobs2, tasks2 = build_plan(matrix, version)
    assert {j["job_id"] for j in jobs} == {j["job_id"] for j in jobs2}
    assert {t["task_id"] for t in tasks} == {t["task_id"] for t in tasks2}


def test_config_version_changes_when_matrix_changes():
    m1 = normalized_matrix()
    m2 = normalized_matrix()
    m2["budget_total"] = 5
    assert keyer.config_version(m1) != keyer.config_version(m2)
