#!/usr/bin/env bash
set -euo pipefail

if [[ "$#" -lt 2 ]]; then
    echo "Usage: $0 EIC_SHELL COMMAND [ARGUMENT ...]" >&2
    exit 2
fi

EIC_SHELL=$1
shift

if [[ ! -x "$EIC_SHELL" ]]; then
    echo "eic-shell not found or not executable: $EIC_SHELL" >&2
    exit 2
fi

# The container-side eic-shell accepts a command reliably through stdin.
# Quote every argv element as a single-quoted shell word.  Use the
# '"'"' spelling for an embedded apostrophe so the generated command line
# contains no escape backslashes for eic-shell's `read` loop to consume.
quote_word() {
    local value=$1
    local apostrophe="'\"'\"'"
    value=${value//\'/$apostrophe}
    printf "'%s'" "$value"
}

emit_command() {
    local first=1
    local value
    for value in "$@"; do
        if (( first )); then
            first=0
        else
            printf ' '
        fi
        quote_word "$value"
    done
    printf '\n'
}

emit_command "$@" | "$EIC_SHELL"
