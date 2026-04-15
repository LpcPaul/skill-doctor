#!/usr/bin/env bash
#
# Skill Doctor — Case Submission Script
#
# Validates a case report, runs redaction, and submits as a GitHub Issue.
# Requires: gh CLI (authenticated), python3
#
# Usage:
#   bash submit_case.sh <case.json>
#
# Exit codes:
#   0 — Submitted successfully
#   1 — Redaction warnings (submitted after redaction)
#   2 — Blocked by redaction (not submitted)
#   3 — Missing dependencies or invalid input

set -euo pipefail

REPO="LpcPaul/skill-doctor"  # ← Change this to your actual repo
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ── Dependency check ──

if ! command -v gh &>/dev/null; then
    echo "Error: GitHub CLI (gh) is not installed."
    echo "Install it: https://cli.github.com/"
    exit 3
fi

if ! gh auth status &>/dev/null 2>&1; then
    echo "Error: GitHub CLI is not authenticated."
    echo "Run: gh auth login"
    exit 3
fi

if ! command -v python3 &>/dev/null; then
    echo "Error: python3 is not installed."
    exit 3
fi

# ── Input validation ──

if [ $# -lt 1 ]; then
    echo "Usage: bash submit_case.sh <case.json>"
    exit 3
fi

CASE_FILE="$1"
if [ ! -f "$CASE_FILE" ]; then
    echo "Error: File not found: $CASE_FILE"
    exit 3
fi

# ── Step 1: Run redaction ──

echo "🔍 Running redaction check..."

REDACTED_FILE="/tmp/skill_doctor_redacted_$(date +%s).json"
cp "$CASE_FILE" "$REDACTED_FILE"

REDACT_EXIT=0
python3 "$SCRIPT_DIR/redact.py" --input "$REDACTED_FILE" || REDACT_EXIT=$?

if [ $REDACT_EXIT -eq 2 ]; then
    echo ""
    echo "❌ Case blocked by redaction. Too much sensitive content."
    echo "Please review and rewrite the case before submitting."
    rm -f "$REDACTED_FILE"
    exit 2
fi

if [ $REDACT_EXIT -eq 3 ]; then
    echo ""
    echo "❌ Invalid input file."
    rm -f "$REDACTED_FILE"
    exit 3
fi

# ── Step 2: Extract fields for the issue ──

PLATFORM=$(python3 -c "import json; d=json.load(open('$REDACTED_FILE')); print(d.get('platform','unknown'))")
SKILL=$(python3 -c "import json; d=json.load(open('$REDACTED_FILE')); print(d.get('skill_triggered','unknown'))")
FTYPE=$(python3 -c "import json; d=json.load(open('$REDACTED_FILE')); print(d.get('failure_type','unknown'))")
SIGNATURE=$(python3 -c "import json; d=json.load(open('$REDACTED_FILE')); print(d.get('failure_signature',''))")
REMEDY=$(python3 -c "import json; d=json.load(open('$REDACTED_FILE')); print(d.get('remedy',''))")
CONFIDENCE=$(python3 -c "import json; d=json.load(open('$REDACTED_FILE')); print(d.get('confidence','unknown'))")
CASE_ID=$(python3 -c "import json; d=json.load(open('$REDACTED_FILE')); print(d.get('case_id','no-id'))")

# ── Step 3: Build issue body ──

ISSUE_TITLE="[${FTYPE}] ${SKILL} — ${PLATFORM}"

ISSUE_BODY=$(cat <<EOF
## Case Report: ${CASE_ID}

**Platform:** ${PLATFORM}
**Skill Triggered:** ${SKILL}
**Failure Type:** \`${FTYPE}\`
**Confidence:** ${CONFIDENCE}

### Failure Signature

${SIGNATURE}

### Recommended Remedy

${REMEDY}

### Full Case Data

\`\`\`json
$(cat "$REDACTED_FILE")
\`\`\`

---
*Submitted by skill-doctor. This case has passed automated redaction.*
*Please review for any remaining sensitive content before merging into cases/.*
EOF
)

# ── Step 4: Submit ──

echo ""
echo "📤 Submitting case to ${REPO}..."

ISSUE_URL=$(gh issue create \
    --repo "$REPO" \
    --title "$ISSUE_TITLE" \
    --body "$ISSUE_BODY" \
    --label "case-report,unverified,${FTYPE}" \
    2>&1) || {
    echo "Error: Failed to create GitHub issue."
    echo "$ISSUE_URL"
    rm -f "$REDACTED_FILE"
    exit 3
}

echo ""
echo "✅ Case submitted successfully!"
echo "   Issue: ${ISSUE_URL}"
echo "   Case ID: ${CASE_ID}"

# Cleanup
rm -f "$REDACTED_FILE"

if [ $REDACT_EXIT -eq 1 ]; then
    echo ""
    echo "⚠️  Note: Some content was automatically redacted before submission."
    echo "   Please check the issue to verify redaction quality."
    exit 1
fi

exit 0
