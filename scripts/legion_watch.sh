#!/usr/bin/env bash
# Weekly read-only watch for a Legion sale announcement (A1 Phase-0 follow-up).
# Diffs sale-related phrases on legion.cc and the TOS PDF hash against the last
# run; alerts via journal + notify-send when either changes. No writes to the
# repo, no wallets, no capital. See docs/reports/a1-phase0-final-report-2026-07-14.md (#4).
set -euo pipefail

STATE_DIR="${XDG_STATE_HOME:-$HOME/.local/state}/crypto-agent-legion-watch"
mkdir -p "$STATE_DIR"

HOME_URL="https://legion.cc/"
TOS_URL="https://legion.cc/documents/Launchpad_Terms_of_Service.pdf"
KEYWORDS='(live|upcoming|active|open)[^<>]{0,60}(token sale|sale|round|launch)|(sale|round)[^<>]{0,40}(is live|now open|starts)'

now="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
html="$(curl -sL --max-time 30 "$HOME_URL")" || { echo "[$now] fetch failed: $HOME_URL"; exit 1; }
phrases="$(grep -o -i -E "$KEYWORDS" <<<"$html" | tr '[:upper:]' '[:lower:]' | sort -u)"
tos_hash="$(curl -sL --max-time 30 "$TOS_URL" | sha256sum | cut -d' ' -f1)"

prev_phrases_file="$STATE_DIR/phrases.txt"
prev_tos_file="$STATE_DIR/tos.sha256"
changed=""

if [[ -f "$prev_phrases_file" ]] && ! diff -q "$prev_phrases_file" <(printf '%s\n' "$phrases") >/dev/null; then
    changed="homepage sale-phrases changed"
fi
if [[ -f "$prev_tos_file" ]] && [[ "$(cat "$prev_tos_file")" != "$tos_hash" ]]; then
    changed="${changed:+$changed; }TOS PDF hash changed"
fi

printf '%s\n' "$phrases" > "$prev_phrases_file"
printf '%s\n' "$tos_hash" > "$prev_tos_file"
echo "[$now] phrases=$(wc -l <<<"$phrases") tos=$tos_hash ${changed:-no change}" >> "$STATE_DIR/watch.log"

if [[ -n "$changed" ]]; then
    msg="LEGION WATCH: $changed — manually check app.legion.cc for a sale round"
    echo "$msg"
    command -v notify-send >/dev/null && notify-send -u critical "Legion watch" "$msg" || true
else
    echo "[$now] no change"
fi
