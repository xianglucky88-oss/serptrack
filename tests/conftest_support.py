"""测试公用夹具与构造助手。"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from serptrack import store as _store          # noqa: E402
from serptrack.fetcher import MockFetcher      # noqa: E402
from serptrack.loader import load_matrix, load_pages  # noqa: E402
from serptrack.normalizer import normalize_matrix  # noqa: E402
from serptrack.runner import run_domain        # noqa: E402


def fresh_con(tmp_path) -> _store.sqlite3.Connection:
    con = _store.connect(str(tmp_path / "test.db"))
    return con


def normalized_matrix():
    return normalize_matrix(load_matrix())


def default_fetcher(**kw):
    return MockFetcher(load_pages(), **kw)


def run_full(tmp_path, fetcher=None, matrix=None):
    con = fresh_con(tmp_path)
    matrix = matrix or normalized_matrix()
    stats = run_domain(con, matrix, fetcher or default_fetcher())
    return con, stats
