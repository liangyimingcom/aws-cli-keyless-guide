#!/usr/bin/env bash
# 只退出一个 sso-session，其他会话保持登录。用法：./logout-one.sh china-sso
S="${1:?请指定 sso-session 名称}"
# sso-session 的令牌缓存文件名，是该会话名称的 SHA-1 值
TOKEN="$HOME/.aws/sso/cache/$(printf %s "$S" | shasum | cut -d' ' -f1).json"
[ -f "$TOKEN" ] && rm -f "$TOKEN" && echo "已清除 ${S} 的访问令牌缓存"
# 清除 SSO 角色的临时凭证缓存；其他会话会在下次调用时用各自的令牌自动重新获取
grep -lE '"ProviderType": ?"sso"' "$HOME"/.aws/cli/cache/*.json 2>/dev/null | xargs rm -f
