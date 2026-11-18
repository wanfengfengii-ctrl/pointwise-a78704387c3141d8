# 海岸气象观测归档

Python 3.11 标准库离线工程。研究站接入员登记供应商模板并导入 WOBS1 录制包，质控员逐条复核观测和质量，日加工员从确切批次集合生成精确日统计。不连接气象服务，不解释通用 BUFR。

## 运行

从项目根目录执行：

```sh
python3.11 -B verify.py
python3.11 -B demo.py
python3.11 -B -m unittest discover -s tests -v
```

无需安装依赖。verify 执行全部五个测试文件；也接受真实 unittest 测试名，例如 `python3.11 -B verify.py test_ingest.TestIngest.test_nested_occurrences`。完整命令参数见 RUN_MANIFEST.json 和 .pointwise-runtime.json。测试与 demo 使用系统临时目录中的独立 SQLite 数据库，退出后清理；模块采用 Python 命名空间包。

## 一天的资料处理

1. 供应商交付有序描述符树。接入员登记模板修订；完整校验后保存不可变定义。
2. 选择确切绑定。包头确定模板、站点、日期与有效位数，不能自动改用最新模板。
3. 提交字节分块。完整消费输入源并计算完整输入身份，源异常不会成为成功回执。
4. 展开固定或延迟复制。零次不生成观测，两层复制保留完整父级发生路径。
5. 转换原始位值。全1缺测仍保留身份，其余值按参考值、缩放和单位公式转为 Fraction。
6. 在每次组调用自己的直接成员中解释质量位图。缺测成员仍占位置，子组不借用父组引用域。
7. 校验后共同发布批次、观测和回执。质控员按发生序号读取，失败没有部分批次。
8. 日加工员选择确切批次集合和质量规则，冻结各输入摘要，不自动纳入同日其他批次。
9. 计算按站点、测量量、日期排序的计数、排除原因、精确和与均值，保存固定成果供核账。

## Python API

```python
from weather.archive.imports import Archive

with Archive(":memory:") as archive:
    binding = archive.register_template("VENDOR", 1, [
        {"kind": "field", "measurement": "wind_speed",
         "width": 10, "reference": 0, "scale": 2, "unit": "m/s"}
    ])
    # archive.ingest("morning", binding, byte_chunks)
    # observations = archive.read_observations("morning")
    # result = archive.build_daily("daily", ["morning"], {"allow_unselected": True})
```

完整可运行的导入例子见 demo.py 和 examples/day.json。Archive 接受 SQLite 文件路径或默认的 `:memory:`，支持上下文管理器。另提供：

- `archive.catalog.get_binding(template_id, revision)` 读取确切模板修订。
- `archive.read_daily(result_id)` 读取持久成果，不重新加工。
- `TemplateCatalog(connection)`、`ObservationQueries(connection)`、`DailyResults(connection, queries)` 可用于已有 SQLite 连接。

`register_template(template_id, revision, descriptors)` 返回冻结的 TemplateBinding。同修订同内容幂等；不同内容抛 ConflictError，结构、单位或类型错误抛 TemplateError。外部列表之后的修改不影响绑定。

`ingest(batch_id, template_binding, byte_chunks)` 返回 ImportReceipt(batch_id, input_digest, observation_count)。输入是 bytes、bytearray 或 memoryview 块的可迭代对象，允许空块和任意分块。摘要覆盖确切绑定和完整包字节。同批次同输入重放返回原回执，不再解码；不同输入或绑定抛 ConflictError。重放也先完整消费源。

`ingest_columns(batch_id, template_binding, byte_chunks)` 接入 WOBS2 多子集列包（仅版本2），参数类型、绑定核验、完整源消费、摘要公式与 ImportReceipt 沿用 `ingest`，两入口共用 batch_id 命名空间。它把 1～16 个有序子集的差分列重建为逐子集 V1 载荷并复用同一解码、质量与事务链路；任一子集非法则整包不发布。同号同绑定同字节重放返回原回执，异输入抛 ConflictError。列字节规则见 COLUMNS.md。

`read_observations(batch_id)` 返回不可变 Observation 元组，每条保存 occurrence（模板路径、复制路径、组身份、序号）、station、day、measurement、unit、raw、value、missing 和 quality。value 为 Fraction 或 None；质量是 UNSELECTED、MISSING 或 VALUE(code)，0 为真实质量值。WOBS2 观测另有 subset_index（0 基包内子集下标，全批 sequence 连续），WOBS1 与历史记录为 None。不存在的批次抛 KeyError。

`build_daily(result_id, batch_ids, quality_rule=None)` 返回 DailyResult，含固定 input_binding 和 DailyGroup 元组。批次按排序去重集合解释。规则只允许 accepted_codes（非负整数列表）、allow_unselected、allow_quality_missing（严格布尔值）；默认代码为 [0]，两个允许标志为 false。同语义规则规范化；非法规则抛 ValueError。缺批次抛 KeyError；同成果同绑定返回原成果，异绑定抛 ConflictError。

先排除观测缺测，再判定质量。每组 input_count 包含全部实际观测；accepted_count 是接纳数，另保存 observation_missing、unselected、quality_missing、quality_rejected 四种互斥排除计数。exact_sum 和 mean 全程为 Fraction；全部排除时和为0、均值为 None；零次组没有统计行。

## 持久性与错误

模板登记只追加新修订。批次、观测与回执共同提交；日输入绑定与全部统计行共同提交。SQLite 写入错误原样传播并整体回滚。初始化是幂等的，只创建缺失初始结构并检查版本，不清空或重导样例；无旧库迁移。用户通过公开入口新增资料在重复初始化和重开后保留。直接手工编辑数据库不是支持的写入接口，初始化也不会用样例覆盖数据。

非法包头、截断、非法复制次数、尾字节或填充抛 DecodeError；未登记/伪造绑定、包头绑定不符、组内质量引用或身份完整性错误抛 ObservationError。源迭代异常保留原异常对象。任何导入失败均不发布当前批次，不改变旧资料。缺测、质量未选中、质量缺测、质量0互相独立。

## 模块与回归

templates 的 catalog/descriptors/validation/bindings 拥有登记和不可变修订；decoding 的 bits/header/fields/groups/events 拥有连续位游标、组调用和原始事件；observations 的 convert/quality/assemble/validate/models 拥有规范值、组内关联和完整性；archive 的 schema/imports/publication/queries/codecs 拥有事务和固定读取；daily 的 plans/rules/filtering/aggregate/results 拥有固定输入、筛选和精确成果。

tests/test_templates.py 覆盖修订绑定及非法定义；test_ingest.py 覆盖手工样例、复制、分块、单位、截断和源异常；test_quality.py 覆盖三态、缺测占位、父子与相邻组及越界；test_archive.py 用真实 SQLite 触发器验证回滚、重放、重开和初始化；test_daily.py 覆盖固定集合、规则、精确均值、空组、排序和日成果回滚；test_columns.py 覆盖 WOBS2 差分列、不等长复制、同形子集、变长质量列、头尾损坏、重放冲突、SQLite 回滚、重开保真与新旧入口混合日加工。columns_audit.py 是不依赖 columns_cases 的独立小例核账。

样例的独立核账为风速 100/100=1、300/100=3、1023=缺测。前两项质量为0，因此输入3、接纳2、和4、均值2。demo 先比较实际观测、日成果及重开结果，再打印 POINTWISE_SCENARIO JSON。更多字节规则见 FORMAT.md。
