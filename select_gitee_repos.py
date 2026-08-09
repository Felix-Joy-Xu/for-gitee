#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Gitee 仓库选择器（中美对比版）
===========================
逻辑与 GitHub 抓取一致：
  1. 生产性软件：Gitee 上高 star、中文社区活跃的原生仓库
  2. AI/LLM：国产 AI 框架、模型、工具在 Gitee 上的主仓
  3. 评估 2020 年以来的 issue/PR 活跃度与 mirror 关系，按分数排序选取

输出：
  - modelscope_output/gitee_selected_repos.json
  - modelscope_output/gitee_repo_candidates.json（含筛选过程元数据）
"""
import argparse
import json
import os
import sys
import time
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
OUTPUT_DIR.mkdir(exist_ok=True)

OUT_SELECTED = OUTPUT_DIR / "gitee_selected_repos.json"
OUT_CANDIDATES = OUTPUT_DIR / "gitee_repo_candidates.json"

GITEE_API = "https://gitee.com/api/v5"
HEADERS = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}

REQUEST_DELAY = 0.3
MAX_PER_PAGE = 100

# Gitee 仓库候选清单（中美对比逻辑）
# 规则：
#   1. 生产性软件：Gitee 上高 star、中文社区活跃的原生仓库
#   2. AI/LLM：国产 AI 框架、模型、工具在 Gitee 上的主仓
#   3. 通过 API 获取真实 star 数与 2020 年以来的 issue/PR 活跃度，再按分数排序选取
# 类别：AI_LLM 或 GENERAL
CANDIDATE_REPOS = [
    # AI/LLM 类
    {"gitee": "mindspore/mindspore", "category": "AI_LLM", "label": "昇思 MindSpore"},
    {"gitee": "mindspore/mindspore-lite", "category": "AI_LLM", "label": "MindSpore Lite"},
    {"gitee": "Jittor/Jittor", "category": "AI_LLM", "label": "计图 Jittor"},
    {"gitee": "Jittor/jittorllms", "category": "AI_LLM", "label": "Jittor LLMs"},
    {"gitee": "InternLM/InternLM", "category": "AI_LLM", "label": "书生·浦语"},
    {"gitee": "InternLM/lmdeploy", "category": "AI_LLM", "label": "LMDeploy"},
    {"gitee": "InternLM/xtuner", "category": "AI_LLM", "label": "XTuner"},
    {"gitee": "InternLM/opencompass", "category": "AI_LLM", "label": "OpenCompass"},
    {"gitee": "InternLM/MindSearch", "category": "AI_LLM", "label": "MindSearch"},
    {"gitee": "ZhipuAI/ChatGLM-6B", "category": "AI_LLM", "label": "ChatGLM-6B"},
    {"gitee": "ZhipuAI/chatglm3-6b", "category": "AI_LLM", "label": "ChatGLM3-6B"},
    {"gitee": "baichuan-inc/Baichuan2", "category": "AI_LLM", "label": "百川 Baichuan2"},
    {"gitee": "OpenBMB/CPM-Bee", "category": "AI_LLM", "label": "OpenBMB CPM-Bee"},
    {"gitee": "OpenBMB/MiniCPM", "category": "AI_LLM", "label": "MiniCPM"},
    {"gitee": "opengauss/openGauss", "category": "AI_LLM", "label": "openGauss"},
    {"gitee": "opengauss/openGauss-AI", "category": "AI_LLM", "label": "openGauss-AI"},
    {"gitee": "OpenAtomFoundation/pacific-ai", "category": "AI_LLM", "label": "OpenAtom Pacific-AI"},
    {"gitee": "modelscope/FunASR", "category": "AI_LLM", "label": "FunASR"},
    {"gitee": "XiaoHuAI/AgentVerse", "category": "AI_LLM", "label": "AgentVerse"},

    # 通用开源/生产性软件类（高 star 中文原生仓库）
    {"gitee": "labuladong/fucking-algorithm", "category": "GENERAL", "label": "labuladong 算法"},
    {"gitee": "macrozheng/mall", "category": "GENERAL", "label": "mall 电商系统"},
    {"gitee": "xuxueli/xxl-job", "category": "GENERAL", "label": "XXL-JOB"},
    {"gitee": "elunez/eladmin", "category": "GENERAL", "label": "EL-ADMIN"},
    {"gitee": "lenve/vhr", "category": "GENERAL", "label": "微人事"},
    {"gitee": "baomidou/mybatis-plus", "category": "GENERAL", "label": "MyBatis-Plus"},
    {"gitee": "apolloconfig/apollo", "category": "GENERAL", "label": "Apollo"},
    {"gitee": "seata/seata", "category": "GENERAL", "label": "Seata"},
    {"gitee": "jeecgboot/jeecg-boot", "category": "GENERAL", "label": "JeecgBoot"},
    {"gitee": "ruoyi/ruoyi-vue-pro", "category": "GENERAL", "label": "RuoYi-Vue-Pro"},
    {"gitee": "newbee-ltd/newbee-mall", "category": "GENERAL", "label": "NewBee Mall"},
    {"gitee": "alibaba/easyexcel", "category": "GENERAL", "label": "EasyExcel"},
    {"gitee": "alibaba/canal", "category": "GENERAL", "label": "Canal"},
    {"gitee": "alibaba/Sentinel", "category": "GENERAL", "label": "Sentinel"},
    {"gitee": "alibaba/arthas", "category": "GENERAL", "label": "Arthas"},
    {"gitee": "Tencent/weui", "category": "GENERAL", "label": "WeUI"},
    {"gitee": "Tencent/vConsole", "category": "GENERAL", "label": "vConsole"},
    {"gitee": "Tencent/wepy", "category": "GENERAL", "label": "WePY"},
    {"gitee": "Tencent/APIJSON", "category": "GENERAL", "label": "APIJSON"},
    {"gitee": "youzan/vant", "category": "GENERAL", "label": "Vant"},
    {"gitee": "openeuler/openEuler", "category": "GENERAL", "label": "openEuler"},
    {"gitee": "openharmony/openharmony", "category": "GENERAL", "label": "OpenHarmony"},
    {"gitee": "openlookeng/openLooKeng", "category": "GENERAL", "label": "openLooKeng"},
    {"gitee": "pingcap/tidb", "category": "GENERAL", "label": "TiDB"},
    {"gitee": "sofastack/sofa-boot", "category": "GENERAL", "label": "SOFABoot"},
    {"gitee": "apache/dolphinscheduler", "category": "GENERAL", "label": "DolphinScheduler"},
    {"gitee": "apache/shardingsphere", "category": "GENERAL", "label": "ShardingSphere"},
    {"gitee": "apache/rocketmq", "category": "GENERAL", "label": "RocketMQ"},
    {"gitee": "Meituan/Leaf", "category": "GENERAL", "label": "Leaf"},
    {"gitee": "didi/DoraemonKit", "category": "GENERAL", "label": "DoraemonKit"},
    {"gitee": "Baidu/uid-generator", "category": "GENERAL", "label": "uid-generator"},
    {"gitee": "opengoofy/hippo4j", "category": "GENERAL", "label": "Hippo4j"},
]


# ============================================================================
# Token 轮换器
# ============================================================================
class TokenRotator:
    """Gitee token 轮换器，支持多 token 避免限流/单 token 失效。"""
    def __init__(self):
        self.tokens = list(dict.fromkeys(GITEE_TOKENS or ([GITEE_TOKEN] if GITEE_TOKEN else [])))
        self.idx = 0
        self.failed = set()

    def count(self) -> int:
        return len(self.tokens)

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

    def summary(self) -> str:
        if not self.tokens:
            return "no tokens"
        first = self.tokens[0]
        masked = first[:4] + "****" + first[-4:] if len(first) >= 8 else "****"
        return f"{len(self.tokens)} tokens, first={masked}"


TOKEN_ROTATOR = TokenRotator()


# ============================================================================
# 工具函数
# ============================================================================
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
            if r.status_code in (401, 429, 403):
                # 401 可能是当前 token 失效，尝试下一个；429/403 是限流
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


def fetch_paginated(session: requests.Session, path: str, params: dict = None,
                    since: str = None, since_key: str = "since",
                    max_pages: int = 10) -> list:
    """分页拉取 Gitee 列表接口。"""
    all_items = []
    p = dict(params) if params else {}
    p["per_page"] = MAX_PER_PAGE
    page = 1
    consecutive_empty = 0

    while page <= max_pages:
        p["page"] = page
        if since and since_key:
            p[since_key] = since

        data, status = gitee_get(session, path, p)
        if status != 200:
            break
        if not isinstance(data, list):
            break
        if not data:
            consecutive_empty += 1
            if consecutive_empty >= 2:
                break
        else:
            consecutive_empty = 0
            all_items.extend(data)
            if since and data:
                last_created = data[-1].get("created_at") or data[-1].get("createdAt", "")
                if last_created and last_created < since:
                    break

        if len(data) < MAX_PER_PAGE:
            break
        page += 1
        time.sleep(REQUEST_DELAY)

    return all_items


def classify_governance(owner_type: str, description: str, org_login: str) -> str:
    """简单判断治理主体类型。"""
    desc = (description or "").lower()
    org = org_login.lower()
    if owner_type == "Organization":
        if any(k in org for k in ["foundation", "apache", "openatom", "openeuler", "openharmony", "opengauss"]):
            return "foundation"
        return "enterprise"
    return "community"


def is_github_mirror(repo_info: dict) -> bool:
    """判断仓库是否为 GitHub 镜像。"""
    mirror_url = repo_info.get("mirror_url") or ""
    html_url = repo_info.get("html_url") or ""
    parent = repo_info.get("parent") or repo_info.get("source")
    has_github_parent = bool(parent and "github.com" in json.dumps(parent))
    return has_github_parent or "github.com" in mirror_url or "github.com" in html_url


# ============================================================================
# 主流程
# ============================================================================
def evaluate_repo(session: requests.Session, owner: str, repo: str, since: str,
                  exclude_mirrors: bool, activity_threshold: int) -> dict:
    """评估单个候选仓库。"""
    result = {
        "owner": owner,
        "repo": repo,
        "full_name": f"{owner}/{repo}",
        "status": "pending",
        "is_mirror": None,
        "activity_2y": 0,
        "governance_type": None,
        "selected": False,
        "reason": "",
    }

    repo_info, status = gitee_get(session, f"/repos/{owner}/{repo}")
    if status != 200 or not isinstance(repo_info, dict):
        result["status"] = "error"
        result["reason"] = f"API error {status}"
        return result

    result["status"] = "ok"
    result["repo_info"] = {
        "id": repo_info.get("id"),
        "name": repo_info.get("name"),
        "full_name": repo_info.get("full_name"),
        "description": repo_info.get("description"),
        "language": repo_info.get("language"),
        "stargazers_count": repo_info.get("stargazers_count"),
        "watchers_count": repo_info.get("watchers_count"),
        "forks_count": repo_info.get("forks_count"),
        "open_issues_count": repo_info.get("open_issues_count"),
        "created_at": repo_info.get("created_at"),
        "updated_at": repo_info.get("updated_at"),
        "owner_type": (repo_info.get("owner") or {}).get("type"),
        "owner_login": (repo_info.get("owner") or {}).get("login"),
    }

    mirror = is_github_mirror(repo_info)
    result["is_mirror"] = mirror
    if mirror and exclude_mirrors:
        result["reason"] = "GitHub mirror (excluded)"
        return result

    result["governance_type"] = classify_governance(
        result["repo_info"]["owner_type"],
        result["repo_info"]["description"],
        result["repo_info"]["owner_login"],
    )

    issues = fetch_paginated(session, f"/repos/{owner}/{repo}/issues",
                             {"state": "all", "sort": "created", "direction": "desc"},
                             since=since, max_pages=5)
    pulls = fetch_paginated(session, f"/repos/{owner}/{repo}/pulls",
                            {"state": "all", "sort": "created", "direction": "desc"},
                            since=since, max_pages=5)

    result["activity_2y"] = len(issues) + len(pulls)

    if result["activity_2y"] < activity_threshold:
        result["reason"] = f"activity too low ({result['activity_2y']})"
        return result

    result["selected"] = True
    result["reason"] = "selected" + (" (mirror)" if mirror else "")
    return result


def main():
    parser = argparse.ArgumentParser(description="Gitee 仓库选择器（中美对照清单版）")
    parser.add_argument("--since", default="2024-01-01", help="活跃度统计起始日期（默认 2024-01-01）")
    parser.add_argument("--target", type=int, default=35, help="目标仓库数（默认 35）")
    parser.add_argument("--activity-threshold", type=int, default=5, help="近 since 以来 issue + PR 门槛（默认 5）")
    parser.add_argument("--exclude-mirrors", action="store_true", help="排除 GitHub 镜像（默认保留，以便中美对照）")
    parser.add_argument("--dry-run", action="store_true", help="只输出候选，不写入文件")
    args = parser.parse_args()

    if TOKEN_ROTATOR.count() == 0:
        print("[error] 未设置 GITEE_TOKEN / GITEE_TOKENS，无法调用 Gitee API。请检查 GitHub Secrets。")
        sys.exit(1)
    print(f"[info] {TOKEN_ROTATOR.summary()}")

    session = requests.Session()
    session.headers.update(HEADERS)

    all_candidates = []
    for item in CANDIDATE_REPOS:
        owner, repo = item["gitee"].split("/", 1)
        print(f"[eval] {owner}/{repo} ({item['category']}) ...")
        info = evaluate_repo(session, owner, repo, args.since,
                             args.exclude_mirrors, args.activity_threshold)
        info.update({
            "category": item["category"],
            "label": item["label"],
        })
        all_candidates.append(info)
        time.sleep(REQUEST_DELAY)

    # 按类别分别选取，保持 AI/LLM 与通用大致各半
    # 排序：优先 star 数（高 star 生产性软件），其次活跃度
    def repo_score(c):
        stars = (c.get("repo_info") or {}).get("stargazers_count", 0) or 0
        return stars + c["activity_2y"] * 10

    selected = []
    ai_selected = [c for c in all_candidates if c["category"] == "AI_LLM" and c["selected"]]
    gen_selected = [c for c in all_candidates if c["category"] == "GENERAL" and c["selected"]]

    ai_selected.sort(key=lambda x: -repo_score(x))
    gen_selected.sort(key=lambda x: -repo_score(x))

    half = args.target // 2
    selected.extend(ai_selected[:half])
    selected.extend(gen_selected[:half])

    if len(selected) < args.target:
        remaining = [c for c in all_candidates if c["selected"] and c not in selected]
        remaining.sort(key=lambda x: -repo_score(x))
        selected.extend(remaining[:args.target - len(selected)])

    selected = selected[:args.target]

    print(f"\n[summary] 候选总数: {len(all_candidates)}, 选中: {len(selected)}")
    print(f"  AI/LLM: {len([s for s in selected if s['category']=='AI_LLM'])}")
    print(f"  General: {len([s for s in selected if s['category']=='GENERAL'])}")
    print(f"  Mirrors: {len([s for s in selected if s['is_mirror']])}")
    print(f"  Governance: enterprise={len([s for s in selected if s['governance_type']=='enterprise'])}, "
          f"foundation={len([s for s in selected if s['governance_type']=='foundation'])}, "
          f"community={len([s for s in selected if s['governance_type']=='community'])}")

    if args.dry_run:
        print("\n[detailed candidates]")
        for c in all_candidates:
            print(f"  {c['full_name']:40s} selected={str(c['selected']):5s} status={c['status']:6s} "
                  f"mirror={c['is_mirror']} activity={c['activity_2y']:4d} gov={c['governance_type'] or 'N/A':12s} reason={c['reason']}")
        print("\n[selected]")
        for s in selected:
            print(f"  {s['full_name']}  activity_2y={s['activity_2y']}  stars={s.get('repo_info',{}).get('stargazers_count')}  gov={s['governance_type']}")
        return

    with open(OUT_SELECTED, "w", encoding="utf-8") as f:
        json.dump(selected, f, ensure_ascii=False, indent=2)

    with open(OUT_CANDIDATES, "w", encoding="utf-8") as f:
        json.dump(all_candidates, f, ensure_ascii=False, indent=2)

    print(f"[save] {OUT_SELECTED}")
    print(f"[save] {OUT_CANDIDATES}")


if __name__ == "__main__":
    main()
