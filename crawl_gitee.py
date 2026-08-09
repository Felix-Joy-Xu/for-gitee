#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Gitee 仓库行为数据采集
======================
输入：modelscope_output/gitee_selected_repos.json（由 select_gitee_repos.py 生成）
输出：
  - 原始 JSONL：gitee_raw/{owner}/{repo}/{table}.jsonl
  - 断点文件：modelscope_output/state_gitee_*.json

采集表（对齐 github_full.db 的 9 张表）：
  - repos
  - issues
  - issue_comments
  - pull_requests
  - pr_comments
  - pr_reviews  （端点需实测：Gitee API v5 中可能为 /pulls/{number}/review_comments 或不存在）
  - pr_timeline （端点需实测：operate_logs 或 pull_request_events）
  - discussions （Gitee 无 Discussions，留空表）
  - disc_comments （同上）

注意：
  - Gitee API 个人 access_token 限速较严，建议低并发 + 退避。
  - 部分端点（reviews、timeline）的可得性需实测，脚本内置 fallback：
    若不可得，则 pr_reviews / pr_timeline 留空，后续分析可用 issue/PR 状态变更代理。
"""
import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

# ============================================================================
# 配置
# ============================================================================
try:
    from _secrets import GITEE_TOKEN, GITEE_TOKENS
except ImportError:
    GITEE_TOKEN = os.environ.get("GITEE_TOKEN", "").strip()
    GITEE_TOKENS = [t.strip() for t in os.environ.get("GITEE_TOKENS", "").split(",") if t.strip()]

BASE_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = BASE_DIR / "modelscope_output"
RAW_DIR = BASE_DIR / "02-原始数据" / "各平台原始数据" / "gitee_raw"
OUTPUT_DIR.mkdir(exist_ok=True)
RAW_DIR.mkdir(parents=True, exist_ok=True)

SELECTED_FILE = OUTPUT_DIR / "gitee_selected_repos.json"

GITEE_API = "https://gitee.com/api/v5"
HEADERS = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}

REQUEST_DELAY = 0.6
MAX_PER_PAGE = 100

TABLES = ["repos", "issues", "issue_comments", "pull_requests", "pr_comments", "pr_reviews", "pr_timeline"]


class TokenRotator:
    """Gitee token 轮换器，支持多 token 避免限流。"""
    def __init__(self):
        self.tokens = list(dict.fromkeys(GITEE_TOKENS or ([GITEE_TOKEN] if GITEE_TOKEN else [])))
        self.idx = 0
        self.failed = set()

    def current(self) -> str | None:
        available = [t for t in self.tokens if t not in self.failed]
        if not available:
            return None
        return available[self.idx % len(available)]

    def next(self):
        available = [t for t in self.tokens if t not in self.failed]
        if available:
            self.idx = (self.idx + 1) % len(available)

    def mark_failed(self, token: str):
        self.failed.add(token)


TOKEN_ROTATOR = TokenRotator()

# Gitee API 能力探测结果（实测 /pulls/{n}/reviews、/pulls/{n}/events、/operate_logs 均 404）
REVIEWS_AVAILABLE = True
TIMELINE_AVAILABLE = True


def probe_capabilities(session: requests.Session, repos: list):
    """探测不可靠端点；404 则置 False，对应表整表跳过（节省每 PR 2 次请求）。"""
    global REVIEWS_AVAILABLE, TIMELINE_AVAILABLE
    if not repos:
        return
    owner, repo = repos[0]
    probes = [
        ("REVIEWS", f"/repos/{owner}/{repo}/pulls/1/reviews"),
        ("TIMELINE", f"/repos/{owner}/{repo}/pulls/1/events"),
    ]
    for name, path in probes:
        data, status = gitee_get(session, path)
        if status == 404:
            if name == "REVIEWS":
                REVIEWS_AVAILABLE = False
            else:
                TIMELINE_AVAILABLE = False
            print(f"[probe] {name} 端点不可用（HTTP 404），对应表将整表跳过")
        else:
            print(f"[probe] {name} 端点可用（HTTP {status}）")


# ============================================================================
# 工具函数
# ============================================================================
def load_state(table: str) -> dict:
    state_file = OUTPUT_DIR / f"state_gitee_{table}.json"
    if state_file.exists():
        with open(state_file, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"completed": []}


def save_state(table: str, state: dict):
    state_file = OUTPUT_DIR / f"state_gitee_{table}.json"
    with open(state_file, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def gitee_get(session: requests.Session, path: str, params: dict = None) -> tuple:
    """请求 Gitee API，返回 (data, status)；支持 token 轮换。"""
    url = f"{GITEE_API}{path}"
    p = dict(params) if params else {}

    for attempt in range(10):
        token = TOKEN_ROTATOR.current()
        if not token:
            return None, 401
        p["access_token"] = token

        try:
            r = session.get(url, headers=HEADERS, params=p, timeout=30)
            if r.status_code == 200:
                try:
                    return r.json(), 200
                except Exception:
                    return r.text, 200
            if r.status_code == 404:
                return None, 404
            if r.status_code == 403:
                # 可能是限流或私有/无权限
                if "rate limit" in r.text.lower() or "limit" in r.text.lower() or "请求次数" in r.text:
                    TOKEN_ROTATOR.mark_failed(token)
                    TOKEN_ROTATOR.next()
                    time.sleep(1)
                    continue
                return None, 403
            if r.status_code == 429:
                TOKEN_ROTATOR.mark_failed(token)
                TOKEN_ROTATOR.next()
                time.sleep(1)
                continue
            return None, r.status_code
        except Exception as e:
            if attempt < 9:
                time.sleep(min(2 ** (attempt % 4), 10))
                continue
            return str(e), -1
    return None, -1


MAX_FILE_SIZE = 50 * 1024 * 1024  # 50MB 触发分片，确保单文件远低于 GitHub 100MB 上限


def get_active_path(repo_dir: Path, table: str) -> Path:
    """返回当前可写的 jsonl 路径；超过 MAX_FILE_SIZE 自动切换 {table}_part{N}.jsonl。"""
    base = repo_dir / f"{table}.jsonl"
    if not base.exists() or base.stat().st_size < MAX_FILE_SIZE:
        return base
    part = 1
    while True:
        cand = repo_dir / f"{table}_part{part}.jsonl"
        if not cand.exists():
            return cand
        if cand.stat().st_size >= MAX_FILE_SIZE:
            part += 1
            continue
        return cand


def write_jsonl(repo_dir: Path, table: str, records: list):
    if not records:
        return
    file_path = get_active_path(repo_dir, table)
    with open(file_path, "a", encoding="utf-8") as f:
        for rec in records:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def fetch_paginated(session: requests.Session, path: str, params: dict = None,
                    since_key: str = "since", since: str = None) -> list:
    """分页拉取列表接口，返回合并后的列表。"""
    all_items = []
    p = dict(params) if params else {}
    p["per_page"] = MAX_PER_PAGE
    page = 1
    empty_count = 0

    while True:
        p["page"] = page
        if since and since_key:
            p[since_key] = since

        data, status = gitee_get(session, path, p)
        if status != 200:
            print(f"  [paginate] {path} HTTP {status}, stop at page {page}")
            break

        if not isinstance(data, list):
            print(f"  [paginate] {path} returned non-list, stop")
            break

        if not data:
            empty_count += 1
            if empty_count >= 2:
                break
        else:
            empty_count = 0
            all_items.extend(data)

        if len(data) < MAX_PER_PAGE:
            break

        page += 1
        time.sleep(REQUEST_DELAY)

    return all_items


# ============================================================================
# 各表采集函数
# ============================================================================
def crawl_repos(session: requests.Session, repos: list, state: dict):
    completed = set(state.get("completed", []))
    for owner, repo in repos:
        full = f"{owner}/{repo}"
        if full in completed:
            continue
        repo_dir = RAW_DIR / owner / repo
        repo_dir.mkdir(parents=True, exist_ok=True)

        data, status = gitee_get(session, f"/repos/{owner}/{repo}")
        record = {"repo": full, "crawled_at": datetime.now(timezone.utc).isoformat(),
                  "status": status, "data": data}
        write_jsonl(repo_dir, "repos", [record])

        if status == 200:
            completed.add(full)
            state["completed"] = sorted(completed)
            save_state("repos", state)
        time.sleep(REQUEST_DELAY)


def crawl_issues(session: requests.Session, repos: list, state: dict, since: str):
    completed = set(state.get("completed", []))
    for owner, repo in repos:
        full = f"{owner}/{repo}"
        if full in completed:
            continue
        repo_dir = RAW_DIR / owner / repo
        repo_dir.mkdir(parents=True, exist_ok=True)

        items = fetch_paginated(session, f"/repos/{owner}/{repo}/issues",
                                {"state": "all"}, since_key="since", since=since)
        # 过滤 pull_request（Gitee issue 列表有时会混 PR）
        pure_issues = [it for it in items if "pull_request" not in it]
        for it in pure_issues:
            it["repo"] = full
        write_jsonl(repo_dir, "issues", pure_issues)

        completed.add(full)
        state["completed"] = sorted(completed)
        save_state("issues", state)
        print(f"  [issues] {full}: {len(pure_issues)}")


def crawl_issue_comments(session: requests.Session, repos: list, state: dict, since: str):
    completed = set(state.get("completed", []))
    for owner, repo in repos:
        full = f"{owner}/{repo}"
        if full in completed:
            continue
        repo_dir = RAW_DIR / owner / repo
        repo_dir.mkdir(parents=True, exist_ok=True)

        items = fetch_paginated(session, f"/repos/{owner}/{repo}/issues/comments",
                                {}, since_key="since", since=since)
        # 只保留 issue 评论（Gitee 该端点同时返回 PR 评论，PR 评论归 pr_comments 表）
        # Gitee issue 编号为字符串（如 I1CATK），无 issue_url 字段，需从 target.issue.number 提取
        pure = []
        for it in items:
            target = it.get("target") or {}
            if not target.get("issue"):
                continue
            it["repo"] = full
            it["issue_number"] = target["issue"].get("number", "")
            pure.append(it)
        write_jsonl(repo_dir, "issue_comments", pure)

        completed.add(full)
        state["completed"] = sorted(completed)
        save_state("issue_comments", state)
        print(f"  [issue_comments] {full}: {len(items)}")


def crawl_pull_requests(session: requests.Session, repos: list, state: dict, since: str):
    completed = set(state.get("completed", []))
    for owner, repo in repos:
        full = f"{owner}/{repo}"
        if full in completed:
            continue
        repo_dir = RAW_DIR / owner / repo
        repo_dir.mkdir(parents=True, exist_ok=True)

        items = fetch_paginated(session, f"/repos/{owner}/{repo}/pulls",
                                {"state": "all", "sort": "created", "direction": "asc"},
                                since_key="since", since=since)
        for it in items:
            it["repo"] = full
        write_jsonl(repo_dir, "pull_requests", items)

        completed.add(full)
        state["completed"] = sorted(completed)
        save_state("pull_requests", state)
        print(f"  [pull_requests] {full}: {len(items)}")


def crawl_pr_comments(session: requests.Session, repos: list, state: dict):
    """遍历每个 PR，抓取 PR 评论。"""
    completed = set(state.get("completed", []))
    for owner, repo in repos:
        full = f"{owner}/{repo}"
        if full in completed:
            continue
        repo_dir = RAW_DIR / owner / repo
        repo_dir.mkdir(parents=True, exist_ok=True)

        # 读取已抓取的 PR 列表
        pr_file = repo_dir / "pull_requests.jsonl"
        pr_numbers = []
        if pr_file.exists():
            with open(pr_file, "r", encoding="utf-8") as f:
                for line in f:
                    pr = json.loads(line)
                    pr_numbers.append(pr.get("number"))

        all_comments = []
        for num in pr_numbers:
            comments = fetch_paginated(session, f"/repos/{owner}/{repo}/pulls/{num}/comments")
            for c in comments:
                c["repo"] = full
                c["pr_number"] = num
            all_comments.extend(comments)
            time.sleep(REQUEST_DELAY)

        write_jsonl(repo_dir, "pr_comments", all_comments)
        completed.add(full)
        state["completed"] = sorted(completed)
        save_state("pr_comments", state)
        print(f"  [pr_comments] {full}: {len(all_comments)}")


def crawl_pr_reviews(session: requests.Session, repos: list, state: dict):
    """PR reviews / operate_logs —— 端点需实测。"""
    if not REVIEWS_AVAILABLE:
        state["completed"] = sorted(f"{o}/{r}" for o, r in repos)
        state["skipped"] = True
        state["reason"] = "Gitee API 无 /pulls/{n}/reviews 端点（404）"
        save_state("pr_reviews", state)
        print("[pr_reviews] 端点不可用，整表跳过")
        return
    completed = set(state.get("completed", []))
    for owner, repo in repos:
        full = f"{owner}/{repo}"
        if full in completed:
            continue
        repo_dir = RAW_DIR / owner / repo
        repo_dir.mkdir(parents=True, exist_ok=True)

        pr_file = repo_dir / "pull_requests.jsonl"
        pr_numbers = []
        if pr_file.exists():
            with open(pr_file, "r", encoding="utf-8") as f:
                for line in f:
                    pr = json.loads(line)
                    pr_numbers.append(pr.get("number"))

        all_reviews = []
        # 尝试 reviews 端点
        for num in pr_numbers:
            reviews, status = gitee_get(session, f"/repos/{owner}/{repo}/pulls/{num}/reviews")
            if status == 200 and isinstance(reviews, list):
                for r in reviews:
                    r["repo"] = full
                    r["pr_number"] = num
                    r["source_endpoint"] = "reviews"
                all_reviews.extend(reviews)
            time.sleep(REQUEST_DELAY)

        # 如果 reviews 端点不可用，尝试 operate_logs（仓库级）
        if not all_reviews:
            logs, status = gitee_get(session, f"/repos/{owner}/{repo}/operate_logs")
            if status == 200 and isinstance(logs, list):
                for lg in logs:
                    lg["repo"] = full
                    lg["source_endpoint"] = "operate_logs"
                all_reviews.extend(logs)

        write_jsonl(repo_dir, "pr_reviews", all_reviews)
        completed.add(full)
        state["completed"] = sorted(completed)
        save_state("pr_reviews", state)
        print(f"  [pr_reviews] {full}: {len(all_reviews)}")


def crawl_pr_timeline(session: requests.Session, repos: list, state: dict):
    """PR timeline —— 端点需实测。"""
    if not TIMELINE_AVAILABLE:
        state["completed"] = sorted(f"{o}/{r}" for o, r in repos)
        state["skipped"] = True
        state["reason"] = "Gitee API 无 /pulls/{n}/events 端点（404）"
        save_state("pr_timeline", state)
        print("[pr_timeline] 端点不可用，整表跳过")
        return
    completed = set(state.get("completed", []))
    for owner, repo in repos:
        full = f"{owner}/{repo}"
        if full in completed:
            continue
        repo_dir = RAW_DIR / owner / repo
        repo_dir.mkdir(parents=True, exist_ok=True)

        pr_file = repo_dir / "pull_requests.jsonl"
        pr_numbers = []
        if pr_file.exists():
            with open(pr_file, "r", encoding="utf-8") as f:
                for line in f:
                    pr = json.loads(line)
                    pr_numbers.append(pr.get("number"))

        all_events = []
        # 尝试 PR 级事件
        for num in pr_numbers:
            events, status = gitee_get(session, f"/repos/{owner}/{repo}/pulls/{num}/events")
            if status == 200 and isinstance(events, list):
                for e in events:
                    e["repo"] = full
                    e["pr_number"] = num
                    e["source_endpoint"] = "pull_events"
                all_events.extend(events)
            time.sleep(REQUEST_DELAY)

        # 回退：仓库级 operate_logs
        if not all_events:
            logs, status = gitee_get(session, f"/repos/{owner}/{repo}/operate_logs")
            if status == 200 and isinstance(logs, list):
                for lg in logs:
                    lg["repo"] = full
                    lg["source_endpoint"] = "operate_logs"
                all_events.extend(logs)

        write_jsonl(repo_dir, "pr_timeline", all_events)
        completed.add(full)
        state["completed"] = sorted(completed)
        save_state("pr_timeline", state)
        print(f"  [pr_timeline] {full}: {len(all_events)}")


# ============================================================================
# 主流程
# ============================================================================
def main():
    parser = argparse.ArgumentParser(description="Gitee 仓库行为数据采集")
    parser.add_argument("--table", choices=TABLES + ["all"], default="all",
                        help="采集哪张表（默认 all）")
    parser.add_argument("--since", default="2020-01-01",
                        help="行为数据起始日期（默认 2020-01-01）")
    parser.add_argument("--selected", type=Path, default=SELECTED_FILE,
                        help="选中仓库 JSON 路径")
    parser.add_argument("--only-repo", type=str, default=None,
                        help="仅采集指定仓库，格式 owner/repo")
    args = parser.parse_args()

    if args.only_repo:
        try:
            owner, repo = args.only_repo.split("/")
            repos = [(owner, repo)]
            print(f"[main] 仅采集指定仓库: {args.only_repo}")
        except ValueError:
            print("[error] --only-repo 格式错误，应为 owner/repo")
            sys.exit(2)
    else:
        if not args.selected.exists():
            print(f"[error] 未找到 {args.selected}，请先运行 select_gitee_repos.py")
            sys.exit(2)
        with open(args.selected, "r", encoding="utf-8") as f:
            selected = json.load(f)
        repos = [(s["owner"], s["repo"]) for s in selected if s.get("selected")]
        print(f"[main] 加载 {len(repos)} 个选中仓库")

    if not GITEE_TOKEN and not GITEE_TOKENS:
        print("[warn] 未设置 GITEE_TOKEN / GITEE_TOKENS，可能触发严格限速。")
    else:
        print(f"[info] 已加载 {len(GITEE_TOKENS or ([GITEE_TOKEN] if GITEE_TOKEN else []))} 个 Gitee token")

    session = requests.Session()
    session.headers.update(HEADERS)

    probe_capabilities(session, repos)

    tables_to_run = TABLES if args.table == "all" else [args.table]

    dispatch = {
        "repos": lambda: crawl_repos(session, repos, load_state("repos")),
        "issues": lambda: crawl_issues(session, repos, load_state("issues"), args.since),
        "issue_comments": lambda: crawl_issue_comments(session, repos, load_state("issue_comments"), args.since),
        "pull_requests": lambda: crawl_pull_requests(session, repos, load_state("pull_requests"), args.since),
        "pr_comments": lambda: crawl_pr_comments(session, repos, load_state("pr_comments")),
        "pr_reviews": lambda: crawl_pr_reviews(session, repos, load_state("pr_reviews")),
        "pr_timeline": lambda: crawl_pr_timeline(session, repos, load_state("pr_timeline")),
    }

    for table in tables_to_run:
        print(f"\n[table] {table}")
        dispatch[table]()


if __name__ == "__main__":
    main()
