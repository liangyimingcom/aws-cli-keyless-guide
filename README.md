# 让电脑里"无钥可偷"：30 分钟把 AWS CLI 从长期访问密钥迁移到 AWS IAM Identity Center

先做个小实验。打开终端，执行：

```bash
grep -c 'AKIA' ~/.aws/credentials
```

如果结果大于 0，你的电脑里就存着**永不过期的 AWS 钥匙**。它们可能已经跟着你换过两台电脑，被同步过一次网盘，还被好几个 IDE 插件和 AI 编码助手读取过。只要没人到 IAM 里把它们停用，它们就一直有效。

本文分享一次真实的迁移实践：在**不改动任何现有脚本**的前提下，用约 30 分钟把运维工作站上的长期访问密钥全部换成 AWS IAM Identity Center 签发的**临时凭证**，再在 AWS 侧把旧密钥彻底停用并删除。迁移完成后，上面那条命令的结果会变成 0。电脑上即使有程序把凭证文件翻一遍，也找不到一把能长期使用的访问密钥。

> **适合读者**：使用 AWS CLI 管理多个账号的运维和平台工程师，需要了解 IAM 和 AWS CLI profile 的基本概念（Level 200–300）。
> **你将获得**：7 个可直接照做的步骤、每一步的检查点，以及备份、合并配置、清理凭证、验证、单会话退出等关键示例代码。
>
> 🤖 **不想自己敲命令？** 把一段提示词粘贴给 AI 编码助手 **Kiro**，它会读完本文并替你执行迁移，你只需确认和登录。见下文「[让 Kiro 替你完成迁移](#让-kiro-替你完成迁移)」。

> 📁 文中的示例脚本也单独放在 [`examples/`](examples/) 目录里，可以直接下载使用。

---

## 背景：为什么现在是时候了

### 官方建议早已明确

亚马逊云科技在 [IAM 安全最佳实践](https://docs.aws.amazon.com/IAM/latest/UserGuide/best-practices.html) 中建议：**人类用户应通过身份提供商联合访问 AWS，并使用临时凭证**；需要集中管理多账号访问时，推荐使用 AWS IAM Identity Center。[AWS Well-Architected 框架安全支柱 SEC02-BP02](https://docs.aws.amazon.com/wellarchitected/latest/security-pillar/sec_identities_unique.html) 也把"开发者使用 IAM 用户的长期访问密钥，而不是通过联合身份从 CLI 获取临时凭证"列为常见反模式，并把未遵循该最佳实践的风险等级定为**高**。

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
| 有效期 | 直到被手动停用或删除 | 每份临时凭证的有效期由权限集会话时长决定（1–12 小时）；多久需要重新登录，由 Identity Center 的用户会话时长决定 |
| 如何获取 | 复制、粘贴密钥 | 在浏览器登录 IAM Identity Center，可强制 MFA |
| 人员变动时 | 逐个账号查找、删除密钥 | 在 Identity Center 停用该用户，所有账号的访问一并收回 |
| 现有脚本 | — | profile 名称不变，`--profile`、`AWS_PROFILE` 的写法无需修改 |

---

## 选择适合你的方式

要让工作站上不再保存长期密钥，AWS CLI 目前有两种官方方式。两者都是在浏览器中登录，由 CLI 自动获取和刷新临时凭证，区别在于身份由谁管理、管理多少账号：

- **[`aws login`](https://docs.aws.amazon.com/cli/latest/userguide/cli-configure-sign-in.html)**（AWS CLI 2.32.0 及以上）：直接使用登录控制台的身份，即 root 用户、IAM 用户，或通过 IAM 联合的角色。CLI 每 15 分钟自动刷新临时凭证，会话最长 12 小时。
- **IAM Identity Center**（`sso-session` 需要 AWS CLI 2.9.0 及以上）：使用在 Identity Center 中统一管理的工作人员身份，一次登录即可访问所有已分配的账号。

| 维度 | `aws login` | IAM Identity Center |
|---|---|---|
| 登录身份 | 各账号自己的 root 用户、IAM 用户，或通过 IAM 联合（企业 IdP）登录的角色 | 在 Identity Center 中统一管理的工作人员身份 |
| 多账号 | 每个账号（或角色）各配一个 profile，分别登录 | 一个 `sso-session` 登录一次，覆盖所有已分配的账号 |
| MFA | 沿用控制台登录的 MFA 设置（IAM 用户的 MFA，或企业 IdP 的 MFA） | 在 Identity Center 中统一配置，可强制所有用户启用 |
| 人员变动 | IAM 用户：需要到每个账号里分别停用；IAM 联合：在企业 IdP 中停用即可 | 在 Identity Center（或其对接的 IdP）停用一次，所有账号的访问一并收回 |
| 权限管理 | 沿用各账号中 IAM 用户或角色的策略；非 root 身份另需 `SignInLocalDevelopmentAccess` 托管策略 | 权限集集中定义，按账号分配 |
| 前期准备 | 几乎不需要，升级 CLI 即可使用 | 需要管理员先启用 Identity Center，并配置用户和权限集 |
| SDK 与工具兼容 | 需要较新版本的 SDK；不支持的旧工具可通过 `credential_process` 由 CLI 代为提供凭证 | 主流 SDK 和工具（如 Terraform、AWS CDK）普遍支持 SSO profile |
| 本机缓存 | `~/.aws/login/cache/` | `~/.aws/sso/cache/` |

可以根据自己的情况选择：

- **个人账号或账号很少时，用 `aws login` 最快。** 不需要启用新服务，升级 CLI 后执行 `aws login --profile <名称>`，在浏览器中确认即可。适合个人开发者、实验账号，或者团队还没有启用 Identity Center 的过渡阶段。
- **需要集中管理多个账号、多名工程师时，用 IAM Identity Center。** 多账号一次登录，人员变动一处收回，权限集统一管理，MFA 可统一强制。官方文档也说明，已经使用 IAM Identity Center 的，应按 Identity Center 的方式配置 CLI。

> **中国区域**：中国区域的账号无论选用哪一种，都请以 [docs.amazonaws.cn](https://docs.amazonaws.cn/en_us/cli/latest/userguide/cli-chap-authentication.html) 中国区文档为准，并先在自己的账号中实际验证。

本文后续步骤以 IAM Identity Center 为例，演示一台管理多个账号的运维工作站的完整迁移过程。选择 `aws login` 的读者，步骤 2 的备份、步骤 4 的清理和步骤 6 的"在 IAM 中停用并删除旧密钥"同样适用，**尤其是步骤 6，无论选哪一种都不能省**。

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

> ⚠️ **需要清楚的残余风险**：迁移后，本机不再有永不过期的密钥，但 `~/.aws/sso/cache/`（访问令牌和刷新令牌）和 `~/.aws/cli/cache/`（临时凭证）里仍有明文缓存。这些缓存一旦被复制，在会话有效期内仍可使用。因此迁移之后还要做三件事：把权限集和 Identity Center 用户会话时长设得保守一些；开启磁盘加密（如 macOS FileVault）；设备丢失时，由管理员立即结束该用户的活动会话（见步骤 7）。

---

## 前提条件

- macOS 或 Linux 工作站，已安装 [AWS CLI v2](https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html)。`sso-session` 配置需要 2.9.0 及以上版本，建议使用最新版本。
- 目标账号已启用 AWS IAM Identity Center，并且你有权限创建用户、权限集和账号分配。
- Python 3，用于运行文中的示例脚本。macOS 上第一次运行 `python3` 时，如果提示安装 Xcode Command Line Tools，按提示安装即可；也可以用 Homebrew 安装。

```bash
aws --version          # 确认输出为 aws-cli/2.x
which -a aws           # 确认 PATH 中排在第一位的是新版本
```

---

## 让 Kiro 替你完成迁移

本文的命令都可以手动执行。如果不想自己敲，最省事的办法是：**打开 [Kiro](https://kiro.dev/docs/cli/)（IDE 的对话面板或终端里的 `kiro-cli` 都可以），粘贴下面这段提示词。** Kiro 会自己读完这篇文章，按步骤替你执行，你只负责确认和登录。

交给 Kiro 之前，请注意两点：

- **先自己通读一遍本文**。提示词里的链接固定在 `v1.1` 版本，不会随仓库后续改动而变化；不要把 AI 助手指向未经审阅、随时可能变化的远程内容。
- **AI 助手与你是同一个系统用户**。迁移过程中它在技术上能读到 `~/.aws/credentials`。提示词要求它只运行文中的脚本（这些脚本不会输出密钥），但这只是约定，不是隔离。在步骤 6 删除旧密钥之前，不要在这台电脑上安装新的第三方插件。

把尖括号里的内容换成你在步骤 1 中拿到的信息，然后整段粘贴给 Kiro：

```text
请阅读这篇文章：https://raw.githubusercontent.com/liangyimingcom/aws-cli-keyless-guide/v1.1/README.md
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
3. 只运行文中给出的命令和脚本；不要用 cat、less 等命令查看 ~/.aws/credentials 或 ~/.aws/sso/cache，
   不在输出中显示任何密钥或令牌的值，访问密钥 ID 一律打码显示（例如 AKIA****ABCD）。
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
4. **设置会话时长**：权限集的会话时长默认为 1 小时，最长可设为 12 小时（参见 [Set session duration](https://docs.aws.amazon.com/singlesignon/latest/userguide/howtosessionduration.html)）。建议按工作习惯设为 4–8 小时，不要超过实际需要。另外注意区分 Identity Center 的**用户会话时长**（默认 8 小时）：它决定多久需要重新执行 `aws sso login`，在 *Settings → Authentication* 中设置，参见 [Configure the session duration](https://docs.aws.amazon.com/singlesignon/latest/userguide/configure-user-session.html)。
5. **分配账号**：在 *AWS accounts* 中，把用户（或组）和权限集分配到目标账号。

> **中国区域**：亚马逊云科技中国（北京）区域和中国（宁夏）区域都支持 IAM Identity Center。访问门户地址形如 `https://start.<region>.home.awsapps.cn/directory/<别名>`（笔者宁夏区域实例的实际地址即为此格式；请以 Identity Center 控制台 *Settings* 页显示的地址为准），详见 [IAM Identity Center in Amazon Web Services China](https://docs.amazonaws.cn/en_us/aws/latest/userguide/iam-identity-center.html)。

完成后，在访问门户（AWS access portal）的 *Access keys* 页面中记下 **SSO Start URL** 和 **SSO Region**，下一步要用。

### 步骤 2：盘点并完整备份本机凭证

动手之前，先把现状摸清楚，并做一份可以回退的完整备份。

```bash
# 只列出段名，不显示密钥
grep -n '^\[' ~/.aws/config ~/.aws/credentials

# 统计长期密钥数量（只显示前缀，不显示完整密钥 ID）
grep -oE 'AKIA[0-9A-Z]{4}' ~/.aws/credentials | sort | uniq -c

# 完整备份 ~/.aws 和 shell 启动文件：创建时即限制权限，只打包存在的文件，并做完整性自检
TS=$(date +%Y%m%d-%H%M%S)
cd ~ && ( umask 077
  FILES=(.aws); for f in .zshrc .zprofile .zshenv .bash_profile .bashrc .profile; do [ -f "$f" ] && FILES+=("$f"); done
  zip -r -q "aws-backup-$TS.zip" "${FILES[@]}" )
unzip -tq "aws-backup-$TS.zip" && shasum -a 256 "aws-backup-$TS.zip"
```

✅ **检查点**：`unzip -t` 输出 `No errors detected`，记下备份文件的 sha256。

⚠️ 备份包里有旧密钥的明文，以及有效期内的 SSO 令牌。不要把它放进会同步到云盘的目录，并留意 Time Machine 等备份工具也会把它带走。等步骤 6 在 IAM 中删除旧密钥、确认业务无影响后，就可以销毁这个备份包。

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
            name = re.sub(r"\s+", " ", m.group(1).strip())
            secs.append(("default" if name == "profile default" else name, [line]))   # 两种写法等价
        elif secs:
            secs[-1][1].append(line)
        else:
            head.append(line)
    return head, secs

frag_path, dry = sys.argv[1], "--dry-run" in sys.argv
cfg = os.path.realpath(os.path.expanduser("~/.aws/config"))   # 若是指向 dotfiles 的符号链接，写回真实文件
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

> 被整段替换的段，其中的注释也会一起被替换掉；需要保留的注释，请写进配置片段。

### 步骤 4：清理 credentials 中的同名静态段

这是最容易漏掉的一步。在同一个 profile 内，AWS CLI 会先尝试 SSO 配置，再读 `~/.aws/credentials` 里的静态密钥。所以 SSO 配置写对时，SSO 优先生效（SSO 配置缺项时 CLI 会直接报错，不会悄悄改用静态密钥）。真正的陷阱在于：如果某个 profile 在 config 里**根本没有生效的 SSO 配置**，例如片段里的 profile 名称与原来的拼写不一致，或者原来写的是 `[profile default]`、片段里写的是 `[default]` 而没有合并上，CLI 就会**静默使用** credentials 里的同名静态密钥。表现为看起来一切正常，`sts get-caller-identity` 返回的却是 `user/xxx`，而不是 SSO 角色。更重要的是，只要静态段还在，长期密钥就仍然以明文躺在硬盘上。

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

另外检查 shell 启动文件和项目目录里有没有硬编码的密钥环境变量。环境变量的优先级高于所有 profile 配置：

```bash
grep -nE '(export[[:space:]]+)?AWS_(ACCESS_KEY_ID|SECRET_ACCESS_KEY|SESSION_TOKEN|SECURITY_TOKEN)[[:space:]]*=' \
  ~/.zshrc ~/.zprofile ~/.zshenv ~/.zlogin ~/.bash_profile ~/.bashrc ~/.profile \
  ./.envrc ./.env 2>/dev/null | sed -E 's/=.*/=***/'
unset AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY AWS_SESSION_TOKEN AWS_SECURITY_TOKEN
```

`unset` 只对当前终端生效。扫出来的行，需要到对应文件里手动删除，然后重新打开终端。

### 步骤 5：登录，并逐个 profile 验证

每个 `sso-session` 登录一次：

```bash
aws sso login --sso-session main-sso
aws sso login --sso-session china-sso
# 通过 SSH 远程操作、本机没有浏览器时，加 --use-device-code
```

在浏览器中依次完成：登录 → MFA → **Allow access** → 看到 *Request approved*。

> 💡 如果浏览器自动带入了另一个用户的登录会话，可以加上 `--no-browser`，把命令输出的链接复制到**本机**浏览器的无痕窗口中打开。AWS CLI 从 2.22.0 起默认使用 PKCE 授权，这个链接必须在运行 CLI 的同一台设备上打开；需要在另一台设备上完成授权时，请改用 `--use-device-code`。

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
- 显示 `key=AKIA`：说明这个 profile 在 config 中没有生效的 SSO 配置，CLI 用的是 credentials 里的同名静态段。检查步骤 3 片段里的 profile 名称，并完成步骤 4。
- `account` 为空：说明还没有登录，或者该账号没有分配权限集，回到步骤 1 或重新登录。

> ARN 末尾的会话名不一定是登录时输入的用户名，也可能是邮箱，这是正常的。所以脚本只核对账号 ID 和角色前缀，不核对用户名。

### 步骤 6：在 AWS 侧停用并删除旧的长期密钥

**只删除本机文件，并不等于密钥失效。** 密钥的副本可能还留在其他电脑、旧备份或聊天记录里。只有在 IAM 中停用并删除，这把密钥才算真正作废。

**6.1 先在账号层面盘点，并记下"最近使用时间"**

先做这一步，再做 6.2 的有效性核查。原因是 6.2 会用旧密钥调用一次 API，这会刷新该密钥的"最近使用时间"，之后就无法再据此判断它是否还在被别的程序使用。

用刚配置好的 SSO profile 生成 [IAM 凭证报告](https://docs.aws.amazon.com/IAM/latest/UserGuide/id_credentials_getting-report.html)：

```bash
aws iam generate-credential-report --profile prod
# 报告生成需要几秒钟；如果提示 ReportNotPresent 或 ReportInProgress，稍等后再执行下一条
aws iam get-credential-report --profile prod --query Content --output text | base64 --decode \
  | awk -F, 'NR==1 || tolower($9)=="true" || tolower($14)=="true" {print $1","$9","$11","$14","$16}'
# 输出列：用户名, key1 是否启用, key1 最近使用时间, key2 是否启用, key2 最近使用时间

# 对要处理的每把密钥，记下它的最近使用时间和服务
aws iam get-access-key-last-used --access-key-id <ACCESS_KEY_ID> --profile prod
```

盘点时注意四点：

- **凭证报告只覆盖当前账号**。有多个账号时，用对应的 profile 逐个账号执行。
- **凭证报告只包含每个用户的前两把访问密钥**。需要完整核对时，对相关用户执行 `aws iam list-access-keys --user-name <用户名>`。
- **报告中的 `<root_account>` 一行**如果显示有启用的访问密钥，说明 root 用户还有长期密钥。root 密钥不能用 `--user-name` 处理，需要以 root 身份登录控制台删除。
- **迁移后不再有人使用的 IAM 用户**，除了删除访问密钥，还要删除控制台密码（登录配置）和 MFA 设备，或者直接删除该用户。

也可以启用 [IAM Access Analyzer 的未使用访问分析](https://docs.aws.amazon.com/IAM/latest/UserGuide/access-analyzer-findings.html)，持续找出在指定时间窗口内没有被使用过的 IAM 用户访问密钥和密码。这需要单独创建未使用访问分析器，属于付费功能，启用前请先查看计费说明。

**6.2 只读核查本机剩余的密钥是否仍然有效**

```bash
for p in $(grep -oE '^\[[^]]+\]' ~/.aws/credentials | tr -d '[]'); do
  printf '%-25s ' "$p"
  aws sts get-caller-identity --profile="$p" --query Arn --output text 2>&1 | tail -1
done
```

- 返回 ARN：密钥仍然有效，按 6.3 处理。
- 返回 `InvalidClientTokenId`：密钥已失效，可以直接从文件中删除这一段。

> 注意写成 `--profile=NAME`。如果 profile 名称以 `-` 开头，用空格分隔的写法会被 CLI 当成参数来解析。

**6.3 先停用，观察一段时间后再删除**

```bash
aws iam update-access-key --user-name ops-user --access-key-id <ACCESS_KEY_ID> \
  --status Inactive --profile prod
# 观察 1–2 周，确认业务没有受到影响后，再删除
aws iam delete-access-key --user-name ops-user --access-key-id <ACCESS_KEY_ID> --profile prod
```

> 停用（Inactive）随时可以恢复，删除则不可恢复。拿不准用途的密钥，先停用，确认没有影响后再删除。

**6.4 清理本机残留的明文密钥**

除了 `~/.aws`，明文密钥还常常藏在 shell 配置和历史记录、IDE 设置、工具配置、项目里的 `.env` 和 tfvars、旧备份文件以及 AI 编码助手的会话记录里。下面的命令只读扫描常见位置，并对结果打码：

```bash
grep -rIoEn --exclude-dir=node_modules --exclude-dir=.git 'AKIA[0-9A-Z]{16}' \
  ~/.aws ~/.config ~/.kiro ~/.claude \
  ~/.zshrc ~/.zprofile ~/.zshenv ~/.bash_profile ~/.bashrc ~/.profile \
  ~/.zsh_history ~/.bash_history \
  ~/Library/Application\ Support/*/User/settings.json 2>/dev/null \
  | sed -E 's/(AKIA)[0-9A-Z]{12}/\1************/' | sort -u
```

这只是示例，请按自己使用的工具补充路径，并对代码仓库单独扫描（包括 git 历史）。注意这条命令只匹配以 `AKIA` 开头的密钥 ID；与之配对的 40 位私有访问密钥没有固定前缀，用 grep 很难可靠识别，建议配合 [git-secrets](https://github.com/awslabs/git-secrets) 或 trufflehog 这类专门工具。

扫描结果的处理方式：
- 已停用或已删除的密钥：直接删掉这一行。
- 写死在脚本或配置里的密钥：改为 `--profile <名称>` 或 `AWS_PROFILE=<名称>`。
- 要删除的旧备份文件：先确认它已经包含在步骤 2 的备份包里。

### 步骤 7：日常使用与单会话缓存清除

迁移完成后，日常只需要记住三条命令：

```bash
aws sso login --sso-session main-sso     # 每天第一次使用，或看到 "Token has expired" 时
aws s3 ls --profile prod                 # 与迁移前的用法完全相同
aws configure export-credentials --profile prod | grep Expiration   # 查看临时凭证的到期时间（UTC）
```

**关于退出**：根据 [命令参考](https://docs.aws.amazon.com/cli/latest/reference/sso/logout.html)，`aws sso logout` 会清除本机**所有** profile 缓存的 IAM Identity Center 访问令牌，以及通过它们获取的临时凭证。如果你同时登录了全球区域和中国区域，又只想清除其中一个会话的本地缓存，可以使用下面的脚本。

**示例脚本 `logout-one.sh`**：

```bash
#!/usr/bin/env bash
# 只清除一个 sso-session 的本地令牌缓存，其他会话保持登录。用法：./logout-one.sh china-sso
S="${1:?请指定 sso-session 名称}"
# sso-session 的令牌缓存文件名，是该会话名称的 SHA-1 值
TOKEN="$HOME/.aws/sso/cache/$(printf %s "$S" | shasum | cut -d' ' -f1).json"
[ -f "$TOKEN" ] && rm -f "$TOKEN" && echo "已清除 ${S} 的访问令牌缓存"
# 清除所有 SSO 角色的临时凭证缓存；其他会话会在下次调用时用各自的令牌自动重新获取
grep -lE '"ProviderType": ?"sso"' "$HOME"/.aws/cli/cache/*.json 2>/dev/null | xargs rm -f
```

> 这个脚本依赖 AWS CLI 的缓存实现：令牌缓存文件名是会话名称的 SHA-1 值，临时凭证缓存中带有 `"ProviderType": "sso"` 字段。笔者在 AWS CLI 2.39.3 上核对过，这两点都不是官方承诺的接口，升级 CLI 后请复测。
>
> 无论是 `aws sso logout` 还是这个脚本，都只清除本地缓存。如果需要立即终止某个用户的会话（例如设备丢失），管理员可以在 IAM Identity Center 控制台[结束该用户的活动会话](https://docs.aws.amazon.com/singlesignon/latest/userguide/end-active-sessions.html)。需要同时切断已签发的角色临时凭证时，参见 [Revoke active IAM role sessions created by permission sets](https://docs.aws.amazon.com/singlesignon/latest/userguide/revoke-user-permissions.html)。

---

## 迁移效果

| 目标 | 达成情况 | 验证方式 |
|---|---|---|
| 工作站不保存长期密钥 | 托管账号的访问密钥已从 `~/.aws/credentials` 移除，并在 IAM 中停用、删除 | `verify.sh` 全部 PASS；步骤 6.4 扫描范围内结果为空 |
| 凭证自动过期 | 每次调用使用的都是 `ASIA` 临时凭证，按权限集会话时长自动失效 | `aws configure export-credentials` 中的 `Expiration` |
| 本地文件外泄的影响有时限 | 本机只剩有时效的令牌和临时凭证，泄露后的可用窗口受会话时长限制，不再有永不过期的密钥 | 同上；会话时长设置 |
| 登录受 MFA 保护 | 获取凭证必须由本人在浏览器中完成身份验证 | Identity Center 的 MFA 设置 |
| 人员变动一处处理 | 在 Identity Center 停用用户，即可收回该用户在所有账号的访问 | Identity Center → Users |
| 现有工作方式不变 | profile 名称、脚本、Terraform、CDK 都无需修改 | `aws s3 ls --profile <原名称>` |
| 可回退 | 迁移前已完整备份并通过自检 | 备份包及其 sha256 |

在笔者的实际迁移中，一台工作站涉及全球区域和中国区域共 3 个账号、7 个 profile（文中示例做了简化，为 6 个）。从备份到全部验证通过约 30 分钟，原有脚本一行未改。

---

## 进阶建议

- **防止新增长期密钥**：在 AWS Organizations 中，可以用[服务控制策略（SCP）](https://docs.aws.amazon.com/organizations/latest/userguide/orgs_manage_policies_scps.html)拒绝成员账号创建访问密钥，再通过条件为确有需要的角色设置例外。注意三点：SCP 对管理账户中的用户和角色不生效；SCP 只能阻止新建，已有的密钥仍需按步骤 6 处理；请先挂到测试 OU 验证，再逐步推广。

```json
{
  "Version": "2012-10-17",
  "Statement": [{
    "Sid": "DenyCreateAccessKey",
    "Effect": "Deny",
    "Action": "iam:CreateAccessKey",
    "Resource": "*",
    "Condition": {
      "ArnNotLike": {
        "aws:PrincipalArn": "arn:aws:iam::*:role/AccessKeyBreakGlassRole"
      }
    }
  }]
}
```

> 示例中的 `AccessKeyBreakGlassRole` 是例外角色名，请换成你自己的。中国区域的 ARN 前缀为 `arn:aws-cn:`。

- **无人值守任务使用 IAM 角色**：cron、CI/CD 等自动化任务，不适合使用需要交互登录的 SSO。
  - 在 AWS 上运行的任务，请使用 EC2 实例角色、Lambda 执行角色等。
  - 在 AWS 之外运行的任务，可以考虑 [IAM Roles Anywhere](https://docs.aws.amazon.com/rolesanywhere/latest/userguide/introduction.html) 或 OIDC 联合。
  - 更多选择参见 AWS Security Blog 文章 [Beyond IAM access keys](https://aws.amazon.com/blogs/security/beyond-iam-access-keys-modern-authentication-approaches-for-aws/)。
- **监控与告警**：结合 AWS CloudTrail 和 Amazon EventBridge，对 `CreateAccessKey`、未使用 MFA 的控制台登录等高风险事件设置告警；启用 Amazon GuardDuty，及时发现凭证被异常使用的迹象；再配合 AWS Budgets 和 AWS Cost Anomaly Detection，兜底发现异常资源消耗。
- **AI 编码助手的权限**：如何为 AI 编码助手和 AI 代理设计 AWS 访问权限，可延伸阅读 AWS 安全博客《[Secure AI agent access patterns to AWS resources using Model Context Protocol](https://aws.amazon.com/blogs/security/secure-ai-agent-access-patterns-to-aws-resources-using-model-context-protocol/)》。
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
5. AWS CLI 用户指南：[Login for AWS local development using console credentials](https://docs.aws.amazon.com/cli/latest/userguide/cli-configure-sign-in.html)
6. AWS Security Blog：[Simplified developer access to AWS with 'aws login'](https://aws.amazon.com/blogs/security/simplified-developer-access-to-aws-with-aws-login/)
7. AWS IAM Identity Center 用户指南：[Set session duration for AWS accounts](https://docs.aws.amazon.com/singlesignon/latest/userguide/howtosessionduration.html)
8. AWS IAM Identity Center 用户指南：[Configure the session duration in IAM Identity Center](https://docs.aws.amazon.com/singlesignon/latest/userguide/configure-user-session.html)
9. AWS IAM Identity Center 用户指南：[View and end active sessions for your workforce users](https://docs.aws.amazon.com/singlesignon/latest/userguide/end-active-sessions.html)
10. [IAM Identity Center in Amazon Web Services China](https://docs.amazonaws.cn/en_us/aws/latest/userguide/iam-identity-center.html)
11. IAM 用户指南：[Generate credential reports for your AWS account](https://docs.aws.amazon.com/IAM/latest/UserGuide/id_credentials_getting-report.html)
12. IAM 用户指南：[IAM Access Analyzer findings](https://docs.aws.amazon.com/IAM/latest/UserGuide/access-analyzer-findings.html)
13. AWS CLI 命令参考：[aws sso logout](https://docs.aws.amazon.com/cli/latest/reference/sso/logout.html)
14. AWS Organizations 用户指南：[Service control policies (SCPs)](https://docs.aws.amazon.com/organizations/latest/userguide/orgs_manage_policies_scps.html)
15. [What is IAM Roles Anywhere?](https://docs.aws.amazon.com/rolesanywhere/latest/userguide/introduction.html)
16. AWS Security Blog：[Beyond IAM access keys: Modern authentication approaches for AWS](https://aws.amazon.com/blogs/security/beyond-iam-access-keys-modern-authentication-approaches-for-aws/)
17. AWS Security Blog：[Secure AI agent access patterns to AWS resources using Model Context Protocol](https://aws.amazon.com/blogs/security/secure-ai-agent-access-patterns-to-aws-resources-using-model-context-protocol/)
18. Kiro 文档：[Kiro CLI](https://kiro.dev/docs/cli/)
*亚马逊云科技中国区域的相关云服务由西云数据和光环新网运营，具体信息以中国区域官网为准。*

---

## License

本文内容与示例代码以 [MIT-0](LICENSE) 许可发布，可自由使用、修改和分发。
