# WOBS2 多子集列包接入交接说明

本扩展在不改动 WOBS1 业务语义的前提下，新增 `Archive.ingest_columns`，接入供应商把
多个同站/异站、变长复制子集共同压缩成一列的 WOBS2 包。旧入口 `ingest` 仍只收版本1，
旧数据、旧接口、五个原测试文件与既有日成果全部保留；新增 `tests/test_columns.py`
（26 个用例）与独立核账 `columns_audit.py`。

## 代码落点

- `weather/decoding/columns.py`（新增）：WOBS2 包头解码、差分列读取、按子集重建 V1
  载荷、逐子集复用 V1 解码/组装/校验。
- `weather/archive/imports.py`：新增 `ingest_columns`，抽出 `_require_registered_binding`
  供两个入口共用；完整暂存、摘要、重放冲突沿用 V1。
- `weather/observations/models.py`：`Observation` 新增末尾可选字段 `subset_index`
  （WOBS2 为 0 基下标，WOBS1 与历史记录为 `None`）。
- `weather/archive/codecs.py`：观测记录持久化 `subset_index`；旧记录无该键时读回 `None`。
- 日加工、质量三态、精确 Fraction、事务发布与回滚均未改算法，直接消费新观测。

## 1. 活动位置如何变成实际发生

包头按序给出 1～16 个站点/日期子集，根调用的活动集合 A 是全部子集、次数均为 1。
复制组一次调用的 A 只包含“实际到达同一模板路径、同一父复制路径”的子集；固定次数取自
模板，延迟次数先以 `count_width` 为宽度读成一列（全1仍为非法保留次数，交给子集 V1
解码器拒绝）。对轮次 i 从 0 到最大次数减一，只有次数大于 i 的子集进入本轮，再按模板
节点顺序处理字段与子组。次数为 0 的子集不进迭代体，因此不产生字段发生；没有到达该调用
的子集不参加它的任何列。

实现上 `walk_columns` 同步遍历所有子集：标量列按“base + 6位 k + 按活动子集升序的 k 位
差值”读取，k=0 时全部复用 base、不读差值；校验 `k<=w` 且 `base+delta < 2**w`，否则
`DecodeError`。每个字段值写入对应子集自己的 V1 位段，延迟组在子组位段前补回它的次数字。
逐子集重建出独立的 V1 载荷后，直接调用既有 `expand_template`、`assemble_observations`
和 `validate_batch_observations`，所以路径、复制发生路径、组身份与 V1 完全同构。最后按
子集下标升序、各子集内 V1 深度优先顺序，把 `sequence` 从 0 全批连续重排，缺测照常编号。

## 2. 质量引用何时完整

尾质量块在“该调用所有轮次全部结束后”处理一次，不是每轮一个。先读一个 32 位长度列，
跨度是本次调用的全部活动子集（零次子集仍在其中，长度为 0）；每个长度必须等于该子集在
本调用的直接字段发生数，嵌套子组成员不占位，不符即 `ObservationError`。随后对成员位置
j=0..max(L)-1 形成 w=1 选择列，活动集合仅为 L>j 的子集；全部选择列读完后，再按同样的
j 顺序形成 w=q 质量字列，活动集合仅为该位置被选中的子集，无人选中就不编质量字列。

重建到某个子集的 V1 位段时，按它自己的局部成员位置输出“32位长度 + 全部选择位 + 按选择
顺序的质量字”，与 FORMAT.md 的 V1 尾块字节布局一致，再由 V1 的 `resolve_group_quality`
统一解释：未选中 UNSELECTED、质量字全1 MISSING、其余 VALUE（含 0）。因此引用域始终锁定
在本调用本子集的直接成员内，不会向父组、子组、相邻组或别的子集借位。无 quality 描述符
的组不编任何这些列，直接成员全部 UNSELECTED。

## 3. 子集与全批身份如何保留

每条观测持久化三类身份：`subset_index`（包内 0 基子集下标）、原 V1 发生路径
（`occurrence.template_path`、`occurrence_path`、`group.template_path`、
`group.parent_occurrence`）以及全批连续 `sequence`。站点与日期取所属子集的头项，不被
压缩列号或包头覆盖；同站同日、同模板同复制路径的两个子集仍是两条独立发生，日加工不去重，
子集也不是额外分组键。记录以 JSON 存入既有 `observations` 表，重开库只读记录、不重新
解码；WOBS1 旧记录缺 `subset_index` 时读为 `None`，旧库无需迁移。

发布边界与 V1 一致：先在私有暂存字节上完整解码全部子集，任一子集非法或 SQLite 写入失败
都在发布前抛出，整包不新增批次/观测/回执，旧资料与日成果不变，不能逐子集提交后拼回执。
摘要覆盖确切绑定与含版本、全部子集和填充在内的完整包字节；同号同绑定同字节重放返回原
回执且仍完整消费源，异输入抛 `ConflictError`。

## 运行与核账

- 全量回归：`python3.11 -B verify.py`（发现并运行含 `test_columns.py` 在内的全部测试）。
- 手工列核账：`python3.11 -B columns_cases.py fixtures|verify|demo`。
- 独立小例核账：`python3.11 -B columns_audit.py`，自拼一个两子集固定复制包，逐项核对
  发生、三态质量、全批序号、子集站点日期、日账排除计数以及真实 SQLite 重开。
- 演示：`python3.11 -B demo.py` 在同一库先跑 V1 场景，再真实接入不等长复制（ragged）、
  区分同形子集（scopes）、读取日账并重开核对。
- 运行清单 `RUN_MANIFEST.json` 已在保留原检查项的基础上加入列包正常/异常/持久边界与
  上述核账入口；`.pointwise-runtime.json` 的 verify/demo 约定不变。
