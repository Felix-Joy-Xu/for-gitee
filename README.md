# for-gitee

GitHub Actions 定时爬取 Gitee 公开仓库行为数据（issues / 评论 / PR），数据与断点状态直接提交进本仓库。

## 运行方式

- **手动**：Actions 页 → Gitee Crawler → Run workflow
- **自动**：每日 UTC 21:00（北京时间 05:00）定时增量续爬

## 前置配置（一次性）

1. 在 gitee.com → 设置 → 私人令牌 创建 token（可多个，读取公开仓库即可）
2. 本仓库 Settings → Secrets and variables → Actions 新增：
   - `GITEE_TOKEN`：第一个 token
   - `GITEE_TOKENS`：多个 token 用英文逗号拼接（自动轮换，避开限流）
3. Actions 页手动 Run workflow 启动首次全量爬取

## 数据说明

- 原始数据：`02-原始数据/各平台原始数据/gitee_raw/{owner}/{repo}/{table}.jsonl`
- 表：`repos` / `issues` / `issue_comments` / `pull_requests` / `pr_comments` / `pr_reviews` / `pr_timeline`
- Gitee 公开 API 无 reviews/events/operate_logs 端点（实测 404），`pr_reviews`、`pr_timeline` 自动整表跳过（见断点文件 reason）
- Gitee issue 编号为字符串（如 `I1CATK`），入库时按 TEXT 处理
- 断点续传：`modelscope_output/state_gitee_{table}.json` 提交进 git，多次调度自动接力
- 进度报告：`PROGRESS.md`

## 本地建库

```bash
pip install requests
python build_gitee_database.py   # 生成 02-原始数据/01-GitHub数据/gitee_full.db
```

## 注意

- 定时任务若仓库 60 天无任何活动会被 GitHub 自动停用，届时手动 Run workflow 即可重新激活
- Gitee 匿名限流极严，未配置 secrets 时工作流无法正常爬取
