"""单一职责：从本地 fixtures 读取输入（离线摄取入口，不联网）。

- load(): 保留原有合法输入（通用记录）。
- load_matrix()/load_pages(): SERP 采集域的矩阵与页面夹具。
"""
import json
from .config import FIXTURES, MATRIX_PATH, PAGES_PATH


def _read_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def load():
    return _read_json(FIXTURES)


def load_matrix(path=None) -> dict:
    return _read_json(path or MATRIX_PATH)


def load_pages(path=None) -> dict:
    return _read_json(path or PAGES_PATH)
