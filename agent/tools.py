"""
IndexPilot - SQL Toolbox
6 tools exposed to the LLM agent via Groq tool calling.
+ utility helpers for benchmarking (not exposed to the LLM).
"""

import os
import sqlite3
import time

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(PROJECT_ROOT, "indexpilot.db")


def get_db_connection():
    return sqlite3.connect(DB_PATH)


# =====================================================================
#  TOOLS EXPOSED TO THE LLM AGENT
# =====================================================================

def get_schema():
    """Returns the CREATE TABLE DDL for all tables in the database."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT name, sql FROM sqlite_master WHERE type='table' ORDER BY name;"
    )
    rows = cursor.fetchall()
    conn.close()
    if not rows:
        return "No tables found."
    parts = []
    for name, sql in rows:
        parts.append(f"-- Table: {name}\n{sql};")
    return "\n\n".join(parts)


def explain_analyze(query: str):
    """
    Runs EXPLAIN QUERY PLAN on a query and measures real execution time.
    Returns the plan + wall-clock duration.
    """
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(f"EXPLAIN QUERY PLAN {query}")
        plan_rows = cursor.fetchall()
        plan_text = "\n".join(str(r) for r in plan_rows)

        start = time.perf_counter()
        cursor.execute(query)
        cursor.fetchall()
        duration_ms = (time.perf_counter() - start) * 1000

        return f"Query Plan:\n{plan_text}\n\nExecution Time: {duration_ms:.3f} ms"
    except Exception as e:
        return f"Error: {e}"
    finally:
        conn.close()


def apply_index(sql: str):
    """Executes a CREATE INDEX statement."""
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(sql)
        conn.commit()
        return f"Success. Executed: {sql}"
    except Exception as e:
        return f"Error: {e}"
    finally:
        conn.close()


def list_indexes():
    """Returns all indexes in the database (name, table, SQL)."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT name, tbl_name, sql FROM sqlite_master "
        "WHERE type='index' ORDER BY tbl_name, name;"
    )
    rows = cursor.fetchall()
    conn.close()
    if not rows:
        return "No indexes found (besides autoindex / primary keys)."
    parts = []
    for name, tbl, sql in rows:
        sql_str = sql if sql else "(auto-created by PRIMARY KEY)"
        parts.append(f"  {name} ON {tbl} -- {sql_str}")
    return "Existing indexes:\n" + "\n".join(parts)


def drop_index(name: str):
    """Drops an index by name."""
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(f"DROP INDEX IF EXISTS {name}")
        conn.commit()
        return f"Dropped index: {name}"
    except Exception as e:
        return f"Error dropping index: {e}"
    finally:
        conn.close()


def think(reasoning: str):
    """No-op tool. Forces the LLM to articulate its reasoning before acting."""
    return "Ok, proceed."


# =====================================================================
#  UTILITY HELPERS (NOT exposed to the LLM)
# =====================================================================

def count_rows(table: str = "transactions"):
    """Safety check: returns row count of a table."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(f"SELECT COUNT(*) FROM {table}")
    count = cursor.fetchone()[0]
    conn.close()
    return count


def measure_latency(query: str, runs: int = 5):
    """Measures average query latency. 1 warmup run (discarded) + N measured runs."""
    wc = get_db_connection()
    wc.cursor().execute(query).fetchall()
    wc.close()

    times = []
    for _ in range(runs):
        conn = get_db_connection()
        cursor = conn.cursor()
        start = time.perf_counter()
        cursor.execute(query)
        cursor.fetchall()
        times.append((time.perf_counter() - start) * 1000)
        conn.close()
    return sum(times) / len(times)


def drop_all_indexes():
    """Drops every non-autoindex index. Used to reset between benchmark runs."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT name FROM sqlite_master "
        "WHERE type='index' AND name NOT LIKE 'sqlite_autoindex%';"
    )
    for (name,) in cursor.fetchall():
        cursor.execute(f"DROP INDEX IF EXISTS {name}")
    conn.commit()
    conn.close()


def seed_redundant_indexes():
    """
    Creates intentionally redundant indexes for the agent to detect.
    Used by eval_report test case #5.
    """
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_email_v1 ON transactions (email)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_email_v2 ON transactions (email)")
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_status_date ON transactions (transaction_status, created_at)"
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_status_only ON transactions (transaction_status)"
    )
    conn.commit()
    conn.close()


def snapshot_indexes():
    """
    Returns a list of (name, sql) for all user-created indexes.
    Used to rollback if the agent makes things worse.
    """
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT name, sql FROM sqlite_master "
        "WHERE type='index' AND name NOT LIKE 'sqlite_autoindex%' AND sql IS NOT NULL;"
    )
    indexes = cursor.fetchall()
    conn.close()
    return indexes


def restore_indexes(snapshot):
    """
    Drops all current user indexes and re-creates from a snapshot.
    This is the rollback mechanism.
    """
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT name FROM sqlite_master "
        "WHERE type='index' AND name NOT LIKE 'sqlite_autoindex%';"
    )
    for (name,) in cursor.fetchall():
        cursor.execute(f"DROP INDEX IF EXISTS {name}")
    for name, sql in snapshot:
        try:
            cursor.execute(sql)
        except Exception:
            pass
    conn.commit()
    conn.close()
