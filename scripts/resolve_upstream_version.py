#!/usr/bin/env python3
"""解析上游最新发布版本，供 CI 注入 App 版本号。

上游 rockswang/wild-work 没有 GitHub Releases，master 源码里也不含 version.go，
因此取 tags 中的最大版本号（形如 v2.11.7）。

主通道用 GitHub API（CI 中传 GITHUB_TOKEN，配额 5000/h），
备用通道用 `git ls-remote --tags`（不受匿名 API 限流影响）。
两者都失败时使用 FALLBACK_VERSION（已知上游版本）并打印 warning，不阻断构建。

用法：
  python3 scripts/resolve_upstream_version.py --github-env   # 写入 $GITHUB_ENV
  python3 scripts/resolve_upstream_version.py                # 打印 KEY=VALUE
"""
import argparse
import json
import os
import re
import subprocess
import sys
import urllib.request

REPO = "rockswang/wild-work"
# 仅在两个查询通道都失败时兜底；正常构建由 tags 实时解析，无需手动维护。
FALLBACK_VERSION = "2.11.7"
FALLBACK_CODE = "21107"


def parse(tag) -> tuple | None:
    """v2.11.7 / 2.11.7-beta -> ((2,11,7), '2.11.7', '21107')。"""
    m = re.search(r"(\d+)\.(\d+)(?:\.(\d+))?", str(tag))
    if not m:
        return None
    major, minor, patch = int(m.group(1)), int(m.group(2)), int(m.group(3) or 0)
    # Android versionCode 必须是递增整数；约定 minor / patch < 100。
    code = major * 10000 + minor * 100 + patch
    return (major, minor, patch), f"{major}.{minor}.{patch}", str(code)


def api_tags() -> list:
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    headers = {"User-Agent": "wild-work-mobile-ci", "Accept": "application/vnd.github+json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    url = f"https://api.github.com/repos/{REPO}/tags?per_page=20"
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=20) as resp:
        return [item.get("name", "") for item in json.load(resp)]


def git_tags() -> list:
    out = subprocess.run(
        ["git", "ls-remote", "--tags", "--refs", f"https://github.com/{REPO}.git"],
        capture_output=True, text=True, timeout=30, check=True,
    ).stdout
    return [line.split("refs/tags/", 1)[1] for line in out.splitlines() if "refs/tags/" in line]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--github-env", action="store_true", help="追加到 $GITHUB_ENV")
    args = ap.parse_args()

    tags, channel = [], "none"
    for name, fn in (("GitHub API", api_tags), ("git ls-remote", git_tags)):
        try:
            tags = fn()
            if tags:
                channel = name
                break
        except Exception as exc:
            print(f"::warning::{name} 查询失败: {exc}")

    version, code = FALLBACK_VERSION, FALLBACK_CODE
    candidates = [p for p in (parse(t) for t in tags) if p]
    if candidates:
        _, version, code = max(candidates, key=lambda x: x[0])
        print(f"上游版本（{channel}）: {version} / versionCode {code}，共 {len(tags)} 个 tag")
    else:
        print(f"::warning::两个通道均未解析出上游版本，使用兜底 {FALLBACK_VERSION}")

    lines = [f"WW_VERSION={version}", f"WW_VERSION_CODE={code}"]
    if args.github_env:
        path = os.environ.get("GITHUB_ENV")
        if not path:
            sys.exit("✗ 缺少 GITHUB_ENV，仅可在 GitHub Actions 内使用 --github-env")
        with open(path, "a", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
    else:
        print("\n".join(lines))


if __name__ == "__main__":
    main()
