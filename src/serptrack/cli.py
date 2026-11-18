"""命令行入口。

- 默认 / `legacy`：原有通用记录流水线（保留原合法输入链路）。
- `serp`：执行 SERP 代次采集（可恢复）。
- `explain`：输出某关键词×站点×条件的排名变化解释与不可比较原因。
- `trace`：由快照 id 追溯页面、解析条目与排名点。
"""
import json
import sys

from . import pipeline, snapshot, store
from .explain import explain, integrity_counts


def _print(obj):
    print(json.dumps(obj, ensure_ascii=False, indent=2, default=str))


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    cmd = argv[0] if argv else "legacy"
    if cmd in ("legacy", "run"):
        _print(pipeline.run())
        return 0
    if cmd == "serp":
        _print(pipeline.run_serp())
        return 0
    if cmd == "explain":
        # explain <keyword> <site_id> <geo> <lang> <device>
        _, keyword, site_id, geo, lang, device = argv
        con = store.connect()
        try:
            _print(explain(con, keyword=keyword, site_id=site_id, geo=geo,
                           lang=lang, device=device))
        finally:
            con.close()
        return 0
    if cmd == "integrity":
        _, keyword, site_id, geo, lang, device = argv
        con = store.connect()
        try:
            _print(integrity_counts(con, keyword=keyword, site_id=site_id,
                                    geo=geo, lang=lang, device=device))
        finally:
            con.close()
        return 0
    if cmd == "trace":
        _, snapshot_id = argv
        con = store.connect()
        try:
            _print(snapshot.trace_snapshot(con, snapshot_id))
        finally:
            con.close()
        return 0
    print(f"unknown command: {cmd}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
