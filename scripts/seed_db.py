"""
IndexPilot - Database Seeder
Creates a realistic multi-table schema with 1M+ rows and NO indexes.
This is the 'broken' state the agent must diagnose and fix.
"""

import os
import sqlite3
import random
import time
from faker import Faker

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(PROJECT_ROOT, "indexpilot.db")


def seed_database():
    fake = Faker()
    Faker.seed(42)
    random.seed(42)

    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    # -- Drop existing tables --
    c.execute("DROP TABLE IF EXISTS transactions")
    c.execute("DROP TABLE IF EXISTS users")

    # -- Create users table (100K rows) --
    c.execute("""
        CREATE TABLE users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            email TEXT NOT NULL,
            country TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # -- Create transactions table (1M rows) --
    c.execute("""
        CREATE TABLE transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            email TEXT NOT NULL,
            transaction_status TEXT NOT NULL,
            amount REAL NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
    """)

    # -- Seed users --
    print("Seeding 100,000 users ...")
    start = time.time()
    countries = ["US", "UK", "IN", "DE", "FR", "JP", "BR", "CA", "AU", "NG"]
    user_rows = []
    for i in range(100_000):
        user_rows.append((
            fake.name(),
            fake.email(),
            random.choice(countries),
        ))
        if len(user_rows) >= 10_000:
            c.executemany(
                "INSERT INTO users (name, email, country) VALUES (?, ?, ?)",
                user_rows,
            )
            user_rows = []
    if user_rows:
        c.executemany(
            "INSERT INTO users (name, email, country) VALUES (?, ?, ?)",
            user_rows,
        )
    conn.commit()
    print(f"  Users seeded in {time.time() - start:.1f}s")

    # -- Seed transactions --
    print("Seeding 1,000,000 transactions ...")
    start = time.time()
    statuses = ["pending", "completed", "failed", "refunded"]
    batch = []
    for i in range(1_000_000):
        batch.append((
            random.randint(1, 100_000),
            fake.email(),
            random.choice(statuses),
            round(random.uniform(1.0, 5000.0), 2),
        ))
        if len(batch) >= 10_000:
            c.executemany(
                "INSERT INTO transactions (user_id, email, transaction_status, amount) "
                "VALUES (?, ?, ?, ?)",
                batch,
            )
            batch = []
            if (i + 1) % 100_000 == 0:
                print(f"  {i + 1:>10,} rows ...")
    if batch:
        c.executemany(
            "INSERT INTO transactions (user_id, email, transaction_status, amount) "
            "VALUES (?, ?, ?, ?)",
            batch,
        )
    conn.commit()
    conn.close()
    print(f"  Transactions seeded in {time.time() - start:.1f}s")
    print("Done. No indexes created (intentionally).")


if __name__ == "__main__":
    seed_database()
