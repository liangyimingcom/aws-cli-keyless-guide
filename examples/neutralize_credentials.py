#!/usr/bin/env python3
"""删除 ~/.aws/credentials 中与 SSO profile 同名的静态密钥段。
用法：python3 neutralize_credentials.py default prod prod-readonly staging china china-nx [--dry-run]"""
import os, re, sys

targets = {a for a in sys.argv[1:] if a != "--dry-run"}
dry = "--dry-run" in sys.argv
path = os.path.expanduser("~/.aws/credentials")
if not os.path.exists(path):
    sys.exit("credentials 文件不存在，无需处理")

out, dropped, skip = [], [], False
for line in open(path, encoding="utf-8").read().splitlines(keepends=True):
    m = re.match(r"^\s*\[([^\]]+)\]\s*$", line)
    if m:
        skip = m.group(1).strip() in targets
        if skip:
            dropped.append(m.group(1).strip())
    if not skip:
        out.append(line)

print("将删除的段：", dropped or "无")
if dropped and not dry:
    with open(path + ".tmp", "w", encoding="utf-8") as f:
        f.writelines(out)
    os.chmod(path + ".tmp", 0o600)
    os.replace(path + ".tmp", path)
    print("已写入", path)
