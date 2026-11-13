#!/usr/bin/env bash
# PreToolUse on Grep, Glob and Bash. In a project with .ragnar/config.toml, the first local code search
# of a session gets a one-line reminder of the Ragnar tools. It never blocks or changes the call.
# Plain bash and sed: the hook must not depend on jq or Python.
root="${CLAUDE_PROJECT_DIR:-$PWD}"
[ -f "$root/.ragnar/config.toml" ] || exit 0
input="$(cat)"
field() { printf '%s' "$input" | sed -n "s/.*\"$1\"[[:space:]]*:[[:space:]]*\"\([^\"]*\)\".*/\1/p" | head -n1; }

tool="$(field tool_name)"
if [ "$tool" = "Bash" ]; then
  # only commands that search code: grep, rg, git grep, find, ag, ack, at the start or after && ; |
  # ([^"\\]|\\.)* walks over escaped quotes inside the JSON string, as in: cd \"src\" && grep -rn foo .
  printf '%s' "$input" | grep -Eq '"command"[[:space:]]*:[[:space:]]*"(([^"\\]|\\.)*(&&|;|\|)[[:space:]]*)?(rtk[[:space:]]+)?(grep|egrep|rg|git[[:space:]]+grep|find|ag|ack)[[:space:]]' || exit 0
fi

# once per session; without a session id there is nothing to key the marker on, so the hint shows every time
session="$(field session_id | tr -cd 'A-Za-z0-9_-')"
if [ -n "$session" ]; then
  marker="${TMPDIR:-${TEMP:-/tmp}}/ragnar-search-hint-${session}"
  [ -e "$marker" ] && exit 0
  : > "$marker" 2>/dev/null
fi

printf '%s' '{"hookSpecificOutput":{"hookEventName":"PreToolUse","additionalContext":"Ragnar: this project is indexed. If this search looks for a concept or behavior, not an exact string, also call search_code with the question in plain words before you answer: it finds code that shares no words with the pattern and code outside the paths you filtered. For a known name, use find_symbol or find_references. Keep grep for exact strings and for files changed since the indexed commit. This reminder shows once per session."}}'
exit 0
