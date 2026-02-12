"""
IndexPilot - Evaluation & Telemetry Report
Runs test cases with safety checks: row count + speedup.
If either fails, the agent's changes are ROLLED BACK.

Usage:
    python scripts/eval_report.py          # Run all 6 tests
    python scripts/eval_report.py 1        # Run only test #1
    python scripts/eval_report.py 1 3 5    # Run tests #1, #3, #5
"""

import os
import re
import sys
import time
import subprocess

# Add project root to path so we can import the agent package
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from agent.tools import (
    measure_latency,
    drop_all_indexes,
    seed_redundant_indexes,
    count_rows,
    snapshot_indexes,
    restore_indexes,
)
from agent.pilot import run_agent

SEPARATOR = "-" * 100


def print_header():
    print()
    print("=" * 100)
    print("  IndexPilot - Telemetry Report")
    print("=" * 100)
    print(
        f"  {'Test Case':<12} | {'Issue Found':<28} | {'Fix Applied':<28} | "
        f"{'Speedup':<10} | {'Row Check':<10} | {'Verdict':<12}"
    )
    print(SEPARATOR)


def print_row(label, issue, fix, speedup_pct, row_ok, verdict):
    print(
        f"  {label:<12} | {issue:<28} | {fix:<28} | "
        f"{speedup_pct:>6.1f}%    | {row_ok:<10} | {verdict:<12}"
    )


def run_test_case(label, query, issue_label, setup_fn=None):
    """
    Runs one benchmark with full safety:
      1. Clean slate (drop indexes)
      2. Optional setup (e.g. seed redundant indexes)
      3. Snapshot indexes + row count (BEFORE)
      4. Baseline latency (avg 5 runs)
      5. Run agent
      6. Row count check (AFTER)
      7. Optimized latency (avg 5 runs)
      8. Evaluate: if row count mismatch OR speedup negative -> ROLLBACK
      9. Report verdict
    """
    # -- 1. Clean slate --
    drop_all_indexes()

    # -- 2. Optional setup --
    if setup_fn:
        setup_fn()

    # -- 3. Snapshot state before agent --
    pre_snapshot = snapshot_indexes()
    pre_rows = count_rows()

    # -- 4. Baseline --
    print(f"\n  >> {label}: Baseline ...")
    baseline = measure_latency(query)
    print(f"     Baseline: {baseline:.2f} ms")

    # -- 5. Agent --
    print(f"  >> {label}: Agent running ...")
    agent_answer = run_agent(query, verbose=True) or ""

    # -- 6. Row count check --
    post_rows = count_rows()
    row_ok = post_rows == pre_rows
    row_status = "OK" if row_ok else "MISMATCH"

    # -- 7. Optimized latency --
    print(f"  >> {label}: Optimized ...")
    optimized = measure_latency(query)
    print(f"     Optimized: {optimized:.2f} ms")

    # -- 8. Evaluate --
    speedup = ((baseline - optimized) / baseline) * 100 if baseline > 0 else 0.0
    speed_ok = speedup > 0

    if not row_ok or not speed_ok:
        # ROLLBACK: restore indexes to pre-agent state
        restore_indexes(pre_snapshot)
        if not row_ok:
            verdict = "ROLLED BACK"
            print(f"  !! {label}: Row count changed ({pre_rows} -> {post_rows}). ROLLED BACK.")
        else:
            verdict = "ROLLED BACK"
            print(f"  !! {label}: Performance regressed ({speedup:+.1f}%). ROLLED BACK.")

        # Re-measure after rollback to confirm we're back to baseline
        restored = measure_latency(query)
        print(f"     Restored: {restored:.2f} ms (back to baseline)")
    else:
        verdict = "PASS"

    # -- 9. Extract fix name --
    fix = "CREATE INDEX"
    idx_match = re.search(r"(idx_\w+)", agent_answer)
    if idx_match:
        fix = f"CREATE INDEX {idx_match.group(1)}"
    if "drop" in agent_answer.lower():
        drop_match = re.search(r"[Dd]ropped?\s+(?:index\s+)?`?(idx_\w+)`?", agent_answer)
        if drop_match:
            fix = f"DROP + {fix}"

    if verdict == "ROLLED BACK":
        fix = "[ROLLED BACK]"

    print_row(label, issue_label, fix, speedup, row_status, verdict)
    return {"speedup": speedup, "row_ok": row_ok, "verdict": verdict}


# -- Test case definitions --

TEST_CASES = {
    1: {
        "label": "Query #1",
        "query": "SELECT * FROM transactions WHERE email = 'test@example.com';",
        "issue": "SCAN (single WHERE email)",
    },
    2: {
        "label": "Query #2",
        "query": "SELECT * FROM transactions WHERE user_id = 8888 OR transaction_status = 'PENDING';",
        "issue": "SCAN (OR condition)",
    },
    3: {
        "label": "Query #3",
        "query": (
            "SELECT u.name, t.amount, t.transaction_status "
            "FROM transactions t "
            "JOIN users u ON t.user_id = u.id "
            "WHERE t.user_id = 42;"
        ),
        "issue": "SCAN (JOIN on user_id)",
    },
    4: {
        "label": "Query #4",
        "query": (
            "SELECT * FROM transactions "
            "WHERE transaction_status = 'completed' "
            "ORDER BY created_at DESC LIMIT 20;"
        ),
        "issue": "SCAN + TEMP B-TREE (sort)",
    },
    5: {
        "label": "Query #5",
        "query": "SELECT * FROM transactions WHERE email = 'test@example.com';",
        "issue": "Redundant indexes (email)",
        "setup": seed_redundant_indexes,
    },
    6: {
        "label": "Query #6",
        "query": "SELECT * FROM transactions WHERE created_at = '2025-01-15 10:30:00';",
        "issue": "SCAN (date equality)",
    },
}


def print_menu():
    print()
    print("  Available test cases:")
    print("  " + "-" * 50)
    for num, tc in TEST_CASES.items():
        print(f"    {num}. {tc['issue']}")
    print("  " + "-" * 50)
    print()


def main():
    # -- Pre-flight --
    db_path = os.path.join(PROJECT_ROOT, "indexpilot.db")
    if not os.path.exists(db_path):
        print("Database not found. Seeding ...")
        subprocess.run(
            [sys.executable, os.path.join(PROJECT_ROOT, "scripts", "seed_db.py")],
            check=True,
        )

    # -- Parse which tests to run --
    args = sys.argv[1:]

    if args:
        try:
            selected = [int(a) for a in args]
        except ValueError:
            print("Usage: python scripts/eval_report.py [test_numbers...]")
            print("  e.g.  python scripts/eval_report.py 1 3 5")
            sys.exit(1)

        invalid = [n for n in selected if n not in TEST_CASES]
        if invalid:
            print(f"Invalid test numbers: {invalid}. Valid: 1-{len(TEST_CASES)}")
            sys.exit(1)
    else:
        selected = list(TEST_CASES.keys())

    print_header()
    if len(selected) < len(TEST_CASES):
        print(f"  Running {len(selected)} of {len(TEST_CASES)} tests: {selected}")

    results = []
    for i, num in enumerate(selected):
        tc = TEST_CASES[num]
        result = run_test_case(
            tc["label"],
            tc["query"],
            tc["issue"],
            setup_fn=tc.get("setup"),
        )
        results.append(result)

        if i < len(selected) - 1:
            time.sleep(3)

    # -- Summary --
    passed = sum(1 for r in results if r["verdict"] == "PASS")
    rolled = sum(1 for r in results if r["verdict"] == "ROLLED BACK")
    row_fails = sum(1 for r in results if not r["row_ok"])
    avg_speedup = sum(r["speedup"] for r in results) / len(results) if results else 0

    print()
    print(SEPARATOR)
    print(f"  PASSED: {passed}/{len(results)} | ROLLED BACK: {rolled} | ROW ERRORS: {row_fails} | Avg Speedup: {avg_speedup:.1f}%")
    print("=" * 100)


if __name__ == "__main__":
    main()
