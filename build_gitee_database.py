#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Gitee 数据入库
==============
将 crawl_gitee.py 生成的 JSONL 原始数据整合为 SQLite 数据库，
表结构对齐 `02-原始数据/01-GitHub数据/github_full.db` 的 9 张表。

输入：02-原始数据/各平台原始数据/gitee_raw/{owner}/{repo}/*.jsonl
输出：02-原始数据/01-GitHub数据/gitee_full.db
"""
import argparse
import json
import os
import re
import sqlite3
import sys
from pathlib import Path

# ============================================================================
# 配置
# ============================================================================
BASE_DIR = Path(__file__).resolve().parent
RAW_DIR = BASE_DIR / "02-原始数据" / "各平台原始数据" / "gitee_raw"
DB_PATH = BASE_DIR / "02-原始数据" / "01-GitHub数据" / "gitee_full.db"

BATCH_SIZE = 5000

# 对齐 github_full.db 的 schema
SCHEMA = """
CREATE TABLE IF NOT EXISTS issues (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    repo TEXT NOT NULL,
    number TEXT NOT NULL,
    title TEXT,
    body TEXT,
    state TEXT,
    created_at TEXT,
    author TEXT,
    phase TEXT,
    keyword_group TEXT,
    UNIQUE(repo, number)
);
CREATE INDEX IF NOT EXISTS idx_issues_repo ON issues(repo);
CREATE INDEX IF NOT EXISTS idx_issues_created ON issues(created_at);

CREATE TABLE IF NOT EXISTS issue_comments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    repo TEXT NOT NULL,
    issue_number TEXT NOT NULL,
    body TEXT,
    author TEXT,
    created_at TEXT,
    phase TEXT
);
CREATE INDEX IF NOT EXISTS idx_ic_repo ON issue_comments(repo);
CREATE INDEX IF NOT EXISTS idx_ic_parent ON issue_comments(repo, issue_number);
CREATE INDEX IF NOT EXISTS idx_ic_time ON issue_comments(created_at);

CREATE TABLE IF NOT EXISTS pull_requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    repo TEXT NOT NULL,
    number INTEGER NOT NULL,
    title TEXT,
    body TEXT,
    state TEXT,
    created_at TEXT,
    merged_at TEXT,
    additions INTEGER DEFAULT 0,
    deletions INTEGER DEFAULT 0,
    author TEXT,
    phase TEXT,
    keyword_group TEXT,
    UNIQUE(repo, number)
);
CREATE INDEX IF NOT EXISTS idx_pr_repo ON pull_requests(repo);
CREATE INDEX IF NOT EXISTS idx_pr_created ON pull_requests(created_at);
CREATE INDEX IF NOT EXISTS idx_pr_merged ON pull_requests(merged_at);

CREATE TABLE IF NOT EXISTS pr_comments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    repo TEXT NOT NULL,
    pr_number INTEGER NOT NULL,
    body TEXT,
    author TEXT,
    created_at TEXT,
    phase TEXT
);
CREATE INDEX IF NOT EXISTS idx_prc_repo ON pr_comments(repo);
CREATE INDEX IF NOT EXISTS idx_prc_pr ON pr_comments(repo, pr_number);
CREATE INDEX IF NOT EXISTS idx_prc_time ON pr_comments(created_at);

CREATE TABLE IF NOT EXISTS pr_reviews (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    repo TEXT NOT NULL,
    pr_number INTEGER NOT NULL,
    type TEXT,
    body TEXT,
    state TEXT,
    created_at TEXT,
    author TEXT,
    phase TEXT
);
CREATE INDEX IF NOT EXISTS idx_prr_repo ON pr_reviews(repo);
CREATE INDEX IF NOT EXISTS idx_prr_pr ON pr_reviews(repo, pr_number);

CREATE TABLE IF NOT EXISTS pr_timeline (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    repo TEXT NOT NULL,
    pr_number INTEGER NOT NULL,
    event_type TEXT
);
CREATE INDEX IF NOT EXISTS idx_ptl_repo ON pr_timeline(repo);
CREATE INDEX IF NOT EXISTS idx_ptl_pr ON pr_timeline(repo, pr_number);

CREATE TABLE IF NOT EXISTS discussions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    repo TEXT NOT NULL,
    number INTEGER NOT NULL,
    title TEXT,
    body TEXT,
    created_at TEXT,
    author TEXT,
    phase TEXT,
    keyword_group TEXT,
    UNIQUE(repo, number)
);
CREATE INDEX IF NOT EXISTS idx_disc_repo ON discussions(repo);

CREATE TABLE IF NOT EXISTS disc_comments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    repo TEXT NOT NULL,
    disc_number INTEGER NOT NULL,
    body TEXT,
    author TEXT,
    created_at TEXT,
    type TEXT,
    phase TEXT
);
CREATE INDEX IF NOT EXISTS idx_dcc_repo ON disc_comments(repo);

CREATE TABLE IF NOT EXISTS repos (
    repo_name TEXT PRIMARY KEY,
    has_discussions INTEGER DEFAULT 0,
    issue_count INTEGER DEFAULT 0,
    pr_count INTEGER DEFAULT 0,
    disc_count INTEGER DEFAULT 0
);
"""


# ============================================================================
# 工具函数
# ============================================================================
def init_db(db_path: Path):
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.executescript(SCHEMA)
    conn.commit()
    return conn


def load_jsonl(file_path: Path):
    if not file_path.exists():
        return []
    records = []
    with open(file_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return records


def get_author(item: dict) -> str:
    user = item.get("user") or item.get("actor") or item.get("author") or {}
    if isinstance(user, dict):
        return user.get("login") or ""
    return str(user) if user else ""


def parse_number_from_url(url: str, key: str) -> int | None:
    if not url:
        return None
    m = re.search(rf"/{key}s?/(\d+)", url)
    if m:
        return int(m.group(1))
    return None


def to_int(v):
    try:
        return int(v)
    except (ValueError, TypeError):
        return 0


# ============================================================================
# 入库函数
# ============================================================================
def insert_issues(conn, repo: str, records: list):
    cur = conn.cursor()
    for item in records:
        cur.execute(
            """INSERT OR REPLACE INTO issues
               (repo, number, title, body, state, created_at, author)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                repo,
                str(item.get("number") or ""),
                item.get("title", ""),
                item.get("body", ""),
                item.get("state", ""),
                item.get("created_at", ""),
                get_author(item),
            )
        )
    conn.commit()


def insert_issue_comments(conn, repo: str, records: list):
    cur = conn.cursor()
    for item in records:
        issue_num = item.get("issue_number")
        if issue_num is None:
            issue_num = parse_number_from_url(item.get("issue_url"), "issue")
        cur.execute(
            """INSERT INTO issue_comments
               (repo, issue_number, body, author, created_at)
               VALUES (?, ?, ?, ?, ?)""",
            (
                repo,
                str(issue_num) if issue_num is not None else "",
                item.get("body", ""),
                get_author(item),
                item.get("created_at", ""),
            )
        )
    conn.commit()


def insert_pull_requests(conn, repo: str, records: list):
    cur = conn.cursor()
    for item in records:
        cur.execute(
            """INSERT OR REPLACE INTO pull_requests
               (repo, number, title, body, state, created_at, merged_at, author)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                repo,
                to_int(item.get("number")),
                item.get("title", ""),
                item.get("body", ""),
                item.get("state", ""),
                item.get("created_at", ""),
                item.get("merged_at", ""),
                get_author(item),
            )
        )
    conn.commit()


def insert_pr_comments(conn, repo: str, records: list):
    cur = conn.cursor()
    for item in records:
        pr_num = item.get("pr_number")
        if pr_num is None:
            pr_num = parse_number_from_url(item.get("pull_request_url"), "pull")
        cur.execute(
            """INSERT INTO pr_comments
               (repo, pr_number, body, author, created_at)
               VALUES (?, ?, ?, ?, ?)""",
            (
                repo,
                to_int(pr_num),
                item.get("body", ""),
                get_author(item),
                item.get("created_at", ""),
            )
        )
    conn.commit()


def insert_pr_reviews(conn, repo: str, records: list):
    cur = conn.cursor()
    for item in records:
        pr_num = item.get("pr_number")
        created = item.get("submitted_at") or item.get("created_at") or item.get("createdAt", "")
        cur.execute(
            """INSERT INTO pr_reviews
               (repo, pr_number, type, body, state, created_at, author)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                repo,
                to_int(pr_num),
                item.get("source_endpoint") or item.get("type", ""),
                str(item.get("body") or item.get("content") or ""),
                item.get("state", ""),
                created,
                get_author(item),
            )
        )
    conn.commit()


def insert_pr_timeline(conn, repo: str, records: list):
    cur = conn.cursor()
    for item in records:
        pr_num = item.get("pr_number")
        event_type = item.get("event") or item.get("action") or item.get("source_endpoint") or ""
        cur.execute(
            """INSERT INTO pr_timeline
               (repo, pr_number, event_type)
               VALUES (?, ?, ?)""",
            (
                repo,
                to_int(pr_num),
                event_type,
            )
        )
    conn.commit()


def insert_repos(conn, repo: str, records: list):
    if not records:
        return
    item = records[0].get("data") or records[0]
    if not isinstance(item, dict):
        return
    cur = conn.cursor()
    cur.execute(
        """INSERT OR REPLACE INTO repos
           (repo_name, has_discussions, issue_count, pr_count, disc_count)
           VALUES (?, ?, ?, ?, ?)""",
        (
            repo,
            0,  # Gitee 无 Discussions
            to_int(item.get("open_issues_count")),
            0,  # PR 数在入库后统计
            0,
        )
    )
    conn.commit()


# ============================================================================
# 主流程
# ============================================================================
def main():
    parser = argparse.ArgumentParser(description="Gitee 数据入库")
    parser.add_argument("--raw-dir", type=Path, default=RAW_DIR)
    parser.add_argument("--db", type=Path, default=DB_PATH)
    args = parser.parse_args()

    if not args.raw_dir.exists():
        print(f"[error] 未找到 {args.raw_dir}，请先运行 crawl_gitee.py")
        sys.exit(2)

    conn = init_db(args.db)

    total_repos = 0
    for owner_dir in sorted(args.raw_dir.iterdir()):
        if not owner_dir.is_dir():
            continue
        for repo_dir in sorted(owner_dir.iterdir()):
            if not repo_dir.is_dir():
                continue
            repo = f"{owner_dir.name}/{repo_dir.name}"
            total_repos += 1
            print(f"[import] {repo}")

            # 支持分片文件：{table}.jsonl / {table}_part{N}.jsonl
            def load_table(table: str) -> list:
                records = []
                for f in sorted(repo_dir.glob(f"{table}*.jsonl")):
                    records.extend(load_jsonl(f))
                return records

            insert_repos(conn, repo, load_table("repos"))
            insert_issues(conn, repo, load_table("issues"))
            insert_issue_comments(conn, repo, load_table("issue_comments"))
            insert_pull_requests(conn, repo, load_table("pull_requests"))
            insert_pr_comments(conn, repo, load_table("pr_comments"))
            insert_pr_reviews(conn, repo, load_table("pr_reviews"))
            insert_pr_timeline(conn, repo, load_table("pr_timeline"))

    # 更新 repos 表的 pr_count
    conn.execute("""
        UPDATE repos SET pr_count = (
            SELECT COUNT(DISTINCT number) FROM pull_requests WHERE pull_requests.repo = repos.repo_name
        )
    """)
    conn.commit()

    # 统计
    cur = conn.cursor()
    cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = [t[0] for t in cur.fetchall()]
    print("\n[summary]")
    for tbl in tables:
        try:
            cur.execute(f"SELECT COUNT(*) FROM {tbl}")
            cnt = cur.fetchone()[0]
            print(f"  {tbl}: {cnt}")
        except Exception:
            pass

    conn.close()
    print(f"[done] 数据库保存于 {args.db}")


if __name__ == "__main__":
    main()
