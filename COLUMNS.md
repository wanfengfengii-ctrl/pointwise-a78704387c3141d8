# WOBS2 多子集列包

README.md、FORMAT.md 和原五个测试文件仍定义完整的 WOBS1 业务。本扩展只增加有限列包接入；不实现通用 BUFR，不增加流式内存、并行或恢复要求。可以完整暂存输入、重建每个子集，并复用 V1 解码、质量处理与事务。

## 入口与结果

新增 Archive.ingest_columns(batch_id, template_binding, byte_chunks)，参数类型、绑定真实性、完整源消费、摘要公式与 ImportReceipt 沿用 Archive.ingest。此入口只接纳版本2，原 ingest 仍只接纳版本1。完整包字节（包括版本、全部子集和填充）参与既有 compute_input_digest 公式；两入口共用 batch_id 命名空间，不按子集拆成多个公开批次。同号同绑定同字节重放返回原回执，同号异输入抛 ConflictError；重放仍须消费完整源，源异常保留原对象。

read_observations 返回原不可变 Observation 元组，新增公开属性 subset_index：WOBS2 为0基包内子集下标，WOBS1 为 None。原有 occurrence 的路径、复制路径、group.template_path 和 group.parent_occurrence 沿用 V1 子集内定义；sequence 则按子集下标升序、各子集 V1 深度优先顺序从0全批连续编号。相同站点日期、相同模板及复制路径的两个子集仍是两个独立发生，缺测仍编号。每条 station、day 取所属子集，不能由压缩列号或全批头覆盖。

所有原观测属性、subset_index 及回执须共同持久交付；读取和重开不重新解码。原有 WOBS1 数据、构造调用与公开入口保持兼容；已存观测记录没有 subset_index 时读为 None。内部事件及批次元数据组织自由，无需新增子集查询接口或迁移系统。

build_daily 继续以确切批次集合冻结输入；按原 station、measurement、day 汇总全部实际观测，子集不是额外分组键，也不去重同形子集。原质量规则、排除计数和 Fraction 公式不变。不要求改写已能正确消费新观测的日算法。

## 字节包头

整数大端，位流高位优先。依次为：

1. 四字节 WOBS，单字节版本2。
2. 单字节模板ID长度及该长度ASCII模板ID，长度1～63。
3. 四字节无符号模板修订号。
4. 单字节子集数 N，1～16。
5. 按子集顺序重复 N 项：单字节站点ID长度、ASCII站点ID、10字节日期。站点长度1～63，日期须为真实规范 YYYY-MM-DD。允许站点日期重复。
6. 四字节无符号有效载荷位数，随后为向上取整到字节的载荷。

必须恰好消费全部有效位，末字节未用低位为0，不允许尾字节。空模板亦可接入，产生零观测回执。模板和字段仍遵循 FORMAT.md 的全部有限约束与精确含义。

## 标量列与复制活动集合

一个非空活动子集集合 A 的标量列，已知原标量宽度 w：先读 w 位无符号 base，再读6位无符号 k；k=0 时该列每个活动值都等于 base，不读差值；否则按 A 的子集下标升序各读 k 位无符号差值 delta，原值为 base+delta。必须 0≤k≤w，且和小于 2**w。不另设压缩缺测标记，也不要求最短编码或 base 为最小值；重建的字段/质量全1按 V1 解释，延迟次数全1仍非法。空活动集合完全不编码，包括 base 和 k。

根调用包含全部子集，次数均为1。一次复制组调用的 A 包含实际到达同一模板路径、同一父复制路径的子集；固定次数来自模板，延迟次数先以 count_width 为 w 形成次数列。对 i 从0至 A 中最大次数减1，只有次数大于 i 的子集进入本轮，按模板节点顺序处理字段及子组。字段以原 width 为 w 编一列；子组递归应用同一规则。同组各轮全部结束后才处理该调用的尾质量块，不是每轮一个尾块。子集次数0不进入迭代体，但仍参加这次调用的尾质量块。没有到达调用的子集不参加它的次数、成员或质量列。根列表也适用尾质量块规则。

## 变长质量列

设调用 A 有尾 quality，宽度为 q。先以 w=32 编各子集位图长度 L；每项 L 必须等于该子集本次调用的直接字段发生数。实际缺测占位置，嵌套子组成员不占位置，同复制组各轮直接成员依子集内 V1 顺序共同编号。

先对成员位置 j 从0至 max(L)-1，形成 w=1 的选择列，活动集合仅为 A 中 L>j 者；保存各子集选择位。全部选择列结束后，再按同样 j 顺序形成 w=q 的质量字列，活动集合仅为该位置被选中的子集。无人选择时不编码质量字列。未选中为 UNSELECTED，质量字全1为 MISSING，其余为 VALUE（包括0）。零长度调用仍有长度列，无选择和质量字列。无 quality 的组不编码这些列，直接成员均为 UNSELECTED。

## 错误及发布边界

沿用 V1 类型及异常约定。非法包头、列 k>w、加法溢出、保留次数、截断、非零填充或多余位/字节抛 DecodeError；包头绑定不符、伪造/未登记绑定、质量长度或发生身份不合法抛 ObservationError。一个输入同时有多个错误时不规定优先级。来源异常原样传播；SQLite 写入错误原样传播。任何子集非法或发布失败，整包不新增批次、观测或回执，既有资料及日成果不变；不能逐子集提交后拼回执。

## 手工列和调用样例

columns_cases.py 只按字面列拼位，列元组是（标签、w、活动子集、base、k、差值元组），未提供模板遍历、活动集合解码或持久身份适配。trace 的 start/end 是载荷内0基半开位坐标，active 不写入线上字节，而是独立手工推导的核账说明。两例分别使用 COL 修订1和2。

ragged：两个同站同日子集外层次数为0和2，后者两次内层次数为1和2。有效载荷227位；恰有3条，均属子集1，原始100、300、1023；值1、3、缺测，质量 VALUE(0)、VALUE(0)、UNSELECTED。日账输入3、接纳2、缺测1、和4、均值2。子集0的零次外层仍占外层质量长度列，但不占内层字段列。

scopes：三个子集外层次数为1、2、0。有效载荷382位；子集0和1同站同日，同形直接字段独立保留。共9条，父组引用域排除内组，内组含质量0的观测缺测和质量缺测。A站日账输入8、接纳4，四种排除各1，和95、均值95/4；B站次日输入1、接纳1、和9、均值9。所有路径、原值与质量的逐项期望均是 CASES 中的字面值。

调用示意（完成扩展后）：

```python
from weather.archive.imports import Archive
from columns_cases import CASES, packet
case = CASES[0]
with Archive(":memory:") as archive:
    binding = archive.register_template("COL", 1, case["descriptors"])
    receipt = archive.ingest_columns("morning", binding, [packet(case)])
    assert receipt.observation_count == 3
    assert [x.subset_index for x in archive.read_observations("morning")] == [1, 1, 1]
    assert str(archive.build_daily("day", ["morning"]).groups[0].mean) == "2"
```

## 核账与演示入口

当前基线的 .pointwise-runtime.json、verify.py 和 demo.py 仍执行 V1 全部回归及原演示。完成后更新现有运行清单的核验与演示入口：核验先保留全部原测试，再覆盖新列包的正常、异常及持久边界；演示真实接入不等长复制、区分同形子集、读取日账并重开核对。运行清单仍遵循已有 version/kind/setup/verify/demo 约定。

可执行非答案材料：

- python3.11 -B columns_cases.py fixtures：仅检查字面列的编码范围、位边界与包长，打印完整十六进制和手工活动集；基线即可执行。
- python3.11 -B columns_cases.py verify：完成扩展后核对两例的全部发生、数值、质量、站点日期、全批序号、单块/逐字节重放、日账和真实数据库重开。
- python3.11 -B columns_cases.py demo：执行同一真实业务核账并展示字面列及结果。

以上样例不替代完整边界核验。交付须覆盖列宽与溢出、非法次数、质量长度不符、截断和尾部损坏、源异常、实际SQLite发布失败、同号冲突，以及新旧入口混合日加工和重开保真；保留失败退出码。提交简短交接说明，解释活动位置如何变成实际发生、质量引用何时完整、子集与全批身份如何保留，并附独立小例核账。不规定内部文件拆分或目标实现。
