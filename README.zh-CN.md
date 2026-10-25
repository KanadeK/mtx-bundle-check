# mtx-bundle-check 中文说明

读取 Matrix Market 稀疏计数矩阵及 feature/barcode 标签文件，检查条目数量、坐标范围、
非相邻重复坐标、标签数量与 ID 唯一性。使用临时 SQLite 磁盘索引，不展开矩阵。

Python 3.11+，无第三方运行依赖。克隆后运行 `python -m pip install .`：

```sh
mtx-bundle-check examples/clean --json
mtx-bundle-check examples/duplicate --json
```

第一条退出 0；第二条退出 1，指出第 6 行坐标与第 4 行重复。示例均为人工测试数据。
支持 matrix.mtx、features.tsv（3 列）或 genes.tsv（2 列）、barcodes.tsv（1 列），
各文件可以独立使用 gzip。`--prefix sample_` 指定统一文件名前缀。

只检查原始计数：存储值必须是正整数，最大为 2^63−1；小数、负数、显式零和重复坐标
都会报告。不是普通浮点矩阵验证器，不接受标准化表达值。重复 feature name 可以存在，
重复 feature ID 不允许。空维度与对应空标签可通过结构检查，但不代表有有效测量数据。

退出码 0=完整检查通过，1=发现问题，2=输入无效、读取不完整、I/O 失败或超出限额。
gzip 截断和 CRC 错误不会被当作通过。报告省略 ID 和计数值，但仍含文件名和矩阵维度。
默认最多 5000 万条目、每轴 1000 万标签、4 GiB 解压字节、每行 1 MiB。
`--tmpdir` 可指定已有的临时目录父路径；索引结束时删除，异常断电可能遗留文件。

检查不能识别长度相同但被打乱或替换的标签文件，也不证明样本身份、实验归属或生物学质量。
请使用静止快照并保留来源记录。完整边界和参数见 [英文说明](README.md)。

测试：`python -m unittest discover -s tests -v`。
卸载：`python -m pip uninstall mtx-bundle-check`；不产生持久缓存，不修改输入。
