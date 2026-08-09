#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成 PROGRESS.md：各仓库各表行数 + 断点状态。"""
import json
from datetime import datetime, timezone
from pathlib import Path

BASE = Path(__file__).resolve().parent
RAW = BASE / "02-原始数据" / "各平台原始数据" / "gitee_raw"
ST = BASE / "modelscope_output"

TABLES = ["repos", "issues", "issue_comments", "pull_requests", "pr_comments", "pr_reviews", "pr_timeline"]

lines = [
    "# Gitee 爬取进度",
    "",
    f"最后更新: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC",
    "",
    "| 仓库 | repos | issues | issue_comments | pull_requests | pr_comments | pr_reviews | pr_timeline |",
    "| --- | --- | --- | --- | --- | --- | --- | --- |",
]

total = {t: 0 for t in TABLES}
if RAW.is_dir():
    for repo_dir in sorted(RAW.iterdir()):
        if not repo_dir.is_dir():
            continue
        counts = []
        for table in TABLES:
            n = 0
            for f in repo_dir.glob(f"{table}*.jsonl"):
                with open(f, encoding="utf-8", errors="ignore") as fh:
                    n += sum(1 for _ in fh)
            total[table] += n
            counts.append(str(n))
        lines.append(f"| {repo_dir.name} | " + " | ".join(counts) + " |")

lines.append("")
lines.append("## 汇总")
for table in TABLES:
    lines.append(f"- {table}: {total[table]} 行")

lines.append("")
lines.append("## 断点状态")
for sf in sorted(ST.glob("state_gitee_*.json")):
    try:
        st = json.loads(sf.read_text(encoding="utf-8"))
        done = len(st.get("completed", []))
        if st.get("skipped"):
            lines.append(f"- {sf.name}: 整表跳过（{st.get('reason', '')}）")
        else:
            lines.append(f"- {sf.name}: 完成 {done} 个仓库")
    except Exception:
        lines.append(f"- {sf.name}: 无法解析")

(BASE / "PROGRESS.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
print("[done] PROGRESS.md 已生成")
