# 示例脚本

正文中各步骤用到的示例，整理成了独立文件，方便直接下载使用。每个文件的用法见 [README](../README.md) 中对应的步骤。

| 文件 | 对应步骤 | 作用 |
|---|---|---|
| `sso.fragment.example` | 步骤 3 | SSO 配置片段示例。复制为 `sso.fragment` 后，改成你自己的门户地址、账号 ID 和权限集 |
| `merge_config.py` | 步骤 3 | 把配置片段按段合并进 `~/.aws/config`，支持 `--dry-run` |
| `neutralize_credentials.py` | 步骤 4 | 删除 `~/.aws/credentials` 中与 SSO profile 同名的静态段，支持 `--dry-run` |
| `verify.sh` | 步骤 5 | 逐个 profile 核对账号 ID、SSO 角色和临时凭证（只读）。先修改脚本中的 `CHECKS` |
| `logout-one.sh` | 步骤 7 | 只清除一个 sso-session 的本地令牌缓存（依赖 AWS CLI 缓存实现，升级 CLI 后请复测） |
| `scp-deny-create-access-key.json` | 进阶建议 | 禁止成员账号新建访问密钥的 SCP 示例（含例外角色条件，请先在测试 OU 验证） |
| `kiro-prompt.txt` | 让 Kiro 替你完成迁移 | 粘贴给 Kiro 的提示词，换掉尖括号里的内容即可 |

```bash
cp sso.fragment.example sso.fragment && vi sso.fragment
python3 merge_config.py sso.fragment --dry-run
```

> ⚠️ 修改 `~/.aws` 之前，请先按步骤 2 完整备份。
