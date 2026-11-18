"""单一职责：离线 SERP 抓取（不联网）。

按 代次×关键词×实际条件×页码 回放 fixtures 中的页面；网络失败通过
fail_after/fail_keys 注入，供恢复性测试使用。预算计数由 runner 负责，
本模块只负责“取页面”这一件事，并保证同一参数得到同一内容（确定性）。
"""
from .models import PagePayload


class FetchError(Exception):
    """可恢复的请求失败：调用方应保留未确认状态并在后续运行中重试。"""


class MockFetcher:
    def __init__(self, pages_doc: dict, fail_keys: set[str] | None = None,
                 fail_once: set[str] | None = None):
        self._index = {}
        for p in pages_doc.get("pages", []):
            key = (p["gen_id"], p["keyword"], p["geo"], p["lang"], p["device"], int(p["page"]))
            self._index[key] = p
        self.fail_keys = set(fail_keys or ())
        self.fail_once = set(fail_once or ())
        self.calls: list[tuple] = []

    def _key_str(self, gen_id, keyword, geo, lang, device, page) -> str:
        return "|".join([gen_id, keyword, geo, lang, device, str(page)])

    def fetch(self, gen_id: str, keyword: str, geo: str, lang: str,
              device: str, page: int = 1) -> PagePayload:
        key = (gen_id, keyword, geo, lang, device, page)
        key_str = self._key_str(*key)
        self.calls.append(key)
        if key_str in self.fail_keys or key_str in self.fail_once:
            self.fail_once.discard(key_str)
            raise FetchError(f"simulated request failure: {key_str}")
        raw = self._index.get(key)
        if raw is None:
            if page == 1:
                raise FetchError(f"page unavailable: {key_str}")
            raise StopIteration("no more pages")
        return PagePayload(
            gen_id=raw["gen_id"], keyword=raw["keyword"], geo=raw["geo"],
            lang=raw["lang"], device=raw["device"], page=int(raw["page"]),
            complete=bool(raw["complete"]), truncated=bool(raw.get("truncated", False)),
            async_pending=list(raw.get("async_pending", [])),
            blocks=list(raw.get("blocks", [])),
        )
