"""保留原有合法输入：通用记录流水线与幂等。"""
from serptrack import pipeline, store


def test_pipeline_runs_and_idempotent(tmp_path):
    con = store.connect(str(tmp_path / "legacy.db"))
    a = pipeline.run(con=con)
    b = pipeline.run(con=con)
    assert a["fed"] == 4
    assert b["rows"] == a["rows"] == 3  # r-003 重复被去重
    con.close()


def test_store_roundtrip(tmp_path):
    con = store.connect(str(tmp_path / "legacy.db"))
    try:
        assert store.count(con) >= 0
    finally:
        con.close()


def test_original_input_preserved(tmp_path):
    con = store.connect(str(tmp_path / "legacy.db"))
    pipeline.run(con=con)
    rows = dict(store.all_records(con))
    assert "r-001" in rows and "r-002" in rows and "r-003" in rows
    # 规范化后的合法字段仍在
    assert rows["r-001"]["src"] == "alpha"
    assert rows["r-001"]["name"] == "Record One"
    con.close()
