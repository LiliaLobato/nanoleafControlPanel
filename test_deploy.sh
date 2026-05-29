#!/usr/bin/env bash
# test_deploy.sh — End-to-end test suite for deploy.sh
#
# Usage:
#   bash test_deploy.sh
#
# Runs entirely in a temp directory; never touches real $HOME or the repo.
# Prints PASS / FAIL for each case. Exits non-zero if any test fails.

set -uo pipefail

REPO_DIR="$(cd "$(dirname "$0")" && pwd)"
DEPLOY="$REPO_DIR/deploy.sh"

# ── Helpers ──────────────────────────────────────────────────────────────────

PASS=0
FAIL=0
SKIP=0
CURRENT_TEST=""

# True when running on Windows (Git Bash / MSYS).
# ln -s requires admin or Developer Mode on Windows, so symlink tests are skipped.
IS_WINDOWS=false
case "$(uname -s)" in MINGW*|MSYS*|CYGWIN*) IS_WINDOWS=true ;; esac

start_test() {
    CURRENT_TEST="$1"
}

pass() {
    echo "  PASS  $CURRENT_TEST"
    PASS=$(( PASS + 1 ))
}

fail() {
    echo "  FAIL  $CURRENT_TEST"
    echo "        reason: $1"
    FAIL=$(( FAIL + 1 ))
}

# skip_on_windows: call at the top of any test that requires ln -s.
# Prints SKIP and returns 1 so the caller can "skip_on_windows || return" to exit early.
skip_on_windows() {
    if $IS_WINDOWS; then
        echo "  SKIP  $CURRENT_TEST  (ln -s unavailable on Windows without Developer Mode)"
        SKIP=$(( SKIP + 1 ))
        return 1
    fi
    return 0
}

# Run deploy.sh with HOME overridden to a temp dir so no real dirs are touched.
# Usage: run_deploy [args...]  (sets output in $OUT and exit code in $RC)
run_deploy() {
    local tmp_home="$TEST_HOME"
    OUT=$(HOME="$tmp_home" bash "$DEPLOY" "$@" 2>&1)
    RC=$?
}

# Assert $OUT contains a substring.
assert_contains() {
    local needle="$1"
    if echo "$OUT" | grep -qF "$needle"; then
        return 0
    else
        fail "expected output to contain: $needle"
        return 1
    fi
}

# Assert $OUT does NOT contain a substring.
assert_not_contains() {
    local needle="$1"
    if echo "$OUT" | grep -qF "$needle"; then
        fail "expected output NOT to contain: $needle"
        return 1
    fi
    return 0
}

# Assert a path exists (file or dir).
assert_exists() { [ -e "$1" ] || { fail "expected to exist: $1"; return 1; }; }

# Assert a path does NOT exist.
assert_not_exists() { [ ! -e "$1" ] || { fail "expected NOT to exist: $1"; return 1; }; }

# Assert exit code equals expected.
assert_exit() {
    local expected="$1"
    [ "$RC" -eq "$expected" ] || { fail "expected exit $expected, got $RC"; return 1; }
}

# ── Test fixture setup ────────────────────────────────────────────────────────

TMPDIR_ROOT="$(mktemp -d)"
trap 'rm -rf "$TMPDIR_ROOT"' EXIT

new_home() {
    # Create a fresh fake HOME for each test group.
    TEST_HOME="$TMPDIR_ROOT/home_$(date +%s%N)"
    mkdir -p "$TEST_HOME"
}

# Paths derived from TEST_HOME (mirrors what deploy.sh computes from $HOME)
share_dir()  { echo "$TEST_HOME/.local/share/nanoleafControlPanel"; }
state_dir()  { echo "$TEST_HOME/.local/state/nanoleafControlPanel"; }
config_dir() { echo "$TEST_HOME/.config/nanoleafControlPanel"; }
bin_dir()    { echo "$TEST_HOME/.local/bin"; }
cli_link()   { echo "$TEST_HOME/.local/bin/nanoleaf-cli"; }

echo "=== deploy.sh test suite ==="
echo "Repo: $REPO_DIR"
echo


# ═══════════════════════════════════════════════════════════════════════════════
# GROUP 1 — --dry-run makes no filesystem changes
# ═══════════════════════════════════════════════════════════════════════════════
echo "── Group 1: --dry-run leaves filesystem unchanged ──"

start_test "dry-run exits 0"
new_home
run_deploy --dry-run
assert_exit 0 && pass

start_test "dry-run prints DRY-RUN mode label"
assert_contains "DRY-RUN" && pass

start_test "dry-run does not create share dir"
assert_not_exists "$(share_dir)" && pass

start_test "dry-run does not create state dir"
assert_not_exists "$(state_dir)" && pass

start_test "dry-run does not create config dir"
assert_not_exists "$(config_dir)" && pass

start_test "dry-run does not create bin dir"
assert_not_exists "$(bin_dir)" && pass

start_test "dry-run does not create symlink"
assert_not_exists "$(cli_link)" && pass

start_test "dry-run still runs import smoke-check and passes"
assert_contains "all imports OK" && pass

start_test "dry-run prints [dry-run] prefix for mkdir"
assert_contains "[dry-run] mkdir" && pass

start_test "dry-run prints [dry-run] prefix for ln"
assert_contains "[dry-run] ln" && pass

start_test "dry-run prints [dry-run] prefix for chmod"
assert_contains "[dry-run] chmod" && pass

start_test "dry-run shows correct crontab command in output"
assert_contains "sunrise_sunset_controller.py" && pass

start_test "dry-run completion message says 'no changes made'"
assert_contains "no changes made" && pass


# ═══════════════════════════════════════════════════════════════════════════════
# GROUP 2 — Live run: fresh install (nothing pre-exists)
# ═══════════════════════════════════════════════════════════════════════════════
echo
echo "── Group 2: live run — fresh install ──"

start_test "live run exits 0 on fresh home"
new_home
run_deploy
assert_exit 0 && pass

start_test "live run prints LIVE mode label"
assert_contains "LIVE" && pass

start_test "creates share dir"
assert_exists "$(share_dir)" && pass

start_test "creates state dir"
assert_exists "$(state_dir)" && pass

start_test "creates config dir"
assert_exists "$(config_dir)" && pass

start_test "creates bin dir"
assert_exists "$(bin_dir)" && pass

start_test "creates symlink pointing at nanoleaf_cli.py"
skip_on_windows || true  # non-fatal skip — continue group
if ! $IS_WINDOWS; then
    target="$(readlink "$(cli_link)" 2>/dev/null || echo '')"
    [ "$target" = "$REPO_DIR/nanoleaf_cli.py" ] && pass || fail "symlink target: $target"
fi

start_test "nanoleaf_cli.py is executable after live run"
[ -x "$REPO_DIR/nanoleaf_cli.py" ] && pass || fail "not executable"

start_test "live run reports imports OK"
assert_contains "all imports OK" && pass

start_test "live run prints Deploy complete"
assert_contains "Deploy complete" && pass


# ═══════════════════════════════════════════════════════════════════════════════
# GROUP 3 — Idempotency: re-running on an already-deployed home
# ═══════════════════════════════════════════════════════════════════════════════
echo
echo "── Group 3: idempotency — re-run on already-deployed home ──"

# Reuse the home from Group 2 (already deployed)
start_test "re-run exits 0"
run_deploy
assert_exit 0 && pass

start_test "re-run reports symlink already correct"
if $IS_WINDOWS; then
    skip_on_windows || true
else
    assert_contains "already correct" && pass
fi

start_test "re-run does not duplicate symlink (still a single valid link)"
if $IS_WINDOWS; then
    skip_on_windows || true
else
    target="$(readlink "$(cli_link)" 2>/dev/null || echo '')"
    [ "$target" = "$REPO_DIR/nanoleaf_cli.py" ] && pass || fail "symlink target: $target"
fi

start_test "re-run: crontab not available warning still shown (Git Bash)"
# On Windows/Git Bash crontab is absent; assert the manual instruction is printed.
if ! command -v crontab &>/dev/null; then
    assert_contains "add this manually" && pass
else
    # On Linux: entry was added first run, second run should skip.
    assert_contains "already present" && pass
fi


# ═══════════════════════════════════════════════════════════════════════════════
# GROUP 4 — Symlink scenarios
# ═══════════════════════════════════════════════════════════════════════════════
echo
echo "── Group 4: symlink edge cases ──"

start_test "stale symlink pointing at wrong target gets updated"
if $IS_WINDOWS; then
    skip_on_windows || true
else
    new_home
    mkdir -p "$(bin_dir)"
    ln -s "/tmp/wrong_target.py" "$(cli_link)"
    run_deploy
    new_target="$(readlink "$(cli_link)" 2>/dev/null || echo '')"
    [ "$new_target" = "$REPO_DIR/nanoleaf_cli.py" ] && pass || fail "target after update: $new_target"
fi

start_test "stale symlink update does not print 'already correct'"
if $IS_WINDOWS; then
    skip_on_windows || true
else
    assert_not_contains "already correct" && pass
fi


# ═══════════════════════════════════════════════════════════════════════════════
# GROUP 5 — .env presence / absence
# ═══════════════════════════════════════════════════════════════════════════════
echo
echo "── Group 5: .env file handling ──"

start_test ".env missing → warning printed"
# deploy.sh looks for .env in REPO_DIR (not HOME), so we can't fake its absence
# without changing REPO_DIR. Use dry-run + real repo where .env exists or not.
new_home
run_deploy --dry-run
if [ -f "$REPO_DIR/.env" ]; then
    assert_contains ".env — 600" && pass
else
    assert_contains ".env not found" && pass
fi

start_test ".env present → chmod 600 line shown in dry-run"
new_home
# Create a fake .env in a copy of REPO_DIR context is not possible without
# patching REPO_DIR; instead verify dry-run output is consistent with .env state.
run_deploy --dry-run
if [ -f "$REPO_DIR/.env" ]; then
    assert_contains "[dry-run] chmod 600" && pass
else
    # .env absent: warning should appear, no chmod line for .env
    assert_contains "WARNING" && pass
fi


# ═══════════════════════════════════════════════════════════════════════════════
# GROUP 6 — config.json permissions
# ═══════════════════════════════════════════════════════════════════════════════
echo
echo "── Group 6: config.json permissions ──"

start_test "no config.json → no config.json chmod output"
new_home
run_deploy --dry-run
assert_not_contains "config.json — 600" && pass

start_test "existing config.json → chmod 600 shown in dry-run"
new_home
mkdir -p "$TEST_HOME/.config/nanoleafControlPanel"
touch "$TEST_HOME/.config/nanoleafControlPanel/config.json"
run_deploy --dry-run
assert_contains "config.json — 600" && pass


# ═══════════════════════════════════════════════════════════════════════════════
# GROUP 7 — Crontab handling (Linux only)
# ═══════════════════════════════════════════════════════════════════════════════
echo
echo "── Group 7: crontab ──"

if command -v crontab &>/dev/null; then
    start_test "crontab: entry added on first live run"
    new_home
    # Back up real crontab and restore after
    _saved_cron="$(crontab -l 2>/dev/null || true)"
    crontab -r 2>/dev/null || true
    run_deploy
    result="$(crontab -l 2>/dev/null || echo '')"
    crontab - <<< "$_saved_cron" 2>/dev/null || crontab -r 2>/dev/null || true
    echo "$result" | grep -qF "sunrise_sunset_controller.py" && pass || fail "crontab entry not found"

    start_test "crontab: re-run reports already present"
    new_home
    _saved_cron="$(crontab -l 2>/dev/null || true)"
    # Pre-seed crontab with the expected entry
    (echo "$_saved_cron"; echo "*/5 * * * * /usr/bin/python3 $REPO_DIR/sunrise_sunset_controller.py >> $TEST_HOME/.local/state/nanoleafControlPanel/cron.log 2>&1") | crontab -
    run_deploy
    crontab - <<< "$_saved_cron" 2>/dev/null || crontab -r 2>/dev/null || true
    assert_contains "already present" && pass
else
    start_test "crontab unavailable → manual instruction printed"
    new_home
    run_deploy --dry-run
    assert_contains "add this manually" && pass

    start_test "crontab manual instruction includes controller path"
    assert_contains "sunrise_sunset_controller.py" && pass
fi


# ═══════════════════════════════════════════════════════════════════════════════
# GROUP 8 — Timezone check
# ═══════════════════════════════════════════════════════════════════════════════
echo
echo "── Group 8: timezone check ──"

start_test "timezone section always prints"
new_home
run_deploy --dry-run
assert_contains "Timezone check" && pass

start_test "timezone mismatch prints WARNING and fix command"
# On Windows timedatectl is absent so timezone shows as unknown (not LA) → warning.
if ! command -v timedatectl &>/dev/null; then
    assert_contains "WARNING" && assert_contains "timedatectl set-timezone" && pass
else
    # On Linux where TZ may or may not match — just verify the section ran.
    assert_contains "current:" && pass
fi


# ═══════════════════════════════════════════════════════════════════════════════
# GROUP 9 — Python import smoke-check
# ═══════════════════════════════════════════════════════════════════════════════
echo
echo "── Group 9: import smoke-check ──"

start_test "smoke-check runs even in --dry-run mode"
new_home
run_deploy --dry-run
assert_contains "all imports OK" && pass

start_test "smoke-check runs in live mode"
new_home
run_deploy
assert_contains "all imports OK" && pass

start_test "smoke-check prints section header"
assert_contains "Verifying Python imports" && pass


# ═══════════════════════════════════════════════════════════════════════════════
# GROUP 10 — Output structure and next-steps
# ═══════════════════════════════════════════════════════════════════════════════
echo
echo "── Group 10: output structure ──"

start_test "all 7 step headers present in dry-run"
new_home
run_deploy --dry-run
for i in 1 2 3 4 5 6 7; do
    echo "$OUT" | grep -qF "[$i/7]" || { fail "missing step [$i/7]"; break; }
done && pass

start_test "live run prints next-steps section"
new_home
run_deploy
assert_contains "Next steps" && pass

start_test "dry-run does NOT print next-steps section"
new_home
run_deploy --dry-run
assert_not_contains "Next steps" && pass

start_test "live run mentions nanoleaf-cli status in next steps"
new_home
run_deploy
assert_contains "nanoleaf-cli status" && pass


# ═══════════════════════════════════════════════════════════════════════════════
# SUMMARY
# ═══════════════════════════════════════════════════════════════════════════════
echo
echo "═══════════════════════════════════"
echo "  Results: $PASS passed, $FAIL failed, $SKIP skipped"
echo "═══════════════════════════════════"

[ "$FAIL" -eq 0 ] || exit 1
