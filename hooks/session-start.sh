#!/usr/bin/env bash
# Keeps Ragnar's local folder out of git in projects that opted in with a .ragnar/config.toml.
# Stays silent and touches nothing in any other project. The block matches ragnar.project.ensure_gitignore.
root="${CLAUDE_PROJECT_DIR:-$PWD}"
[ -f "$root/.ragnar/config.toml" ] || exit 0
git -C "$root" rev-parse --git-dir >/dev/null 2>&1 || exit 0
ignore="$root/.gitignore"
if [ -f "$ignore" ]; then
  grep -qF '# >>> ragnar >>>' "$ignore" && exit 0
  [ -z "$(tail -c1 "$ignore")" ] || echo >> "$ignore"
  echo >> "$ignore"
fi
cat >> "$ignore" <<'EOF'
# >>> ragnar >>>
.rag/
# <<< ragnar <<<
EOF
exit 0
