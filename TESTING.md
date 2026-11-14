# 考核账本测试说明

本环境的 `../../test-artifacts/` 目录不可写，无法沿用 BUSINESS.md 记录的外部测试目录，
因此考核账本的自动化测试暂存于本仓库 `tests/`，仅覆盖新增账本模块，不改动原有模块测试安排。

## 运行方式

使用仓库 `backend/requirements.txt` 的依赖（验证环境另需 `pytest`、`httpx`），在仓库根目录执行：

```bash
python3 -m pytest tests/ -q
```

测试使用 SQLite 内存库与 FastAPI 依赖覆盖，不依赖 MySQL/Redis；验证结果：**16 passed**。

## 覆盖内容

- `test_ledger.py`（13 项）：开户冻结批准口径与 checksum、重复开户拒绝、同日实测冲突整批拒绝且不落任何记录、
  待入账→已入账状态流转、同值重复入账不累计、幂等键重试返回同批次、确认/冲正中断重试不重复记账、
  封账后迟到入账拒绝、封账竞争下待入账批次整批拒绝并清除待入账行、冲正只进当前未封月份且原始月份与快照不变、
  冲正登记日与原始日账同日不冲突、月合计可追到日账 ID 与归因版本、丢弃批次释放日期。
- `test_ledger_api.py`（1 项）：路由级权限（admin/analyst/viewer）与完整 HTTP 流程。
- `test_ledger_concurrency.py`（2 项）：封账生效点与确认入账交错的一致性、并发幂等重放只产生一批一日账。

## 未覆盖项（如实声明）

- 未在真实 MySQL 行锁与 Redis 下跑容器级并发压测（本机无 Docker/MySQL/Redis）；
  并发正确性以 SQLite 写串行化模拟，并依赖服务层 `with_for_update` 账户行锁与数据库唯一约束兜底。
- 未做浏览器端到端截图；前端仅做 ES module 语法检查与模块 import 冒烟。
