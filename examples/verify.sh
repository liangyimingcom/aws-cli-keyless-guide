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
