# IndexPilot

An autonomous agent that finds slow SQL queries, figures out which indexes are missing, creates them, and verifies the fix — without human intervention.

## What problem does this solve

Most database slowness comes from missing indexes. A query that should take 1ms ends up scanning every single row in the table, taking seconds instead. The fix is usually one line of SQL (`CREATE INDEX ...`), but knowing *which* index to create requires understanding the query plan, the table schema, and existing indexes.

IndexPilot automates this entire process. Give it a slow query, and it will:

1. Inspect the database schema and existing indexes
2. Analyze the query execution plan
3. Identify the bottleneck (full table scan, missing join index, unnecessary sort)
4. Create the right index
5. Re-analyze to confirm the fix worked
6. Roll back automatically if it made things worse

## How it works

IndexPilot uses an LLM (via Groq API) with tool calling. The LLM can call 6 tools:

| Tool | What it does |
|---|---|
| `get_schema()` | Returns the CREATE TABLE statements for all tables |
| `list_indexes()` | Returns all existing indexes |
| `explain_analyze(query)` | Runs EXPLAIN QUERY PLAN and measures execution time |
| `apply_index(sql)` | Creates an index |
| `drop_index(name)` | Removes an index |
| `think(reasoning)` | Forces the LLM to explain its reasoning before acting |

The agent runs in a loop: the LLM decides which tool to call, we execute it locally, feed the result back, and the LLM decides what to do next. This continues until it has a solution or hits the step limit.

There is no hardcoded logic telling the agent what to do. It reasons about each query independently based on what it observes in the execution plan.

## What it handles

- Single-column WHERE filters (missing index)
- Multi-column WHERE filters (composite index with correct column order)
- JOIN queries (missing foreign key index)
- ORDER BY / GROUP BY (sort without index)
- Redundant index detection and cleanup
- Any combination of the above

## Safety

- Row count is checked before and after. If it changes, all changes are rolled back.
- Query speed is measured before and after. If it got worse, all changes are rolled back.
- The LLM must explain its reasoning (via the `think` tool) before making any change.

## Setup

**Requirements:** Python 3.10+, a Groq API key.

```bash
# Install dependencies
pip install requests faker

# Set your Groq API key in .env file
# GROQ_API_KEY=your_key_here

# Create the test database (100K users + 1M transactions, no indexes)
python scripts/seed_db.py
```

## Usage

**Run the agent on any query:**

```bash
python -m agent.pilot
```

This runs the agent on a default test query. To test your own query, edit the `test` variable at the bottom of `agent/pilot.py`.

**Run the evaluation suite:**

```bash
# Run all 6 test cases
python scripts/eval_report.py

# Run a specific test
python scripts/eval_report.py 1

# Run multiple specific tests
python scripts/eval_report.py 1 3 5
```

**Test cases:**

| # | Scenario |
|---|---|
| 1 | Single WHERE filter (email lookup) |
| 2 | OR condition (user_id or status) |
| 3 | JOIN without foreign key index |
| 4 | ORDER BY without covering index |
| 5 | Redundant index cleanup |
| 6 | Date equality filter |

## Project structure

```
IndexPilot/
  agent/
    __init__.py       -- Package marker
    pilot.py          -- The agent: tool definitions, Groq API, agent loop
    tools.py          -- 6 tools the LLM can call + utility helpers
  scripts/
    seed_db.py        -- Creates the test database (2 tables, 1.1M rows)
    eval_report.py    -- Benchmark suite: runs tests, measures speedup, rollback
  .env                -- Groq API key (not committed)
  .gitignore
  README.md
```

## Example output

```
  Test Case    | Issue Found                  | Fix Applied                  | Speedup    | Row Check  | Verdict
  ----------------------------------------------------------------------------------------------------
  Query #1     | SCAN (single WHERE email)    | CREATE INDEX idx_email       |   99.8%    | OK         | PASS
  Query #3     | SCAN (JOIN on user_id)       | CREATE INDEX idx_user_id     |   99.2%    | OK         | PASS
  ----------------------------------------------------------------------------------------------------
  PASSED: 2/2 | ROLLED BACK: 0 | ROW ERRORS: 0 | Avg Speedup: 99.5%
```

## Limitations

- SQLite only. The architecture supports other databases (swap `tools.py` internals), but only SQLite is implemented for now.
- Does not consider write performance impact. Adding indexes slows down INSERT/UPDATE/DELETE operations.
- LLM decisions are not deterministic. The same query may get different index recommendations on different runs.
- Free-tier Groq API has rate limits. The agent includes retry logic with backoff, but running all 6 tests back-to-back may hit limits.
