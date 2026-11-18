"""单一职责：集中管理本地路径与运行默认值（离线，不联网）。"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "fixtures" / "input.json"
SERP_DIR = ROOT / "fixtures" / "serp"
MATRIX_PATH = SERP_DIR / "matrix.json"
PAGES_PATH = SERP_DIR / "pages.json"
DB_PATH = ROOT / "serptrack.db"

DEFAULT_BUDGET = 100
DEFAULT_CONCURRENCY = 4
DEFAULT_LEASE_SECONDS = 30
