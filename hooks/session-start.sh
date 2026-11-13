#!/usr/bin/env bash
# Runs only in projects that opted in with a .ragnar/config.toml; stays silent and touches nothing elsewhere.
# 1. Keeps Ragnar's local folder out of git. The block matches ragnar.project.ensure_gitignore.
# 2. Tells the agent that the project is indexed and which skill picks the search tool.
#    It runs again after /clear and compaction, which drop the earlier reminder.
root="${CLAUDE_PROJECT_DIR:-$PWD}"
[ -f "$root/.ragnar/config.toml" ] || exit 0

add_ignore_block() {
  git -C "$root" rev-parse --git-dir >/dev/null 2>&1 || return 0
  local ignore="$root/.gitignore"
  if [ -f "$ignore" ]; then
    grep -qF '# >>> ragnar >>>' "$ignore" && return 0
    [ -z "$(tail -c1 "$ignore")" ] || echo >> "$ignore"
    echo >> "$ignore"
  fi
  cat >> "$ignore" <<'EOF'
# >>> ragnar >>>
.rag/
# <<< ragnar <<<
EOF
}
add_ignore_block

printf '%s' '{"hookSpecificOutput":{"hookEventName":"SessionStart","additionalContext":"This project is indexed by Ragnar (.ragnar/config.toml). Rules for finding code, on every prompt: 1. A question about where or how something works: call the Ragnar search_code tool first, with the question in plain words, before Grep or Glob. A grep for a concept misses code named differently and code outside the folders you guess. 2. A known name: find_symbol; its callers or uses: find_references; what it calls: call_graph. 3. An exact string, an error message, or a file changed since the indexed commit: Grep. 4. A large file: file_outline, then Read only the lines you need. The ragnar:search skill has the full rules."}}'
exit 0
