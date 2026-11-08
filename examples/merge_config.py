#!/usr/bin/env python3
"""把 SSO 配置片段合并进 ~/.aws/config：同名段整段替换，其余段原样保留。
用法：python3 merge_config.py sso.fragment [--dry-run]"""
import difflib, os, re, sys

SEC = re.compile(r"^\s*\[([^\]]+)\]\s*$")

def parse(text):
    """返回 (文件头部, [(段名, [该段的行...]), ...])"""
    head, secs = [], []
    for line in text.splitlines(keepends=True):
        m = SEC.match(line)
        if m:
            secs.append((re.sub(r"\s+", " ", m.group(1).strip()), [line]))
        elif secs:
            secs[-1][1].append(line)
        else:
            head.append(line)
    return head, secs

frag_path, dry = sys.argv[1], "--dry-run" in sys.argv
cfg = os.path.expanduser("~/.aws/config")
os.makedirs(os.path.dirname(cfg), mode=0o700, exist_ok=True)
old = open(cfg, encoding="utf-8").read() if os.path.exists(cfg) else ""
frag = open(frag_path, encoding="utf-8").read()

frag_head, frag_secs = parse(frag)
names = {n for n, _ in frag_secs}
# 片段开头的注释行在合并后会落到上一段末尾；重跑前先去掉，保证可重复执行
drop = {l for l in frag_head if l.strip()}
head, old_secs = parse("".join(l for l in old.splitlines(True) if l not in drop))
kept = [(n, b) for n, b in old_secs if n not in names]
body = ("".join(head) + "".join("".join(b) for _, b in kept)).rstrip("\n")
out = body + "\n\n" + frag if body.strip() else frag

print("整段替换：", sorted(names & {n for n, _ in old_secs}) or "无")
print("原样保留：", [n for n, _ in kept] or "无")
if dry:
    sys.stdout.writelines(difflib.unified_diff(
        old.splitlines(True), out.splitlines(True), "config（当前）", "config（合并后）"))
elif out != old:
    with open(cfg + ".tmp", "w", encoding="utf-8") as f:
        f.write(out)
    os.chmod(cfg + ".tmp", 0o600)
    os.replace(cfg + ".tmp", cfg)          # 原子替换，避免写到一半
    print("已写入", cfg)
