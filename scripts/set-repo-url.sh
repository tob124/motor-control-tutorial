#!/usr/bin/env bash
# 把仓库地址从占位值改成你的真实 GitHub 地址。
#
# 为什么需要：安装脚本、deploy.sh、文档里各有一份仓库地址。
# 手工改容易漏，漏掉的地方会让别人下载到不存在的仓库。
#
# 用法：
#     bash scripts/set-repo-url.sh https://github.com/你的用户名/你的仓库
#     bash scripts/set-repo-url.sh --show          # 只看当前值
#
# 作者：Connor He 和 Astra

set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."

PLACEHOLDER_OWNER_REPO='connor-astra/robocon-control-course'
PLACEHOLDER_URL="https://github.com/$PLACEHOLDER_OWNER_REPO"

# 只取"所有者/仓库名"这一段，不带 .git 后缀。
# 早期版本连 .git 一起匹配，结果只能改到写了 .git 的那一处，
# 其余文件里的裸地址被漏掉——发布时就会有人下载到不存在的仓库。
current_owner_repo() {
  grep -oE 'github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+' install.sh \
    | head -1 | sed 's|^github\.com/||; s|\.git$||'
}

if [ "${1:-}" = "--show" ] || [ $# -eq 0 ]; then
  echo "当前仓库地址：https://github.com/$(current_owner_repo)"
  echo "涉及文件："
  grep -rl "$(current_owner_repo)" install.sh deploy.sh README.md docs/ start-windows.bat 2>/dev/null \
    | sed 's/^/  /' || true
  if [ $# -eq 0 ]; then
    echo
    echo "用法：bash scripts/set-repo-url.sh https://github.com/你的用户名/你的仓库"
  fi
  exit 0
fi

NEW_URL="${1%/}"
NEW_URL="${NEW_URL%.git}"
case "$NEW_URL" in
  https://github.com/*/*) ;;
  *) echo "地址看起来不对：$NEW_URL" >&2
     echo "应形如 https://github.com/用户名/仓库名" >&2
     exit 2 ;;
esac

NEW_OWNER_REPO="${NEW_URL#https://github.com/}"
OLD_OWNER_REPO="$(current_owner_repo)"
OLD_URL="https://github.com/$OLD_OWNER_REPO"

echo "把仓库地址改为：$NEW_URL"
echo "  （原来：$OLD_URL）"
echo

files=(install.sh deploy.sh README.md docs/QUICKSTART.md docs/TEACHER.md start-windows.bat)
changed=0
for file in "${files[@]}"; do
  [ -f "$file" ] || continue
  if grep -q "$OLD_OWNER_REPO" "$file"; then
    # 用不同分隔符，避免地址里的斜杠冲突
    sed -i.bak "s|$OLD_OWNER_REPO|$NEW_OWNER_REPO|g" "$file"
    rm -f "$file.bak"
    echo "  ✓ $file"
    changed=$((changed + 1))
  fi
done

# 逐个核对没有残留。
# 注意不能直接用 grep -l "$OLD_OWNER_REPO"：新地址里可能包含旧地址
# （例如 example-user/robocon-control-course → example-user/x 时不会，
#  但 connor-astra/robocon-control-course → connor-astra/robocon-control-course-app 会），
# 所以用"仓库名后面不能再跟额外路径字符"的边界匹配。
leftover="$(grep -rlE "$(printf '%s' "$OLD_OWNER_REPO" | sed 's/[.[\*^$()+?{|]/\\&/g')([^A-Za-z0-9_.-]|$)" \
  install.sh deploy.sh README.md docs/ start-windows.bat 2>/dev/null || true)"
if [ -n "$leftover" ]; then
  echo
  echo "还有文件残留旧地址，请检查：" >&2
  echo "$leftover" | sed 's/^/  /' >&2
  exit 1
fi

echo
echo "已更新 $changed 个文件。现在检查一遍："
grep -rn "$NEW_OWNER_REPO" install.sh deploy.sh docs/QUICKSTART.md | sed 's/^/  /' | head -8
echo
echo "别忘了：把这份代码推送到 $NEW_URL ，别人才能克隆到。"
