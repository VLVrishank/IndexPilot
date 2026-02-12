"""
IndexPilot - Autonomous Database Performance Agent
Uses Groq's native tool-calling API to diagnose and fix index problems.
"""

import json
import time
import requests
from tools import (
    get_schema,
    explain_analyze,
    apply_index,
    list_indexes,
    drop_index,
    think,
    count_rows,
)

# -- Configuration --------------------------------------------------------

GROQ_API_KEY = os.getenv("GROQ_API_KEY")
GROQ_MODEL  = "openai/gpt-oss-120b"
GROQ_URL    = "https://api.groq.com/openai/v1/chat/completions"

# -- Tool definitions (JSON Schema for Groq) ------------------------------

TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "get_schema",
            "description": (
                "Returns the CREATE TABLE DDL for every table in the database. "
                "Use this first to understand columns, types, and foreign keys."
            ),
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "explain_analyze",
            "description": (
                "Runs EXPLAIN QUERY PLAN on a SQL query and measures real execution time. "
                "Look for SCAN (bad, sequential) vs SEARCH (good, uses index). "
                "Also look for USE TEMP B-TREE (bad, means an in-memory sort is needed)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "The SQL SELECT query to analyze.",
                    }
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "apply_index",
            "description": (
                "Executes a CREATE INDEX SQL statement. "
                "Use this ONLY after confirming a SCAN or TEMP B-TREE in the query plan."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "sql": {
                        "type": "string",
                        "description": "The full CREATE INDEX SQL statement.",
                    }
                },
                "required": ["sql"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_indexes",
            "description": (
                "Returns all existing indexes in the database. "
                "Use this to check for duplicate or redundant indexes before creating new ones."
            ),
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "drop_index",
            "description": (
                "Drops an index by name. Use this to remove redundant or duplicate indexes. "
                "For example, if idx_email_v1 and idx_email_v2 both index the same column, drop one."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "The name of the index to drop.",
                    }
                },
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "think",
            "description": (
                "Use this tool to explain your reasoning before taking any action "
                "(apply_index or drop_index). Describe what you observed in the query plan "
                "and why you chose a specific fix. You MUST call this before every apply_index or drop_index."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "reasoning": {
                        "type": "string",
                        "description": "Your analysis: what you observed, what the problem is, and what fix you will apply and why.",
                    }
                },
                "required": ["reasoning"],
            },
        },
    },
]

# -- Tool dispatch map -----------------------------------------------------

TOOL_DISPATCH = {
    "get_schema":      lambda args: get_schema(),
    "explain_analyze": lambda args: explain_analyze(args["query"]),
    "apply_index":     lambda args: apply_index(args["sql"]),
    "list_indexes":    lambda args: list_indexes(),
    "drop_index":      lambda args: drop_index(args["name"]),
    "think":           lambda args: think(args["reasoning"]),
}

# -- Groq API helper ------------------------------------------------------

def _call_groq(messages: list) -> dict:
    """Sends a chat-completion request to Groq with tools enabled. Retries on rate limits."""
    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": GROQ_MODEL,
        "messages": messages,
        "tools": TOOL_DEFINITIONS,
        "tool_choice": "auto",
        "temperature": 0,
    }

    max_retries = 5
    for attempt in range(max_retries):
        resp = requests.post(GROQ_URL, headers=headers, json=payload)
        if resp.status_code == 429:
            wait = 2 ** attempt * 5
            print(f"    [rate limit] Waiting {wait}s before retry ({attempt+1}/{max_retries}) ...")
            time.sleep(wait)
            continue
        if resp.status_code != 200:
            raise RuntimeError(f"Groq API error {resp.status_code}: {resp.text}")
        return resp.json()

    raise RuntimeError("Groq API: max retries exceeded due to rate limiting.")

# -- System prompt ---------------------------------------------------------

SYSTEM_PROMPT = """\
You are IndexPilot, an autonomous database performance agent specialized in index optimization.

## Your Tools
1. get_schema()         - See all tables, columns, and types
2. list_indexes()       - See all existing indexes
3. explain_analyze(q)   - Get query plan + execution time
4. apply_index(sql)     - Execute a CREATE INDEX statement
5. drop_index(name)     - Remove a redundant or bad index

## Your Process
1. ALWAYS start by calling get_schema() and list_indexes() to understand the current state.
2. Call explain_analyze() on the slow query to see the execution plan.
3. Diagnose the problem by reading the plan:
   - "SCAN table" = sequential scan = missing index on the filtered/joined column
   - "USE TEMP B-TREE" = in-memory sort = missing index on the ORDER BY / GROUP BY column
   - Duplicate indexes on the same column(s) = wasted space, drop the redundant one
4. Apply the fix:
   - Single WHERE filter:        CREATE INDEX idx_col ON table(col)
   - Multiple WHERE filters:     CREATE INDEX idx_cols ON table(eq_col, range_col)
     (put equality columns FIRST, range/inequality columns LAST)
   - JOIN without index:         CREATE INDEX idx_fk ON child_table(foreign_key_col)
   - ORDER BY after WHERE:       CREATE INDEX idx_cols ON table(where_col, order_col)
   - Redundant indexes:          DROP the narrower duplicate
5. Re-run explain_analyze() to VERIFY the fix worked (SEARCH instead of SCAN, no TEMP B-TREE).
6. Summarize what you found and what you fixed.

## Rules
- NEVER guess. Always use tools to inspect before acting.
- NEVER create an index if one already covers the column(s).
- If you find redundant indexes, drop the unnecessary ones.
- ALWAYS call think() before calling apply_index() or drop_index(). Explain what you observed and why you chose this fix.
- Always verify your fix with a second explain_analyze() call.
-END WHEN YOUR DONE
"""

# -- Agent loop ------------------------------------------------------------

def run_agent(query: str, verbose: bool = True) -> str:
    """
    Runs the autonomous agent loop using Groq native tool calling.
    Returns the final text summary from the LLM.
    """
    MAX_STEPS = 20

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user",   "content": f"Optimize this slow query:\n{query}"},
    ]

    # -- Safety: row count before --
    initial_rows = count_rows("transactions")
    if verbose:
        print(f"  [safety] Row count before: {initial_rows:,}")

    for step in range(1, MAX_STEPS + 1):
        if step > 1:
            time.sleep(2)  # avoid Groq rate limits between iterations
        if verbose:
            print(f"  [step {step}] Calling Groq ...")

        data = _call_groq(messages)
        choice = data["choices"][0]
        msg = choice["message"]
        messages.append(msg)

        # -- Tool calls --
        tool_calls = msg.get("tool_calls")
        if tool_calls:
            for tc in tool_calls:
                fn_name = tc["function"]["name"]
                fn_args = json.loads(tc["function"]["arguments"])

                if verbose:
                    if fn_name == "think":
                        print(f"    [REASONING] {fn_args.get('reasoning', '')}")
                    else:
                        print(f"    -> {fn_name}({fn_args})")

                handler = TOOL_DISPATCH.get(fn_name)
                result = str(handler(fn_args)) if handler else f"Unknown tool: {fn_name}"

                if verbose:
                    preview = result[:300] + ("..." if len(result) > 300 else "")
                    print(f"    <- {preview}")

                messages.append({
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "content": result,
                })
            continue

        # -- Final text answer --
        final_text = msg.get("content", "")
        if verbose:
            print(f"  [done] {final_text[:400]}")

        # -- Safety: row count after --
        final_rows = count_rows("transactions")
        if verbose:
            print(f"  [safety] Row count after: {final_rows:,}")
        if final_rows != initial_rows:
            print("  !! CRITICAL: Row count mismatch after agent run!")

        return final_text

    return "Agent reached max steps without a final answer."


# -- CLI -------------------------------------------------------------------

if __name__ == "__main__":
    test = "SELECT * FROM transactions WHERE email = 'test@example.com'"
    print(f"Query: {test}\n")
    result = run_agent(test)
    print(f"\n{'='*60}\n{result}")
