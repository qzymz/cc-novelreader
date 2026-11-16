#!/usr/bin/env python3
"""用 Git 凭据管理器里的 GitHub 凭证创建仓库并推送。
token 只在进程内存中流转：不打印、不落盘、不进命令行参数。"""
import json
import subprocess
import sys
import urllib.request
from pathlib import Path

REPO = "cc-novelreader"
root = Path(__file__).resolve().parent.parent


def get_credential():
    out = subprocess.run(
        ["git", "credential", "fill"],
        input="protocol=https\nhost=github.com\n\n",
        capture_output=True, text=True).stdout
    d = dict(line.split("=", 1) for line in out.splitlines() if "=" in line)
    if "username" not in d or "password" not in d:
        print("NO_CRED")
        sys.exit(2)
    return d["username"].strip(), d["password"].strip()


def api(token, method, url, payload=None):
    req = urllib.request.Request(
        "https://api.github.com" + url,
        data=json.dumps(payload).encode() if payload is not None else None,
        method=method,
        headers={"Authorization": f"token {token}",
                 "Accept": "application/vnd.github+json",
                 "User-Agent": "cc-novelreader-setup"})
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode() or "{}")
        except Exception:
            return e.code, {}


def main():
    user, token = get_credential()
    # 探测 token 身份与权限
    st, me = api(token, "GET", "/user")
    if st != 200:
        print(f"AUTH_FAILED {st}: {me.get('message', '')}")
        sys.exit(1)
    login = me.get("login", user)
    print(f"authenticated as: {login}")

    st, repo = api(token, "POST", "/user/repos",
                   {"name": REPO, "description":
                    "Claude Code look-alike terminal novel reader (摸鱼阅读器)",
                    "private": False, "has_issues": True})
    if st == 201:
        print(f"created: {repo['html_url']} (public)")
    elif st == 422 and "already exist" in str(repo.get("errors", "")):
        print(f"exists: https://github.com/{login}/{REPO}")
    else:
        print(f"CREATE_FAILED {st}: {repo.get('message', '')}")
        print("(token 可能缺少 repo 权限，请在终端运行: gh auth login)")
        sys.exit(1)

    url = f"https://github.com/{login}/{REPO}.git"
    subprocess.run(["git", "remote", "remove", "origin"], cwd=root,
                   capture_output=True)
    r = subprocess.run(["git", "remote", "add", "origin", url], cwd=root)
    if r.returncode:
        sys.exit(1)
    p = subprocess.run(["git", "push", "-u", "origin", "main"],
                       cwd=root, capture_output=True, text=True)
    print(p.stdout.strip() or p.stderr.strip())
    if p.returncode == 0:
        print(f"PUSH_OK https://github.com/{login}/{REPO}")
    else:
        print("PUSH_FAILED")
        sys.exit(1)


if __name__ == "__main__":
    main()
