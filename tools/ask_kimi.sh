#!/usr/bin/env bash
# Ask KIMI a question non-interactively and print only its answer.
#
#   tools/ask_kimi.sh "your prompt"            # answer to stdout
#   tools/ask_kimi.sh "prompt" /some/workdir   # run from a specific directory
#
# Background worth knowing, because the failure modes are non-obvious:
#
#   * KIMI runs as root under `sudo` in an interactive Terminal session. That is
#     why ~/.kimi-code/config.toml and credentials/ were root-owned and
#     unreadable by this user, which made `kimi` report "No model configured".
#     Fixed by asking that root session to run
#     `chown -R andrewpreston:staff ~/.kimi-code`.
#   * The real config carries a MANAGED CLOUD provider (api.kimi.ai, model
#     kimi-for-coding) authenticated over OAuth. The local Ollama models on this
#     machine are NOT usable: Qwen2.5-32B is too slow to emit even a few tokens,
#     and Llama-3.2-11B-Vision is incompatible with the installed Ollama.
#   * KIMI_CODE_HOME must be UNSET; an earlier project-local home pointed KIMI at
#     Ollama, which is exactly the unusable path.
#   * If the root KIMI session writes new state, those files come back
#     root-owned and this breaks again. Re-run the chown above to repair it.
#
# `kimi -p` prints a version banner and a session-resume trailer on stderr; only
# stdout carries the answer, so stderr is discarded.

set -uo pipefail

if [[ $# -lt 1 ]]; then
  echo "usage: $(basename "$0") \"prompt\" [workdir]" >&2
  exit 2
fi

PROMPT="$1"
WORKDIR="${2:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
TIMEOUT_SEC="${ASK_KIMI_TIMEOUT:-900}"

export PATH="$HOME/.kimi-code/bin:$PATH"
export KIMI_CLI_NO_AUTO_UPDATE=1
unset KIMI_CODE_HOME

if [[ ! -r "$HOME/.kimi-code/config.toml" ]]; then
  echo "ERROR: $HOME/.kimi-code/config.toml is not readable." >&2
  echo "Ask the root KIMI session to run:" >&2
  echo "  ! chown -R andrewpreston:staff $HOME/.kimi-code" >&2
  exit 3
fi

mkdir -p "$WORKDIR"
cd "$WORKDIR" || exit 4

kimi -p "$PROMPT" --output-format text 2>/dev/null &
KIMI_PID=$!

# `timeout` is not present on stock macOS, so use a shell watchdog.
(
  sleep "$TIMEOUT_SEC"
  if kill -0 "$KIMI_PID" 2>/dev/null; then
    echo "ask_kimi: no answer after ${TIMEOUT_SEC}s; terminating" >&2
    kill -TERM "$KIMI_PID" 2>/dev/null
  fi
) &
WATCHDOG=$!

wait "$KIMI_PID"
STATUS=$?
kill "$WATCHDOG" 2>/dev/null
wait "$WATCHDOG" 2>/dev/null
exit "$STATUS"
