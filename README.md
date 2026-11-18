# serptrack — 查询×站点矩阵采 SERP，可信地判断排名是否真的下降

运行栈：Python 3.11+ 标准库 `sqlite3`（离线，无需联网、无第三方运行时依赖）；
`httpx`/`pytest` 仅在可用环境下使用。仓库自带零依赖测试运行器 `mini_pytest.py`。

## 要解决的问题
地域切换、广告位变化、页面没采完整都可能被误报成“排名下降”。本系统把
**查询计划 → 页面解析 → 快照 → 排名历史 → 变化解释** 全程接通并留证据，
严格区分：自然结果 / 广告 / 特殊块，以及 **未出现 / 未采到 / 无法判断**。

## 运行方式
```text
make run      # SERP 代次采集（可恢复推进，落 sqlite）
make legacy   # 原有通用记录流水线（保留原合法输入）
make test     # 优先 pytest；环境无 pytest 时自动改用 mini_pytest.py
```
CLI 子命令：
```text
python -m serptrack.cli serp                                    # 执行采集
python -m serptrack.cli explain <keyword> <site> <geo> <lang> <device>
python -m serptrack.cli integrity <keyword> <site> <geo> <lang> <device>
python -m serptrack.cli trace <snapshot_id>
```

## 数据模型与关键规则
- **采集上下文五维**：关键词 × 站点 × 地域 × 语言 × 设备，分别保存
  *请求条件* 与 *实际条件*。地域回退后数据只进入实际条件历史（`rank_history`
  的 `context_key` 基于实际 geo/lang/device），原条件历史查不到该数据，
  回退原因（如 `geo_unavailable`）随证据留存。
- **解析分离**：`organic`/`ad`/`special` 独立编号；同 URL 多个出现位置全部保留
  （同类型跨页连续 `position` + 全块 `ordinal`）；未知块隔离为 special，不污染自然排名。
- **三态区分**：
  - `ranked`：完整快照中找到站点；
  - `not_appeared`：完整快照中确认没有（真未出现）；
  - `unknown`：采到了但截断/异步未加载（无法判断）；
  - `not_collected`：计划存在但没有快照（未采到，任务保持未确认，可恢复重试）。
- **完整性证据**：`complete` / `truncated:<page>` / `async_pending:<name>` /
  `paginated:pages=N` 随快照落库，跨页移动、异步未加载、截断均可追溯。
- **幂等**：快照、页面、解析条目、排名点全部确定性键 + upsert，
  重复采集不新增快照、不新增排名点；同代次内容变化只更新同一排名点。
- **批次守门**：作业每次尝试分配单调递增 `batch_id`；交付时批次过期/未知则
  拒绝并写 `rejected_deliveries`，旧响应不能覆盖新批次。
- **整代次发布**：所有任务确认（done/incomplete）后代次整体 `published`；
  解释层只读已发布代次。未确认任务存在时代次保持 open。
- **可恢复与公平**：预算按配置版本跨 run 累计持久化；并发通过租约控制，
  认领按站点轮询（每轮每站点最多一个任务）；进程重启回收过期租约，
  失败/预算中断的任务回到 pending，只接续未确认工作，终态永不重置。

## 模块边界（单一职责）
| 模块 | 职责 |
| --- | --- |
| `loader` / `normalizer` | 离线读取与字段/主机/矩阵规范化 |
| `geo_locale` | 条件解析与回退（请求条件 vs 实际条件） |
| `queryplan` | 矩阵展开为代次→作业→站点任务，幂等落计划 |
| `fetcher` | 离线页面回放与故障注入（不联网） |
| `serp_parser` | 三类块解析、位置编号、完整性证据 |
| `snapshot` | 快照/页面/条目幂等持久化与双向追溯 |
| `dedup` | 同主机多位置归并、三态分类 |
| `rankstore` | 排名历史幂等写入、整代次发布、历史查询 |
| `ledger` | 租约、预算、公平调度、恢复、批次守门 |
| `runner` | 领域编排（对应通用 `pipeline`） |
| `explain` | 排名变化解释与不可比较原因、三态计数 |
| `store` / `keyer` / `models` / `config` | 持久化、稳定键、数据结构、路径配置 |
| `pipeline` / `cli` | 通用流水线（保留）与 SERP 域入口 |

## 夹具场景（`fixtures/serp/`）
`matrix.json` 定义 3 关键词 × 3 站点 × 2 条件 × 2 代次；`pages.json` 覆盖：
广告插入但自然位不变、真实自然排名下降、跨页移动后消失、截断+异步未加载、
同 URL 多位置、DE→EU 地域回退、完全未出现等，供端到端判定与解释使用。
