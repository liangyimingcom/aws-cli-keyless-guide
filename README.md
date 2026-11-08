# 让电脑里"无钥可偷"：30 分钟把 AWS CLI 从长期访问密钥迁移到 AWS IAM Identity Center


先做个小实验。打开终端，执行：

```bash
grep -c 'AKIA' ~/.aws/credentials
```

如果结果大于 0，你的电脑里就存着**永不过期的 AWS 钥匙**。它们可能已经跟着你换过两台电脑，被同步过一次网盘，还被好几个 IDE 插件和 AI 编码助手读取过。只要没人到 IAM 里把它们停用，它们就一直有效。

本文分享一次真实的迁移实践：在**不改动任何现有脚本**的前提下，用约 30 分钟把运维工作站上的长期访问密钥全部换成 AWS IAM Identity Center 签发的**临时凭证**，再在 AWS 侧把旧密钥彻底停用并删除。迁移完成后，上面那条命令的结果会变成 0。电脑上即使有程序把凭证文件翻一遍，也找不到一把能长期使用的访问密钥。

> **适合读者**：使用 AWS CLI 管理多个账号的运维和平台工程师，需要了解 IAM 和 AWS CLI profile 的基本概念（Level 200–300）。
> **你将获得**：8 个可直接照做的步骤、每一步的检查点，以及备份、合并配置、清理凭证、验证、单会话退出等关键示例代码。
>
> 🤖 **不想自己敲命令？** 把一段提示词粘贴给 AI 编码助手 **Kiro**，它会读完本文并替你执行迁移，你只需确认和登录。见下文「[让 Kiro 替你完成迁移](#让-kiro-替你完成迁移)」。

> 📁 文中的示例脚本也单独放在 [`examples/`](examples/) 目录里，可以直接下载使用。

---

## 背景：为什么现在是时候了

### 官方建议早已明确

亚马逊云科技在 [IAM 安全最佳实践](https://docs.aws.amazon.com/IAM/latest/UserGuide/best-practices.html) 中建议：**人类用户应通过身份提供商联合访问 AWS，并使用临时凭证**；需要集中管理多账号访问时，推荐使用 AWS IAM Identity Center。[AWS Well-Architected 框架安全支柱 SEC02-BP02](https://docs.aws.amazon.com/wellarchitected/latest/security-pillar/sec_identities_unique.html) 也把"开发者使用 IAM 用户的长期访问密钥，而不是通过联合身份从 CLI 获取临时凭证"列为常见反模式，风险等级为**高**。

### 现实中的三个顾虑

不少团队仍然沿用"创建 IAM 用户 → 生成访问密钥 → 写入 `~/.aws/credentials`"的做法，常见的顾虑有三个：

- **担心改造成本高**：脚本、Terraform、CDK、IDE 插件都按 profile 读取凭证，改动范围看起来很大。
- **账号多，关系复杂**：生产、测试、开发账号分布在一个或多个组织里，有的团队还同时运维全球区域和中国区域的账号。
- **不敢动**：不清楚哪把密钥还在用，担心一删就影响生产。

### 工作站环境正在变化

如今，AI 编码助手、各类 CLI 工具和依赖包的安装脚本，都以当前用户身份运行在工程师的电脑上，能读取用户目录下的文件。按照 [IAM 用户指南](https://docs.aws.amazon.com/IAM/latest/UserGuide/id_credentials_access-keys.html) 的说明，共享凭证文件中的访问密钥是**明文存储**的。也就是说，本机能读到 `~/.aws/credentials` 的程序越多，长期密钥被意外读取、复制、外传的可能性就越大。

治本的办法不是把密钥藏得更好，而是让工作站上**根本没有长期密钥**。

### 迁移前后对比

| 维度 | 迁移前 | 迁移后 |
|---|---|---|
| 本机保存的凭证 | 长期访问密钥（`AKIA` 开头），不会过期 | 只有登录会话缓存；调用 API 时使用临时凭证（`ASIA` 开头） |
| 有效期 | 直到被手动停用或删除 | 由权限集的会话时长控制（1–12 小时），到期自动失效 |
| 如何获取 | 复制、粘贴密钥 | 在浏览器登录 IAM Identity Center，可强制 MFA |
| 人员变动时 | 逐个账号查找、删除密钥 | 在 Identity Center 停用该用户，所有账号的访问一并收回 |
| 现有脚本 | — | profile 名称不变，`--profile`、`AWS_PROFILE` 的写法无需修改 |

---

## 方案概述

![方案架构](images/architecture.svg)

*图 1：迁移后的凭证获取流程*

1. 工程师执行 `aws sso login`，在浏览器中完成 IAM Identity Center 登录（含 MFA）。
2. AWS CLI 获得访问令牌，缓存在 `~/.aws/sso/cache/`，并通过刷新令牌自动续期。
3. 调用 AWS API 时，CLI 和 SDK 按 profile 中配置的账号和权限集，换取该账号中 `AWSReservedSSO_<权限集>_*` 角色的临时凭证。
4. 临时凭证到期后自动失效。访问令牌失效后，重新执行一次 `aws sso login` 即可。

三个关键设计点：

- **一个 Identity Center 实例对应一个 `sso-session`**。同一组织下的生产、测试、开发等多个账号，可以配置成多个 profile，共享这一次登录。
- **保留原有 profile 名称**。原来叫 `prod` 的 profile，迁移后仍然叫 `prod`，变的只是凭证来源：从静态密钥换成了 SSO。
- **全球区域与中国区域各配一个 `sso-session`**。亚马逊云科技中国区域与全球区域属于不同的分区，IAM Identity Center 是相互独立的实例。同时运维两边账号的工程师，在本机配置两个 `sso-session` 即可，两者分别登录、分别过期，互不影响。

---

## 前提条件

- macOS 或 Linux 工作站，已安装 [AWS CLI v2](https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html)。`sso-session` 配置需要 2.9.0 及以上版本，建议使用最新版本。
- 目标账号已启用 AWS IAM Identity Center，并且你有权限创建用户、权限集和账号分配。
- Python 3（macOS 自带即可），用于运行文中的示例脚本。

```bash
aws --version          # 确认输出为 aws-cli/2.x
which -a aws           # 确认 PATH 中排在第一位的是新版本
```

---

## 让 Kiro 替你完成迁移

本文的命令都可以手动执行。如果不想自己敲，最省事的办法是：**打开 [Kiro](https://kiro.dev/docs/cli/)（IDE 的对话面板或终端里的 `kiro-cli` 都可以），粘贴下面这段提示词。** Kiro 会自己读完这篇文章，按步骤替你执行，你只负责确认和登录。

把尖括号里的内容换成你在步骤 1 中拿到的信息，然后整段粘贴给 Kiro：

```text
请阅读这篇文章：https://raw.githubusercontent.com/liangyimingcom/aws-cli-keyless-guide/main/README.md
按文中步骤 2 到步骤 7，帮我把这台电脑上的 AWS CLI 从长期访问密钥迁移到 IAM Identity Center。

我的信息（profile 名称 = 账号 ID / 权限集）：
- SSO Start URL：<https://d-xxxxxxxxxx.awsapps.com/start>，SSO Region：<us-east-1>
- default = <111111111111> / <AdministratorAccess>
- prod    = <111111111111> / <AdministratorAccess>
- prod-readonly = <111111111111> / ReadOnlyAccess
- （如有中国区域）Start URL：<...>，Region：cn-northwest-1，china = <333333333333> / <AdministratorAccess>

请遵守：
1. 每一步先说明要执行的命令，等我回复"继续"再执行；每步做完按文中检查点汇报结果。
2. 修改 ~/.aws 下的任何文件之前，先完成步骤 2 的备份（放在家目录，不要放进云盘同步目录）；
   改写 config 和 credentials 时，先用文中脚本的 --dry-run 给我看差异。
3. 不在输出中显示任何密钥或令牌的值，访问密钥 ID 一律打码显示（例如 AKIA****ABCD）。
4. aws sso login 由我在浏览器里完成，你给出命令后等我回复"已登录"。
5. IAM 中的停用、删除命令必须逐条列出、逐条经我确认，不得批量执行。
6. 不要执行 aws sso logout，不要删除 ~/.aws/sso/cache 下的文件。
```

整个过程中，**只有三件事需要你亲自做**：在控制台准备用户和权限集（步骤 1）、在浏览器中登录（步骤 5）、确认停用和删除哪些旧密钥（步骤 6）。其余的盘点、备份、改配置、验证、扫描，都可以交给 Kiro。

---

## 实战步骤

### 步骤 1：在 IAM Identity Center 中准备身份与权限

以下操作由管理员在控制台完成，每个 Identity Center 实例只需做一次。

1. **启用 IAM Identity Center**：推荐使用组织实例（organization instance），参见 [Getting started](https://docs.aws.amazon.com/singlesignon/latest/userguide/getting-started.html)。
2. **创建用户并开启 MFA**：在 *Settings → Authentication* 中配置多重身份验证。
3. **创建权限集**：为日常运维准备满足需要的权限集，另外**单独准备一个只读权限集**（如 `ReadOnlyAccess`），给 AI 编码助手和巡检脚本使用。
4. **设置会话时长**：权限集的会话时长默认为 1 小时，最长可设为 12 小时（参见 [Set session duration](https://docs.aws.amazon.com/singlesignon/latest/userguide/howtosessionduration.html)）。建议按工作习惯设为 4–8 小时，不要超过实际需要。
5. **分配账号**：在 *AWS accounts* 中，把用户（或组）和权限集分配到目标账号。

> **中国区域**：亚马逊云科技中国（北京）区域和中国（宁夏）区域都支持 IAM Identity Center。访问门户地址形如 `https://start.<region>.home.awsapps.cn/directory/<别名>`，详见 [IAM Identity Center in Amazon Web Services China](https://docs.amazonaws.cn/en_us/aws/latest/userguide/iam-identity-center.html)。

完成后，在访问门户（AWS access portal）的 *Access keys* 页面中记下 **SSO Start URL** 和 **SSO Region**，下一步要用。

### 步骤 2：盘点并完整备份本机凭证

动手之前，先把现状摸清楚，并做一份可以回退的完整备份。

```bash
# 只列出段名，不显示密钥
grep -n '^\[' ~/.aws/config ~/.aws/credentials

# 统计长期密钥数量（只显示前缀，不显示完整密钥 ID）
grep -oE 'AKIA[0-9A-Z]{4}' ~/.aws/credentials | sort | uniq -c

# 完整备份 ~/.aws 和 shell 启动文件，限制权限，并做完整性自检
TS=$(date +%Y%m%d-%H%M%S)
cd ~ && zip -r -q "aws-backup-$TS.zip" .aws .zshrc .zprofile .bash_profile .bashrc 2>/dev/null
chmod 600 "aws-backup-$TS.zip"
unzip -tq "aws-backup-$TS.zip" && shasum -a 256 "aws-backup-$TS.zip"
```

✅ **检查点**：`unzip -t` 输出 `No errors detected`，记下备份文件的 sha256。

⚠️ 备份包里有旧密钥的明文。不要把它放进会同步到云盘的目录；迁移验证完成后，妥善保管或删除。

### 步骤 3：编写 SSO 配置，并按段合并进 ~/.aws/config

只有一个账号时，用向导 `aws configure sso` 就够了（参见 [AWS CLI 用户指南](https://docs.aws.amazon.com/cli/latest/userguide/cli-configure-sso.html)）。账号多、又想**保留原有 profile 名称**时，更推荐先写一个配置片段，再按段合并进 `~/.aws/config`。

**3.1 编写配置片段 `sso.fragment`**

下面的示例中，同一组织的三个账号共用一个 `sso-session`，中国区域单独一个：

```ini
# ---------- 全球区域：同一组织下的生产 / 测试账号 ----------
[sso-session main-sso]
sso_start_url = https://d-1234567890.awsapps.com/start
sso_region = us-east-1
sso_registration_scopes = sso:account:access

[default]
sso_session = main-sso
sso_account_id = 111111111111
sso_role_name = AdministratorAccess
region = us-east-1

[profile prod]
sso_session = main-sso
sso_account_id = 111111111111
sso_role_name = AdministratorAccess
region = us-east-1

[profile prod-readonly]
sso_session = main-sso
sso_account_id = 111111111111
sso_role_name = ReadOnlyAccess
region = us-east-1

[profile staging]
sso_session = main-sso
sso_account_id = 222222222222
sso_role_name = PowerUserAccess
region = ap-southeast-1

# ---------- 中国区域：独立的 Identity Center 实例 ----------
[sso-session china-sso]
sso_start_url = https://start.cn-northwest-1.home.awsapps.cn/directory/example-alias
sso_region = cn-northwest-1
sso_registration_scopes = sso:account:access

[profile china]
sso_session = china-sso
sso_account_id = 333333333333
sso_role_name = AdministratorAccess
region = cn-north-1

[profile china-nx]
sso_session = china-sso
sso_account_id = 333333333333
sso_role_name = AdministratorAccess
region = cn-northwest-1
```

要点：

- **profile 名称沿用原来的**。脚本里的 `--profile prod`、Terraform 的 `profile = "prod"`、`export AWS_PROFILE=prod` 都无需修改。
- **默认 profile** 按惯例写作 `[default]`，其他 profile 写作 `[profile 名称]`。
- **profile 的默认区域可以与门户所在区域不同**。例如上面的 `china` 门户在宁夏，默认操作北京区域的资源。
- **原有段中的自定义设置要带过来**。如果原来的 `[profile prod]` 里有 `output`、`cli_pager` 等设置，请一并写入片段。

**3.2 按段合并（示例脚本 `merge_config.py`）**

合并规则：与片段**同名的段整段替换，其他段原样保留**。脚本可以重复执行，并支持先预览差异。

```python
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
```

```bash
python3 merge_config.py sso.fragment --dry-run   # 先预览
python3 merge_config.py sso.fragment             # 确认无误后正式写入
```

✅ **检查点**：预览输出中，"整段替换"只包含你打算迁移的 profile；"原样保留"里没有被误伤的段。

### 步骤 4：清理 credentials 中的同名静态段

这是最容易漏掉的一步。**当同一个 profile 在 `~/.aws/credentials` 中还有静态密钥时，AWS CLI 会优先使用静态密钥**，SSO 配置不会生效。表现为配置看起来没问题，但 `sts get-caller-identity` 返回的仍是 `user/xxx`，而不是 SSO 角色。

**示例脚本 `neutralize_credentials.py`**：只删除指定的段，不打印任何密钥内容。

```python
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
```

暂时无法迁移的其他段先保留，步骤 6 再统一处理。

另外检查 shell 启动文件里有没有硬编码的密钥环境变量。环境变量的优先级高于 profile：

```bash
grep -nE 'export[[:space:]]+AWS_(ACCESS_KEY_ID|SECRET_ACCESS_KEY|SESSION_TOKEN)' \
  ~/.zshrc ~/.zprofile ~/.bash_profile ~/.bashrc 2>/dev/null | sed -E 's/=.*/=***/'
unset AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY AWS_SESSION_TOKEN
```

### 步骤 5：登录，并逐个 profile 验证

每个 `sso-session` 登录一次：

```bash
aws sso login --sso-session main-sso
aws sso login --sso-session china-sso
# 通过 SSH 远程操作、本机没有浏览器时，加 --use-device-code
```

在浏览器中依次完成：登录 → MFA → **Allow access** → 看到 *Request approved*。

> 💡 如果浏览器自动带入了另一个用户的登录会话，可以加上 `--no-browser`，把命令输出的链接复制到浏览器的无痕窗口中打开。

**示例脚本 `verify.sh`**：逐个 profile 同时核对三项——**账号 ID 正确、身份是 SSO 角色、拿到的是 `ASIA` 临时凭证**。脚本不显示完整凭证。

```bash
#!/usr/bin/env bash
# 逐个 profile 核对：账号 ID、SSO 角色、临时凭证（只读）
unset AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY AWS_SESSION_TOKEN AWS_PROFILE
# 格式：profile 名:期望的账号 ID
CHECKS="default:111111111111 prod:111111111111 prod-readonly:111111111111 \
staging:222222222222 china:333333333333 china-nx:333333333333"

pass=0; fail=0
for item in $CHECKS; do
  p="${item%%:*}"; want="${item##*:}"
  read -r acct arn <<< "$(aws sts get-caller-identity --profile "$p" \
      --query '[Account,Arn]' --output text 2>/dev/null)"
  kid="$(aws configure export-credentials --profile "$p" --format process 2>/dev/null \
      | grep -oE '"AccessKeyId": *"[A-Z]{4}' | grep -oE '[A-Z]{4}$')"
  if [ "$acct" = "$want" ] && [[ "$arn" == *":assumed-role/AWSReservedSSO_"* ]] && [ "$kid" = "ASIA" ]; then
    echo "PASS  $p  $arn"; pass=$((pass + 1))
  else
    echo "FAIL  $p  account=${acct:-?}  arn=${arn:-?}  key=${kid:-?}"; fail=$((fail + 1))
  fi
done
grep -qE 'AKIA[0-9A-Z]{16}' ~/.aws/credentials 2>/dev/null \
  && echo "提示：credentials 中仍有长期密钥，请继续完成步骤 6"
echo "PASS=${pass}  FAIL=${fail}"
[ "$fail" -eq 0 ]
```

期望输出类似：

```
PASS  prod  arn:aws:sts::111111111111:assumed-role/AWSReservedSSO_AdministratorAccess_0123456789abcdef/alice
PASS  china arn:aws-cn:sts::333333333333:assumed-role/AWSReservedSSO_AdministratorAccess_0123456789abcdef/alice
PASS=6  FAIL=0
```

✅ **检查点**：所有 profile 都显示 `PASS`。

常见的 `FAIL` 原因：
- 显示 `key=AKIA`：说明 credentials 里还有同名静态段，回到步骤 4。
- `account` 为空：说明还没有登录，或者该账号没有分配权限集，回到步骤 1 或重新登录。

> ARN 末尾的会话名不一定是登录时输入的用户名，也可能是邮箱，这是正常的。所以脚本只核对账号 ID 和角色前缀，不核对用户名。

### 步骤 6：在 AWS 侧停用并删除旧的长期密钥

**只删除本机文件，并不等于密钥失效。** 密钥的副本可能还留在其他电脑、旧备份或聊天记录里。只有在 IAM 中停用并删除，这把密钥才算真正作废。

**6.1 只读核查本机剩余的密钥是否仍然有效**

```bash
for p in $(grep -oE '^\[[^]]+\]' ~/.aws/credentials | tr -d '[]'); do
  printf '%-25s ' "$p"
  aws sts get-caller-identity --profile="$p" --query Arn --output text 2>&1 | tail -1
done
```

- 返回 ARN：密钥仍然有效，按 6.3 处理。
- 返回 `InvalidClientTokenId`：密钥已失效，可以直接从文件中删除这一段。

> 注意写成 `--profile=NAME`。如果 profile 名称以 `-` 开头，用空格分隔的写法会被 CLI 当成参数来解析。

**6.2 在账号层面盘点所有访问密钥**

用刚配置好的 SSO profile 生成 [IAM 凭证报告](https://docs.aws.amazon.com/IAM/latest/UserGuide/id_credentials_getting-report.html)：

```bash
aws iam generate-credential-report --profile prod
# 报告生成需要几秒钟；如果提示 ReportNotPresent 或 ReportInProgress，稍等后再执行下一条
aws iam get-credential-report --profile prod --query Content --output text | base64 --decode \
  | awk -F, 'NR==1 || tolower($9)=="true" || tolower($14)=="true" {print $1","$9","$11","$14","$16}'
# 输出列：用户名, key1 是否启用, key1 最近使用时间, key2 是否启用, key2 最近使用时间
```

> 凭证报告只包含每个用户的前两把访问密钥。需要完整核对时，对相关用户执行 `aws iam list-access-keys --user-name <用户名>`。

也可以启用 [IAM Access Analyzer 的未使用访问分析](https://docs.aws.amazon.com/IAM/latest/UserGuide/access-analyzer-findings.html)，持续找出在指定时间窗口内没有被使用过的 IAM 用户访问密钥和密码。

**6.3 先停用，观察一段时间后再删除**

```bash
aws iam get-access-key-last-used --access-key-id <ACCESS_KEY_ID> --profile prod
aws iam update-access-key --user-name ops-user --access-key-id <ACCESS_KEY_ID> \
  --status Inactive --profile prod
# 观察 1–2 周，确认业务没有受到影响后，再删除
aws iam delete-access-key --user-name ops-user --access-key-id <ACCESS_KEY_ID> --profile prod
```

> 停用（Inactive）随时可以恢复，删除则不可恢复。拿不准用途的密钥，先停用，确认没有影响后再删除。

**6.4 清理本机残留的明文密钥**

除了 `~/.aws`，明文密钥还常常藏在 IDE 设置、工具配置、旧备份文件以及 AI 编码助手的会话记录里。下面的命令只读扫描，并对结果打码：

```bash
grep -rIoEn --exclude-dir=node_modules --exclude-dir=.git 'AKIA[0-9A-Z]{16}' \
  ~/.aws ~/.config ~/.zshrc ~/.bash_profile \
  ~/Library/Application\ Support/*/User/settings.json 2>/dev/null \
  | sed -E 's/(AKIA)[0-9A-Z]{12}/\1************/' | sort -u
```

扫描结果的处理方式：
- 已停用或已删除的密钥：直接删掉这一行。
- 写死在脚本或配置里的密钥：改为 `--profile <名称>` 或 `AWS_PROFILE=<名称>`。
- 要删除的旧备份文件：先确认它已经包含在步骤 2 的备份包里。

### 步骤 7：日常使用与单会话退出

迁移完成后，日常只需要记住三条命令：

```bash
aws sso login --sso-session main-sso     # 每天第一次使用，或看到 "Token has expired" 时
aws s3 ls --profile prod                 # 与迁移前的用法完全相同
aws configure export-credentials --profile prod | grep Expiration   # 查看临时凭证的到期时间（UTC）
```

**关于退出**：根据 [命令参考](https://docs.aws.amazon.com/cli/latest/reference/sso/logout.html)，`aws sso logout` 会清除本机**所有** profile 缓存的 IAM Identity Center 访问令牌，以及通过它们获取的临时凭证。如果你同时登录了全球区域和中国区域，又只想退出其中一个，可以使用下面的单会话退出脚本。

**示例脚本 `logout-one.sh`**：

```bash
#!/usr/bin/env bash
# 只退出一个 sso-session，其他会话保持登录。用法：./logout-one.sh china-sso
S="${1:?请指定 sso-session 名称}"
# sso-session 的令牌缓存文件名，是该会话名称的 SHA-1 值
TOKEN="$HOME/.aws/sso/cache/$(printf %s "$S" | shasum | cut -d' ' -f1).json"
[ -f "$TOKEN" ] && rm -f "$TOKEN" && echo "已清除 ${S} 的访问令牌缓存"
# 清除 SSO 角色的临时凭证缓存；其他会话会在下次调用时用各自的令牌自动重新获取
grep -lE '"ProviderType": ?"sso"' "$HOME"/.aws/cli/cache/*.json 2>/dev/null | xargs rm -f
```

> 本机退出只清除本地缓存。如果需要立即终止某个用户的会话（例如设备丢失），管理员可以在 IAM Identity Center 控制台查看并结束该用户的活动会话，参见 [Configure the session duration](https://docs.aws.amazon.com/singlesignon/latest/userguide/configure-user-session.html)。

### 步骤 8：为 AI 编码助手配置最小权限访问

AI 编码助手可以照常通过 AWS CLI 和 SDK 访问 AWS，但建议：

> 迁移完成后，Kiro 自己也应该改用只读 profile。可以把下面这段约定写进项目的 `AGENTS.md` 或 Kiro 的规则文件中。

- **使用单独的只读 profile**（如步骤 3 中的 `prod-readonly`），不要直接交给它管理员 profile。
- **在项目说明或 Agent 规则文件中写明鉴权约定**，例如：

```markdown
## AWS 访问约定
- 只能通过 `--profile prod-readonly` 访问 AWS；SDK 使用 `AWS_PROFILE=prod-readonly`。
- 开始工作前先执行 `aws sts get-caller-identity --profile prod-readonly` 确认身份。
- 遇到 "Token has expired" 时停止，请工程师执行 `aws sso login --sso-session main-sso`。
- 不得索要访问密钥，不得修改 ~/.aws 下的任何文件。
- 任何写操作（创建、修改、删除资源）必须先经工程师确认。
```

- 不要用 `aws configure export-credentials` 把临时凭证写进 `.env`、代码或日志。

---

## 迁移效果

| 目标 | 达成情况 | 验证方式 |
|---|---|---|
| 工作站不保存长期密钥 | 托管账号的访问密钥已从 `~/.aws/credentials` 移除，并在 IAM 中停用、删除 | `verify.sh` 全部 PASS；步骤 6.4 扫描结果为空 |
| 凭证自动过期 | 每次调用使用的都是 `ASIA` 临时凭证，按权限集会话时长自动失效 | `aws configure export-credentials` 中的 `Expiration` |
| 本地文件外泄的影响可控 | 即使本机的凭证目录被复制，里面也没有可长期使用的访问密钥 | 同上 |
| 登录受 MFA 保护 | 获取凭证必须由本人在浏览器中完成身份验证 | Identity Center 的 MFA 设置 |
| 人员变动一处处理 | 在 Identity Center 停用用户，即可收回该用户在所有账号的访问 | Identity Center → Users |
| 现有工作方式不变 | profile 名称、脚本、Terraform、CDK 都无需修改 | `aws s3 ls --profile <原名称>` |
| 可回退 | 迁移前已完整备份并通过自检 | 备份包及其 sha256 |

在笔者的实际迁移中，一台工作站涉及全球区域和中国区域共 3 个账号、7 个 profile。从备份到全部验证通过约 30 分钟，原有脚本一行未改。

---

## 进阶建议

- **防止新增长期密钥**：在 AWS Organizations 中，可以用[服务控制策略（SCP）](https://docs.aws.amazon.com/organizations/latest/userguide/orgs_manage_policies_scps.html)拒绝成员账号创建访问密钥，再为确有需要的场景单独设置例外。注意：SCP 对管理账户中的用户和角色不生效。

```json
{
  "Version": "2012-10-17",
  "Statement": [{
    "Sid": "DenyCreateAccessKey",
    "Effect": "Deny",
    "Action": "iam:CreateAccessKey",
    "Resource": "*"
  }]
}
```

- **无人值守任务使用 IAM 角色**：cron、CI/CD 等自动化任务，不适合使用需要交互登录的 SSO。
  - 在 AWS 上运行的任务，请使用 EC2 实例角色、Lambda 执行角色等。
  - 在 AWS 之外运行的任务，可以考虑 [IAM Roles Anywhere](https://docs.aws.amazon.com/rolesanywhere/latest/userguide/introduction.html) 或 OIDC 联合。
  - 更多选择参见 AWS Security Blog 文章 [Beyond IAM access keys](https://aws.amazon.com/blogs/security/beyond-iam-access-keys-modern-authentication-approaches-for-aws/)。
- **监控与告警**：为账号配置 AWS Budgets 和 AWS Cost Anomaly Detection，并结合 AWS CloudTrail 对高风险 API 调用设置告警。
- **持续治理**：定期查看 IAM 凭证报告和 IAM Access Analyzer 的未使用访问结果，确保长期密钥保持"清零"。

---

## 总结

回到开头那个小实验。迁移完成后，再执行一次：

```bash
grep -c 'AKIA' ~/.aws/credentials      # 期望结果：0
```

看到 0，说明这台工作站已经告别了永不过期的钥匙。回顾一下，整个迁移的关键只有三点：

1. **先备份、先预览，保留原有 profile 名称**。迁移对现有脚本完全透明。
2. **清理 credentials 中的同名静态段**，并逐个确认每个 profile 拿到的都是临时凭证。
3. **在 IAM 中真正停用并删除旧密钥**，而不只是删掉本地文件。

做完这三点，工程师每天在浏览器里登录一次，其余用法与迁移前完全一样。工作站上运行的工具越来越多，AI 编码助手也越来越能干，在这样的环境里，让电脑里"无钥可偷"，是投入最小、收益最直接的安全加固之一。更好的是，这件事本身也可以交给 AI 编码助手来做：给 Kiro 粘贴一段提示词，你只负责确认和登录。

**不妨就从现在开始：打开终端，执行开头那条命令。如果结果不是 0，就打开 Kiro，把上文的提示词粘贴进去。**

---

## 参考资料

1. IAM 用户指南：[Security best practices in IAM](https://docs.aws.amazon.com/IAM/latest/UserGuide/best-practices.html)
2. AWS Well-Architected 框架：[SEC02-BP02 Use temporary credentials](https://docs.aws.amazon.com/wellarchitected/latest/security-pillar/sec_identities_unique.html)
3. IAM 用户指南：[Manage access keys for IAM users](https://docs.aws.amazon.com/IAM/latest/UserGuide/id_credentials_access-keys.html)
4. AWS CLI 用户指南：[Configuring IAM Identity Center authentication with the AWS CLI](https://docs.aws.amazon.com/cli/latest/userguide/cli-configure-sso.html)
5. AWS IAM Identity Center 用户指南：[Set session duration for AWS accounts](https://docs.aws.amazon.com/singlesignon/latest/userguide/howtosessionduration.html)
6. AWS IAM Identity Center 用户指南：[Configure the session duration in IAM Identity Center](https://docs.aws.amazon.com/singlesignon/latest/userguide/configure-user-session.html)
7. [IAM Identity Center in Amazon Web Services China](https://docs.amazonaws.cn/en_us/aws/latest/userguide/iam-identity-center.html)
8. IAM 用户指南：[Generate credential reports for your AWS account](https://docs.aws.amazon.com/IAM/latest/UserGuide/id_credentials_getting-report.html)
9. IAM 用户指南：[IAM Access Analyzer findings](https://docs.aws.amazon.com/IAM/latest/UserGuide/access-analyzer-findings.html)
10. AWS CLI 命令参考：[aws sso logout](https://docs.aws.amazon.com/cli/latest/reference/sso/logout.html)
11. AWS Organizations 用户指南：[Service control policies (SCPs)](https://docs.aws.amazon.com/organizations/latest/userguide/orgs_manage_policies_scps.html)
12. [What is IAM Roles Anywhere?](https://docs.aws.amazon.com/rolesanywhere/latest/userguide/introduction.html)
13. AWS Security Blog：[Beyond IAM access keys: Modern authentication approaches for AWS](https://aws.amazon.com/blogs/security/beyond-iam-access-keys-modern-authentication-approaches-for-aws/)
14. Kiro 文档：[Kiro CLI](https://kiro.dev/docs/cli/)

*亚马逊云科技中国区域的相关云服务由西云数据和光环新网运营，具体信息以中国区域官网为准。*

---

## License

本文内容与示例代码以 [MIT-0](LICENSE) 许可发布，可自由使用、修改和分发。
