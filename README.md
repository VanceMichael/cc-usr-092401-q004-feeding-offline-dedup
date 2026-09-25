# 离线投喂重复计量修复

这是水产养殖管理系统，后端维护塘口、养殖批次、投苗、投喂、水质、用药、成本和出塘销售资料，前端提供日常录入与周期分析页面。数据默认保存在 SQLite 文件中。

## 测试命令

在仓库根目录执行：

```bash
python3 -m unittest discover -s tests -v
```

## 编译与构建命令

先安装前端依赖，再检查后端并构建前端：

```bash
python3 -m compileall -q backend/app
npm --prefix frontend install --legacy-peer-deps
npm --prefix frontend run build
```

本地启动可使用 `docker compose up --build`。开发环境不得提交真实账号、连接凭据或生产数据。

## 离线投喂同步语义

夜班设备断网恢复后会多次补传，系统按以下规则处理（见 `backend/app/services/feeding_service.py`）：

- **幂等键**：`(device_id, device_seq)` 全局唯一，记入 `feeding_sync_keys` 台账，落盘持久化，服务重启后重复同步仍返回原记录（响应 `result=duplicate`），不重复累计。
- **内容相同 vs 冲突**：对业务字段计算 `content_hash`。同键且同哈希 → 返回原记录；同键不同哈希 → 不产生投喂，登记冲突工单（`result=conflict`），在“冲突处理”中可“采纳来报”（生成更正版本）或“维持原记录”。
- **业务日期**：以 `feeding_date` 为准，跨午夜补传不会错记到上传当天。
- **更正/撤销**：`sync_mode=revision|revocation`（或页面编辑/删除），都追加新版本行（`version` 递增、`logical_id` 标识同一条逻辑记录），按 `effective_at` 指定的时点影响汇总；历史版本保留可审计。
- **汇总时点选择**：分析/日报取每个逻辑链在该时点“最新的已批准且已到生效时点”的版本，撤销行计 0，待审版本不计。`GET /api/analysis/cycle/{id}/?as_of=...` 支持历史时点重算。
- **迟报待审**：批次状态为 `closed/harvested` 后到达的记录标记 `is_late`、`review_status=pending`，批准后才计入（批准时点生效，不回溯），驳回则恢复上一版本。
- **游标分页**：`GET /api/feeding-records/?limit=&cursor=` 按到达序 `(created_at,id)` 做 keyset 分页，返回不透明 `next_cursor`，并发补传期间翻页不重不漏；写事务经进程内锁串行化，数据库唯一约束兜底。
- **日报签署**：`POST /api/feeding-daily-reports/sign/` 固化签署时点快照；`GET .../{id}/replay/` 只依据快照重放，签署之后的更正/撤销不改变已签报告，并标注 `changed_since_sign`。

