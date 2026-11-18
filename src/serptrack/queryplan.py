"""单一职责：把查询矩阵展开成可恢复的采集计划（代次 → 作业 → 站点任务）。

- 一个作业 = 代次 × 关键词 × 请求条件；同条件下多个站点共享一次抓取。
- 一个任务 = 作业 × 站点，承载站点排名结论与租约。
- 解析地域回退：任务/作业同时记录请求条件与实际条件。
- 计划写入是幂等 upsert：配置更新或重启后已存在的计划不被重置，
  未确认工作（pending/running/failed/...）可原样接续。
- process(): 兼容通用记录流水线的占位变换（SERP 域使用 build_plan）。
"""
from . import keyer
from .geo_locale import resolve
from .models import Context
import json


def process(record):
    rec = dict(record)
    rec.setdefault("_stage_queryplan", "naive")
    return rec


def build_plan(matrix: dict, config_version: str):
    """纯函数：返回 (generations, jobs, tasks) 三个字典列表，不碰数据库。"""
    generations, jobs, tasks = [], [], []
    fallback_rules = matrix.get("fallback", [])
    for gen in matrix.get("generations", []):
        gen_id = str(gen["gen_id"])
        generations.append({
            "gen_id": gen_id,
            "config_version": config_version,
            "label": str(gen.get("label", "")),
        })
        for keyword in matrix["keywords"]:
            for c in matrix["contexts"]:
                ctx = Context(**c)
                resolved = resolve(ctx, fallback_rules)
                jid = keyer.job_id(gen_id, keyword, ctx.geo, ctx.lang, ctx.device)
                jobs.append({
                    "job_id": jid,
                    "gen_id": gen_id,
                    "config_version": config_version,
                    "keyword": keyword,
                    "req": resolved.requested,
                    "act": resolved.actual,
                    "fallback_applied": resolved.fallback_applied,
                    "fallback_reason": resolved.reason,
                })
                for site in matrix["sites"]:
                    tid = keyer.task_id(gen_id, keyword, site["site_id"],
                                        ctx.geo, ctx.lang, ctx.device)
                    tasks.append({
                        "task_id": tid,
                        "job_id": jid,
                        "gen_id": gen_id,
                        "keyword": keyword,
                        "site_id": site["site_id"],
                        "host": site["host"],
                        "req": resolved.requested,
                        "act": resolved.actual,
                        "fallback_applied": resolved.fallback_applied,
                        "fallback_reason": resolved.reason,
                    })
    return generations, jobs, tasks


def persist_plan(con, config_version: str, matrix: dict, plan) -> None:
    """幂等写入计划；只插入新行，绝不回退已有状态。"""
    generations, jobs, tasks = plan
    con.execute(
        "INSERT OR IGNORE INTO config_versions(config_version, matrix_id, matrix_json) "
        "VALUES(?,?,?)",
        (config_version, matrix["matrix_id"], json.dumps(matrix, ensure_ascii=False)),
    )
    for g in generations:
        con.execute(
            "INSERT OR IGNORE INTO generations(gen_id, config_version, label) VALUES(?,?,?)",
            (g["gen_id"], g["config_version"], g["label"]),
        )
    for j in jobs:
        req, act = j["req"], j["act"]
        con.execute(
            "INSERT OR IGNORE INTO jobs(job_id, gen_id, config_version, keyword, "
            "req_geo, req_lang, req_device, act_geo, act_lang, act_device, "
            "fallback_applied, fallback_reason) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (j["job_id"], j["gen_id"], j["config_version"], j["keyword"],
             req.geo, req.lang, req.device, act.geo, act.lang, act.device,
             int(j["fallback_applied"]), j["fallback_reason"]),
        )
    for t in tasks:
        req, act = t["req"], t["act"]
        con.execute(
            "INSERT OR IGNORE INTO tasks(task_id, job_id, gen_id, keyword, site_id, host, "
            "req_geo, req_lang, req_device, act_geo, act_lang, act_device, "
            "fallback_applied, fallback_reason) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (t["task_id"], t["job_id"], t["gen_id"], t["keyword"], t["site_id"], t["host"],
             req.geo, req.lang, req.device, act.geo, act.lang, act.device,
             int(t["fallback_applied"]), t["fallback_reason"]),
        )
    con.commit()
