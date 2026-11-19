# 文本数据索引

由 `scripts/flydigi_capture_to_text.py --all` 生成。原始 `.bin` 只在本地 `captures/`（已 gitignore），进仓库的是这里的文本形式。

| 文本文件 | 类型 | 帧数 | 时长 | 来源 bin |
|---|---|---|---|---|
| [accel_20261119-011453.txt](txt/accel_20261119-011453.txt) | 报文流 | 113,625 | 126.4s | `accel_20261119-011453.bin` |
| [accel_20261119-011738.txt](txt/accel_20261119-011738.txt) | 报文流 | 75,077 | 95.1s | `accel_20261119-011738.bin` |
| [factory_fp4_20261119-214700.txt](txt/factory_fp4_20261119-214700.txt) | 出厂配置转储（4 槽 × 840 字节） | — | — | `default_mapping_132.dat`（厂商文件） |
| [blob_audit_20261119-213331.json](txt/blob_audit_20261119-213331.json) | 四槽审计 JSON | — | — | `blob_audit_20261119-213331`（现场读 blob） |
| [blob_audit_20261119-215840.json](txt/blob_audit_20261119-215840.json) | 四槽审计 JSON（当前槽 3） | — | — | `blob_audit_20261119-215840`（现场读 blob + `--dump`） |
| [blob_slot0_20261119-003029.txt](txt/blob_slot0_20261119-003029.txt) | 配置 blob 十六进制转储 | — | — | `blob_slot0_20261119-003029.bin` |
| [blob_slot0_20261119-003343.txt](txt/blob_slot0_20261119-003343.txt) | 配置 blob 十六进制转储 | — | — | `blob_slot0_20261119-003343.bin` |
| [blob_slot2_20261119-003343.txt](txt/blob_slot2_20261119-003343.txt) | 配置 blob 十六进制转储 | — | — | `blob_slot2_20261119-003343.bin` |
| [blob_slot3_20261119-003343.txt](txt/blob_slot3_20261119-003343.txt) | 配置 blob 十六进制转储 | — | — | `blob_slot3_20261119-003343.bin` |
| [buttons_20261118-233633.txt](txt/buttons_20261118-233633.txt) | 报文流 | 111,550 | 313.7s | `buttons_20261118-233633.bin` |
| [control_20261119-222530.txt](txt/control_20261119-222530.txt) | 报文流（按键探针：SELECT/FN/Home/Turbo/M1/M2） | 33,174 | 99.3s | `control_20261119-222530.bin` |
| [control_20261119-222738.txt](txt/control_20261119-222738.txt) | 报文流（SELECT+A/B/X/Y 组合） | 24,568 | 41.1s | `control_20261119-222738.bin` |
| [inputs_20261119-000420.txt](txt/inputs_20261119-000420.txt) | 报文流 | 43,056 | 47.1s | `inputs_20261119-000420.bin` |
| [inputs_20261119-000434.txt](txt/inputs_20261119-000434.txt) | 报文流 | 430 | 2.4s | `inputs_20261119-000434.bin` |
| [inputs_20261119-000457.txt](txt/inputs_20261119-000457.txt) | 报文流 | 17,050 | 21.1s | `inputs_20261119-000457.bin` |
| [inputs_20261119-000752.txt](txt/inputs_20261119-000752.txt) | 报文流 | 124,914 | 172.0s | `inputs_20261119-000752.bin` |
| [inputs_20261119-002308.txt](txt/inputs_20261119-002308.txt) | 报文流 | 242,538 | 270.8s | `inputs_20261119-002308.bin` |
| [motion_20261119-003907.txt](txt/motion_20261119-003907.txt) | 报文流 | 108,336 | 108.3s | `motion_20261119-003907.bin` |
| [motion_phases.txt](txt/motion_phases.txt) | 报文流 | 19,980 | 22.0s | `motion_phases.bin` |
| [old_dialect.txt](txt/old_dialect.txt) | 老方言抓包（含阶段名） | 2,812 | 1.0s | `old_dialect.bin` |
| [raw_stream.txt](txt/raw_stream.txt) | 报文流 | 7,219 | 12.0s | `raw_stream.bin` |

每列的含意见 [docs/data.md](../docs/data.md)。
