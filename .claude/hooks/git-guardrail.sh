#!/bin/bash
# PreToolUse guardrail: force a confirmation prompt for destructive git
# commands run via the Bash tool. A permissionDecision of "ask" forces a
# prompt regardless of permission mode, including auto/bypassPermissions,
# per https://code.claude.com/docs/en/hooks#pretooluse-decision-control
set -euo pipefail

input=$(cat)
tool_name=$(echo "$input" | jq -r '.tool_name // empty')

[ "$tool_name" = "Bash" ] || exit 0

command=$(echo "$input" | jq -r '.tool_input.command // empty')
[ -z "$command" ] && exit 0

patterns=(
  'git +push.*(--force|--force-with-lease|-f( |$))'
  'git +push.*--delete'
  'git +reset.*--hard'
  'git +clean.*-[a-zA-Z]*f'
  'git +branch.*-D'
  'git +checkout.* -- '
  'git +stash.*(drop|clear)'
  'git +filter-branch'
  'git +update-ref.*-d'
  'git +tag.*-d'
  'git +gc.*--prune=now'
)

labels=(
  "force-push (rewrites remote history)"
  "remote branch/tag deletion"
  "hard reset (discards uncommitted work)"
  "clean -f (deletes untracked files)"
  "force branch delete"
  "discard working-tree changes"
  "stash drop/clear (loses stashed work)"
  "filter-branch (rewrites history)"
  "ref deletion"
  "tag deletion"
  "aggressive gc (prunes unreachable objects immediately)"
)

for i in "${!patterns[@]}"; do
  if echo "$command" | grep -Eq "${patterns[$i]}"; then
    jq -n --arg cmd "$command" --arg label "${labels[$i]}" \
      '{hookSpecificOutput: {hookEventName: "PreToolUse", permissionDecision: "ask", permissionDecisionReason: ("Destructive git command (" + $label + "): " + $cmd)}}'
    exit 0
  fi
done

# Strip Claude/Anthropic attribution from git commit messages. This repo's
# convention is to never list Claude as (co-)author, so rewrite the command
# silently rather than asking each time.
if echo "$command" | grep -Eq 'git +commit\b'; then
  lower=$(printf '%s' "$command" | tr '[:upper:]' '[:lower:]')

  if [[ "$lower" == *claude* || "$lower" == *anthropic* ]]; then
    if echo "$command" | grep -Eiq -- '--author[= ].*(claude|anthropic)'; then
      jq -n --arg cmd "$command" \
        '{hookSpecificOutput: {hookEventName: "PreToolUse", permissionDecision: "deny", permissionDecisionReason: "Commit --author references Claude/Anthropic. Remove it and retry — this repo does not credit Claude as an author."}}'
      exit 0
    fi

    filtered=$(printf '%s\n' "$command" | grep -viE '^[[:space:]]*(co-authored-by|author):.*(claude|anthropic)|generated (with|by).*claude')

    if [ "$filtered" != "$command" ]; then
      jq -n --arg cmd "$filtered" \
        '{hookSpecificOutput: {hookEventName: "PreToolUse", permissionDecision: "allow", updatedInput: {command: $cmd}, additionalContext: "Stripped a Claude/Anthropic attribution line from the commit message per repo convention."}}'
      exit 0
    fi
  fi
fi

exit 0
