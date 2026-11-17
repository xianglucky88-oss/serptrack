"""单一职责：对记录做单一职责变换：snapshot。（基线朴素实现，已接入 happy-path）"""
def process(record):
    rec = dict(record)
    rec.setdefault("_stage_snapshot", "ok")
    return rec
