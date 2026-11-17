"""单一职责：对记录做单一职责变换：snapshot。（作为流水线阶段）"""
def process(record):
    rec = dict(record)
    rec.setdefault("_stage_snapshot", "ok")
    return rec
