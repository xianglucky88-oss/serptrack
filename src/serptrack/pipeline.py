"""薄编排。

- run(): 保留原有通用记录流水线（loader→normalizer→queryplan→serp_parser→
  snapshot→dedup→keyer→store），不含业务判断。
- run_serp(): SERP 采集域入口，委托 runner 执行可恢复的代次采集。
"""
import importlib

from . import loader as _loader
from . import store as _store
from .keyer import key_of
from .models import Record

CHAIN_NAMES = ['normalizer', 'queryplan', 'serp_parser', 'snapshot', 'dedup']


def run(con=None):
    own = con is None
    con = con or _store.connect()
    try:
        chain = [importlib.import_module("." + n, __package__).process
                 for n in CHAIN_NAMES]
        fed = 0
        for raw in _loader.load():
            rec = raw
            for fn in chain:
                rec = fn(rec)
            _store.upsert(con, Record(key=key_of(rec), data=rec))
            fed += 1
        _store.commit(con)
        return {"fed": fed, "rows": _store.count(con)}
    finally:
        if own:
            con.close()


def run_serp(con=None, matrix_path=None, fetcher=None, **kwargs):
    """SERP 域执行入口；fetcher 可注入（离线回放/故障模拟）。"""
    from .fetcher import MockFetcher
    from .normalizer import normalize_matrix
    from .runner import run_domain

    matrix = normalize_matrix(_loader.load_matrix(matrix_path))
    fetcher = fetcher or MockFetcher(_loader.load_pages())
    own = con is None
    con = con or _store.connect()
    try:
        return run_domain(con, matrix, fetcher, **kwargs)
    finally:
        if own:
            con.close()
