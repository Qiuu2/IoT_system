# IoT_system — 智慧会议物联运维平台

对标 C-AIoT 智慧物联协作平台，需求基线来自 [`清单.xlsx`](./清单.xlsx)（多媒体会议系统报价清单）。

## 目录

| 文件 | 说明 |
|---|---|
| [`docs/开源选型调研.md`](docs/开源选型调研.md) | 选型结论、许可证风险、必须自研的部分 |
| [`docs/需求规格-自清单还原.md`](docs/需求规格-自清单还原.md) | 472 条功能条目，自清单技术参数列抽取，未改写 |
| [`docs/设备BOM.csv`](docs/设备BOM.csv) | 116 行设备清单，按空间/子系统归类 |
| [`tools/extract_bom.py`](tools/extract_bom.py) | 上述两份文档的生成脚本 |

## 重新生成文档

```bash
pip install openpyxl
python3 tools/extract_bom.py 清单.xlsx docs/
```
