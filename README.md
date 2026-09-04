# IoT_system — 智慧会议物联运维平台

对标 C-AIoT 智慧物联协作平台，需求基线来自 [`清单.xlsx`](./清单.xlsx)（多媒体会议系统报价清单）。

## 目录

| 文件 | 说明 |
|---|---|
| [`docs/分层架构与选型.md`](docs/分层架构与选型.md) | 9 层拆分、逐层开源候选、许可证红线、落地顺序 |
| [`docs/开源选型调研.md`](docs/开源选型调研.md) | 清单解读、设备协议、选型结论 |
| [`docs/需求规格-自清单还原.md`](docs/需求规格-自清单还原.md) | 472 条功能条目，自清单技术参数列抽取，未改写 |
| [`docs/设备BOM.csv`](docs/设备BOM.csv) | 116 行设备清单，按空间/子系统归类 |
| [`docs/模拟器与数据契约.md`](docs/模拟器与数据契约.md) | 设备模拟器用法、驱动可信度标注、HTTP 数据契约 |
| [`sim/`](sim/) | 设备模拟器、协议采集器、模拟数据生成 |
| [`ui/`](ui/) | 界面原型模板与构建脚本 |
| [`tools/extract_bom.py`](tools/extract_bom.py) | 需求与 BOM 的生成脚本 |

## 重新生成文档

```bash
pip install openpyxl
python3 tools/extract_bom.py 清单.xlsx docs/
```
