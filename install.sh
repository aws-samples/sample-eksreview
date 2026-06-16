#!/usr/bin/env bash
# eksreview — one-time setup script.
# Creates a Python virtual environment, installs dependencies, and prints
# next-step instructions for launching the agent.
#
# Usage:
#   ./install.sh           # set up the agent
#   ./install.sh --dev     # also install dev dependencies (pytest, ruff, ...)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

GREEN="\033[32m"
YELLOW="\033[33m"
RED="\033[31m"
RESET="\033[0m"

# ── 1. Locate a usable Python ────────────────────────────────
PYTHON_BIN=""
for cmd in python3.13 python3.12 python3.11 python3.10 python3 python; do
    if command -v "$cmd" >/dev/null 2>&1; then
        ver=$("$cmd" -c 'import sys; print(f"{sys.version_info[0]}.{sys.version_info[1]}")')
        if "$cmd" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' 2>/dev/null; then
            PYTHON_BIN="$cmd"
            echo -e "${GREEN}✓${RESET} Found Python $ver ($cmd)"
            break
        fi
    fi
done

if [ -z "$PYTHON_BIN" ]; then
    echo -e "${RED}✗${RESET} Python 3.10 or higher is required."
    echo "  Install Python 3.10+ and re-run this script."
    exit 1
fi

# ── 2. Check uv (required for the bundled MCP server) ────────
# Detection only here. If uv is missing we install it into the project
# venv in step 4, because the launcher activates that venv at runtime and
# will always find a venv-local uv (no global PATH or shell reload needed).
if command -v uv >/dev/null 2>&1; then
    echo -e "${GREEN}✓${RESET} Found uv ($(uv --version 2>&1 | head -n 1))"
else
    echo -e "${YELLOW}!${RESET} uv is not on your PATH. It will be installed into the project's virtual environment."
fi

# ── 3. Create or reuse the virtual environment ───────────────
if [ -d ".venv" ]; then
    echo -e "${GREEN}✓${RESET} Reusing existing .venv"
else
    echo "  Creating virtual environment in .venv ..."
    "$PYTHON_BIN" -m venv .venv
    echo -e "${GREEN}✓${RESET} Created .venv"
fi

# ── 4. Install dependencies ──────────────────────────────────
# shellcheck disable=SC1091
source .venv/bin/activate

echo "  Upgrading pip ..."
python -m pip install --upgrade pip --quiet

if [ "${1:-}" = "--dev" ]; then
    echo "  Installing eksreview with dev dependencies ..."
    pip install --quiet -e ".[dev]"
else
    echo "  Installing eksreview ..."
    pip install --quiet -e .
fi

# Ensure uv is available to the agent. The launcher (./eksreview) activates
# this venv, so installing uv here guarantees it is found at runtime,
# regardless of the global PATH or shell configuration.
if ! command -v uv >/dev/null 2>&1; then
    echo "  Installing uv into the virtual environment ..."
    if pip install --quiet uv; then
        echo -e "${GREEN}✓${RESET} Installed uv ($(uv --version 2>&1 | head -n 1))"
    else
        echo -e "${RED}✗${RESET} Could not install uv automatically."
        echo "  Install it manually and re-run this script. For example:"
        echo "    source .venv/bin/activate && pip install uv"
        echo "    # or install it globally: curl -LsSf https://astral.sh/uv/install.sh | sh"
        echo "    # (Homebrew users can run: brew install uv)"
        exit 1
    fi
fi
echo -e "${GREEN}✓${RESET} Dependencies installed"

# ── 5. Quick AWS credential check (non-fatal) ────────────────
if command -v aws >/dev/null 2>&1; then
    if aws sts get-caller-identity --output text --query Arn >/dev/null 2>&1; then
        echo -e "${GREEN}✓${RESET} AWS credentials detected"
    else
        echo -e "${YELLOW}!${RESET} AWS credentials not detected. Run: aws configure"
    fi
fi

# ── 6. Next steps ────────────────────────────────────────────
printf '\n'
printf '  %bSetup complete.%b\n\n' "$GREEN" "$RESET"
printf '  To start eksreview:\n\n'
printf '    %bsource .venv/bin/activate%b\n' "$YELLOW" "$RESET"
printf '    %b./eksreview%b\n\n' "$YELLOW" "$RESET"
printf '  Or in one step:\n\n'
printf '    %b./eksreview%b     (auto-activates the venv)\n\n' "$YELLOW" "$RESET"
printf '  Make sure you have:\n'
printf '    • AWS credentials configured (aws configure)\n'
printf '    • Bedrock model access in your region (Claude Opus / Sonnet)\n'
printf '    • Your IAM identity granted access to the EKS clusters you want to\n'
printf '      review (via an EKS access entry or the aws-auth ConfigMap)\n\n'
