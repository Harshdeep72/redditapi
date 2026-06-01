"""
Reddit Campaign Platform Database Layer.
Multi-engine provider supporting remote PostgreSQL (asyncpg) or local SQLite (aiosqlite).
"""

from __future__ import annotations

import os
import json
import logging
import sqlite3
import uuid
import datetime as dt
from datetime import datetime, timezone
from typing import Any, Protocol

import aiosqlite
import asyncpg
from bot.config import settings

logger = logging.getLogger(__name__)

DB_PATH = "data/platform.db"
os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)


def _compile_query(query: str, is_postgres: bool) -> str:
    if not is_postgres:
        return query
    # Replace ? with $1, $2, ...
    parts = query.split('?')
    if len(parts) == 1:
        return query
    compiled = []
    for i, part in enumerate(parts[:-1]):
        compiled.append(part)
        compiled.append(f"${i+1}")
    compiled.append(parts[-1])
    return "".join(compiled)


class DatabaseProvider(Protocol):
    async def init_db(self) -> None: ...
    async def close(self) -> None: ...
    async def execute(self, query: str, *args: Any) -> None: ...
    async def fetch(self, query: str, *args: Any) -> list[dict[str, Any]]: ...
    async def fetchrow(self, query: str, *args: Any) -> dict[str, Any] | None: ...
    async def get_admin_user(self, username: str) -> dict[str, Any] | None: ...
    async def create_admin_user(self, username: str, password_hash: str, role: str, email: str | None) -> None: ...
    async def set_user_web_password(self, discord_id: str, password_hash: str) -> None: ...
    async def generate_web_login_token(self, discord_id: str) -> str: ...
    async def get_user(self, discord_id: str) -> dict[str, Any] | None: ...
    async def register_user(self, discord_id: str) -> dict[str, Any]: ...
    async def update_user_reddit(self, discord_id: str, reddit_username: str, verified: bool) -> bool: ...
    async def get_user_by_reddit(self, reddit_username: str) -> dict[str, Any] | None: ...
    async def get_user_by_referral(self, code: str) -> dict[str, Any] | None: ...
    async def update_user_wallet(self, discord_id: str, method: str, value: str, network: str | None) -> None: ...
    async def set_user_flag(self, discord_id: str, flagged: bool, reason: str | None) -> None: ...
    async def toggle_user_digest(self, discord_id: str, enabled: bool) -> None: ...
    async def apply_referral(self, referee_id: str, code: str) -> bool: ...
    async def get_task(self, task_id: str) -> dict[str, Any] | None: ...
    async def create_task(self, task_id: str, task_type: str, reward: float, slots_total: int, time_limit: int, hold_hours: int, min_trust: int, cooldown_minutes: int, requires_image: bool, target_url: str, campaign_id: str | None) -> None: ...
    async def list_open_tasks(self) -> list[dict[str, Any]]: ...
    async def get_active_claim(self, discord_id: str) -> dict[str, Any] | None: ...
    async def get_user_last_claim_time(self, discord_id: str, task_type: str) -> str | None: ...
    async def claim_task(self, discord_id: str, task_id: str, time_limit_minutes: int) -> int | None: ...
    async def submit_proof(self, claim_id: int, discord_id: str, proof_url: str, screenshot_url: str | None) -> int: ...
    async def update_submission_status(self, submission_id: int, status: str, hold_hours: int, reason: str | None) -> None: ...
    async def request_withdrawal(self, discord_id: str, amount: float, method: str, info: str) -> int | None: ...
    async def get_pending_withdrawals(self) -> list[dict[str, Any]]: ...
    async def finalize_withdrawal(self, withdrawal_id: int, admin_id: str) -> None: ...
    async def create_campaign(self, campaign_id: str, subreddit: str, title: str, content: str, keyword: str | None) -> None: ...
    async def get_campaign(self, campaign_id: str) -> dict[str, Any] | None: ...
    async def update_campaign_post_url(self, campaign_id: str, post_url: str) -> None: ...
    async def get_campaign_tasks(self, campaign_id: str) -> list[dict[str, Any]]: ...


# ─── POSTGRESQL PROVIDER (asyncpg) ────────────────────────────────────────────

class PostgresProvider:
    def __init__(self, dsn: str) -> None:
        self.dsn = dsn
        self._pool: asyncpg.Pool | None = None

    async def _ensure_pool(self) -> asyncpg.Pool:
        if self._pool is None:
            self._pool = await asyncpg.create_pool(self.dsn, min_size=1, max_size=10)
        return self._pool

    async def close(self) -> None:
        if self._pool:
            await self._pool.close()
            self._pool = None

    async def execute(self, query: str, *args: Any) -> None:
        pool = await self._ensure_pool()
        pg_query = _compile_query(query, True)
        await pool.execute(pg_query, *args)

    async def fetch(self, query: str, *args: Any) -> list[dict[str, Any]]:
        pool = await self._ensure_pool()
        pg_query = _compile_query(query, True)
        rows = await pool.fetch(pg_query, *args)
        return [dict(r) for r in rows]

    async def fetchrow(self, query: str, *args: Any) -> dict[str, Any] | None:
        pool = await self._ensure_pool()
        pg_query = _compile_query(query, True)
        row = await pool.fetchrow(pg_query, *args)
        return dict(row) if row else None

    async def init_db(self) -> None:
        pool = await self._ensure_pool()
        async with pool.acquire() as conn:
            # 0. Admin Users
            await conn.execute("""
            CREATE TABLE IF NOT EXISTS admin_users (
                id SERIAL PRIMARY KEY,
                username TEXT UNIQUE NOT NULL,
                password_hash TEXT,
                role TEXT DEFAULT 'client' NOT NULL,
                setup_token TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL,
                status TEXT DEFAULT 'active' NOT NULL,
                display_name TEXT,
                email TEXT,
                notes TEXT,
                applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL,
                approved_at TIMESTAMP,
                approved_by INTEGER,
                first_login_unlocked BOOLEAN DEFAULT false NOT NULL,
                discord_id TEXT,
                discord_username TEXT,
                discord_avatar TEXT,
                discord_linked_at TIMESTAMP
            );
            """)
            # 0.5. Campaigns
            await conn.execute("""
            CREATE TABLE IF NOT EXISTS campaigns (
                campaign_id TEXT PRIMARY KEY,
                subreddit TEXT NOT NULL,
                title TEXT NOT NULL,
                content TEXT NOT NULL,
                keyword TEXT,
                status TEXT DEFAULT 'open',
                target_post_url TEXT
            );
            """)
            # 1. Users
            await conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                discord_id TEXT PRIMARY KEY,
                reddit_username TEXT UNIQUE,
                verified INTEGER DEFAULT 0,
                trust_score INTEGER DEFAULT 100,
                referral_code TEXT UNIQUE,
                balance_pending DOUBLE PRECISION DEFAULT 0.0,
                balance_available DOUBLE PRECISION DEFAULT 0.0,
                is_flagged INTEGER DEFAULT 0,
                flag_reason TEXT,
                upi_id TEXT,
                paypal_email TEXT,
                crypto_wallet TEXT,
                crypto_network TEXT,
                digest_enabled INTEGER DEFAULT 0,
                role TEXT DEFAULT 'user',
                password_hash TEXT,
                web_login_token TEXT
            );
            """)
            # 2. Referrals
            await conn.execute("""
            CREATE TABLE IF NOT EXISTS referrals (
                referral_id SERIAL PRIMARY KEY,
                referrer_id TEXT NOT NULL REFERENCES users(discord_id),
                referee_id TEXT UNIQUE NOT NULL REFERENCES users(discord_id),
                code TEXT NOT NULL,
                credited INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            """)
            # 3. Tasks
            await conn.execute("""
            CREATE TABLE IF NOT EXISTS tasks (
                task_id TEXT PRIMARY KEY,
                type TEXT NOT NULL,
                reward DOUBLE PRECISION NOT NULL,
                slots_total INTEGER NOT NULL,
                slots_filled INTEGER DEFAULT 0,
                time_limit INTEGER NOT NULL,
                hold_hours INTEGER NOT NULL,
                min_trust INTEGER DEFAULT 0,
                cooldown_minutes INTEGER DEFAULT 0,
                requires_image INTEGER DEFAULT 0,
                target_url TEXT NOT NULL,
                status TEXT DEFAULT 'open',
                campaign_id TEXT REFERENCES campaigns(campaign_id) ON DELETE CASCADE,
                comment_index INTEGER,
                parent_index TEXT,
                comment_body TEXT
            );
            """)
            # 4. Claims
            await conn.execute("""
            CREATE TABLE IF NOT EXISTS claims (
                claim_id SERIAL PRIMARY KEY,
                task_id TEXT NOT NULL REFERENCES tasks(task_id) ON DELETE CASCADE,
                discord_id TEXT NOT NULL REFERENCES users(discord_id),
                status TEXT DEFAULT 'active',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                expires_at TIMESTAMP NOT NULL
            );
            """)
            # 5. Submissions
            await conn.execute("""
            CREATE TABLE IF NOT EXISTS submissions (
                submission_id SERIAL PRIMARY KEY,
                claim_id INTEGER NOT NULL REFERENCES claims(claim_id) ON DELETE CASCADE,
                discord_id TEXT NOT NULL REFERENCES users(discord_id),
                proof_url TEXT NOT NULL,
                screenshot_url TEXT,
                status TEXT DEFAULT 'pending_validation',
                rejection_reason TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                hold_expires_at TIMESTAMP,
                last_checked_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            """)
            # 6. Withdrawals
            await conn.execute("""
            CREATE TABLE IF NOT EXISTS withdrawals (
                withdrawal_id SERIAL PRIMARY KEY,
                discord_id TEXT NOT NULL REFERENCES users(discord_id),
                amount DOUBLE PRECISION NOT NULL,
                payment_method TEXT NOT NULL,
                payment_info TEXT NOT NULL,
                status TEXT DEFAULT 'pending',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                marked_paid_by TEXT DEFAULT '[]'
            );
            """)

            def _hash_pwd(password: str) -> str:
                import hashlib
                import os
                import base64
                salt = os.urandom(16)
                key = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt, 100000)
                return base64.b64encode(salt + key).decode('utf-8')

            row = await conn.fetchrow("SELECT COUNT(*) as count FROM admin_users;")
            if row and row["count"] == 0:
                dev_hash = _hash_pwd("devpass")
                client_hash = _hash_pwd("clientpass")
                staff_hash = _hash_pwd("staffpass")
                admin_hash = _hash_pwd("adminpass")
                await conn.execute(
                    """
                    INSERT INTO admin_users (username, password_hash, role, display_name) VALUES
                    ('dev', $1, 'dev', 'Platform Developer'),
                    ('client', $2, 'client', 'Campaign Advertiser'),
                    ('staff', $3, 'staff', 'Moderation Team'),
                    ('admin', $4, 'admin', 'System Administrator');
                    """,
                    dev_hash, client_hash, staff_hash, admin_hash
                )

    async def get_admin_user(self, username: str) -> dict[str, Any] | None:
        pool = await self._ensure_pool()
        row = await pool.fetchrow("SELECT * FROM admin_users WHERE LOWER(username) = LOWER($1);", username)
        return dict(row) if row else None

    async def create_admin_user(self, username: str, password_hash: str, role: str, email: str | None) -> None:
        pool = await self._ensure_pool()
        await pool.execute(
            """
            INSERT INTO admin_users (username, password_hash, role, email) 
            VALUES ($1, $2, $3, $4)
            ON CONFLICT (username) DO UPDATE 
            SET password_hash = EXCLUDED.password_hash, role = EXCLUDED.role, email = EXCLUDED.email;
            """,
            username, password_hash, role, email
        )

    async def set_user_web_password(self, discord_id: str, password_hash: str) -> None:
        await self.register_user(discord_id)
        pool = await self._ensure_pool()
        await pool.execute(
            "UPDATE users SET password_hash = $1, web_login_token = NULL WHERE discord_id = $2;",
            password_hash, discord_id
        )

    async def generate_web_login_token(self, discord_id: str) -> str:
        import secrets
        token = "".join(secrets.choice("0123456789") for _ in range(6))
        await self.register_user(discord_id)
        pool = await self._ensure_pool()
        await pool.execute(
            "UPDATE users SET web_login_token = $1 WHERE discord_id = $2;",
            token, discord_id
        )
        return token

    async def get_user(self, discord_id: str) -> dict[str, Any] | None:
        pool = await self._ensure_pool()
        row = await pool.fetchrow("SELECT * FROM users WHERE discord_id = $1;", discord_id)
        if not row and discord_id == "dev":
            ref_code = str(uuid.uuid4())[:8].upper()
            await pool.execute(
                "INSERT INTO users (discord_id, reddit_username, verified, balance_available, balance_pending, trust_score, referral_code) "
                "VALUES ('dev', 'dev_tester', 1, 1000.0, 0.0, 100, $1) ON CONFLICT (discord_id) DO NOTHING;",
                ref_code
            )
            row = await pool.fetchrow("SELECT * FROM users WHERE discord_id = $1;", discord_id)
        return dict(row) if row else None

    async def register_user(self, discord_id: str) -> dict[str, Any]:
        user = await self.get_user(discord_id)
        if user:
            return user
        ref_code = str(uuid.uuid4())[:8].upper()
        pool = await self._ensure_pool()
        if discord_id == "dev":
            await pool.execute(
                "INSERT INTO users (discord_id, reddit_username, verified, balance_available, balance_pending, trust_score, referral_code) "
                "VALUES ('dev', 'dev_tester', 1, 1000.0, 0.0, 100, $1) ON CONFLICT (discord_id) DO NOTHING;",
                ref_code
            )
        else:
            await pool.execute(
                "INSERT INTO users (discord_id, referral_code) VALUES ($1, $2);",
                discord_id, ref_code
            )
        row = await pool.fetchrow("SELECT * FROM users WHERE discord_id = $1;", discord_id)
        return dict(row) if row else {}

    async def update_user_reddit(self, discord_id: str, reddit_username: str, verified: bool) -> bool:
        await self.register_user(discord_id)
        pool = await self._ensure_pool()
        try:
            await pool.execute(
                "UPDATE users SET reddit_username = $1, verified = $2 WHERE discord_id = $3;",
                reddit_username, 1 if verified else 0, discord_id
            )
            return True
        except asyncpg.UniqueViolationError:
            return False

    async def get_user_by_reddit(self, reddit_username: str) -> dict[str, Any] | None:
        pool = await self._ensure_pool()
        row = await pool.fetchrow("SELECT * FROM users WHERE LOWER(reddit_username) = LOWER($1);", reddit_username)
        return dict(row) if row else None

    async def get_user_by_referral(self, code: str) -> dict[str, Any] | None:
        pool = await self._ensure_pool()
        row = await pool.fetchrow("SELECT * FROM users WHERE referral_code = $1;", code.upper().strip())
        return dict(row) if row else None

    async def update_user_wallet(self, discord_id: str, method: str, value: str, network: str | None) -> None:
        await self.register_user(discord_id)
        pool = await self._ensure_pool()
        if method == "upi":
            await pool.execute("UPDATE users SET upi_id = $1 WHERE discord_id = $2;", value, discord_id)
        elif method == "paypal":
            await pool.execute("UPDATE users SET paypal_email = $1 WHERE discord_id = $2;", value, discord_id)
        elif method == "crypto":
            await pool.execute(
                "UPDATE users SET crypto_wallet = $1, crypto_network = $2 WHERE discord_id = $3;",
                value, network, discord_id
            )

    async def set_user_flag(self, discord_id: str, flagged: bool, reason: str | None) -> None:
        await self.register_user(discord_id)
        pool = await self._ensure_pool()
        await pool.execute(
            "UPDATE users SET is_flagged = $1, flag_reason = $2 WHERE discord_id = $3;",
            1 if flagged else 0, reason, discord_id
        )

    async def toggle_user_digest(self, discord_id: str, enabled: bool) -> None:
        await self.register_user(discord_id)
        pool = await self._ensure_pool()
        await pool.execute(
            "UPDATE users SET digest_enabled = $1 WHERE discord_id = $2;",
            1 if enabled else 0, discord_id
        )

    async def apply_referral(self, referee_id: str, code: str) -> bool:
        referee = await self.register_user(referee_id)
        referrer = await self.get_user_by_referral(code)
        if not referrer or referrer["discord_id"] == referee_id:
            return False
        pool = await self._ensure_pool()
        try:
            await pool.execute(
                "INSERT INTO referrals (referrer_id, referee_id, code) VALUES ($1, $2, $3);",
                referrer["discord_id"], referee_id, code.upper()
            )
            return True
        except asyncpg.UniqueViolationError:
            return False

    async def get_task(self, task_id: str) -> dict[str, Any] | None:
        pool = await self._ensure_pool()
        row = await pool.fetchrow("SELECT * FROM tasks WHERE task_id = $1;", task_id)
        return dict(row) if row else None

    async def create_task(self, task_id: str, task_type: str, reward: float, slots_total: int, time_limit: int, hold_hours: int, min_trust: int, cooldown_minutes: int, requires_image: bool, target_url: str, campaign_id: str | None) -> None:
        pool = await self._ensure_pool()
        await pool.execute(
            """
            INSERT INTO tasks (
                task_id, type, reward, slots_total, time_limit, hold_hours,
                min_trust, cooldown_minutes, requires_image, target_url, campaign_id
            ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11);
            """,
            task_id, task_type, reward, slots_total, time_limit, hold_hours,
            min_trust, cooldown_minutes, 1 if requires_image else 0, target_url, campaign_id
        )

    async def list_open_tasks(self) -> list[dict[str, Any]]:
        pool = await self._ensure_pool()
        rows = await pool.fetch("SELECT * FROM tasks WHERE status = 'open' AND slots_filled < slots_total;")
        return [dict(r) for r in rows]

    async def get_active_claim(self, discord_id: str) -> dict[str, Any] | None:
        now = datetime.now(timezone.utc)
        pool = await self._ensure_pool()
        row = await pool.fetchrow(
            """
            SELECT c.*, t.reward, t.type as task_type, t.target_url 
            FROM claims c 
            JOIN tasks t ON c.task_id = t.task_id 
            WHERE c.discord_id = $1 AND c.status = 'active' AND c.expires_at > $2;
            """,
            discord_id, now
        )
        return dict(row) if row else None

    async def get_user_last_claim_time(self, discord_id: str, task_type: str) -> str | None:
        pool = await self._ensure_pool()
        row = await pool.fetchrow(
            """
            SELECT c.created_at 
            FROM claims c
            JOIN tasks t ON c.task_id = t.task_id
            WHERE c.discord_id = $1 AND t.type = $2
            ORDER BY c.created_at DESC LIMIT 1;
            """,
            discord_id, task_type
        )
        return row[0].isoformat() if row and row[0] else None

    async def claim_task(self, discord_id: str, task_id: str, time_limit_minutes: int) -> int | None:
        await self.register_user(discord_id)
        task = await self.get_task(task_id)
        if not task or task["status"] != "open" or task["slots_filled"] >= task["slots_total"]:
            return None
        expires_at = datetime.now(timezone.utc) + dt.timedelta(minutes=time_limit_minutes)
        pool = await self._ensure_pool()
        
        async with pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(
                    "UPDATE tasks SET slots_filled = slots_filled + 1 WHERE task_id = $1 AND slots_filled < slots_total;",
                    task_id
                )
                claim_id = await conn.fetchval(
                    "INSERT INTO claims (task_id, discord_id, expires_at) VALUES ($1, $2, $3) RETURNING claim_id;",
                    task_id, discord_id, expires_at
                )
                return claim_id

    async def submit_proof(self, claim_id: int, discord_id: str, proof_url: str, screenshot_url: str | None) -> int:
        pool = await self._ensure_pool()
        async with pool.acquire() as conn:
            async with conn.transaction():
                sub_id = await conn.fetchval(
                    "INSERT INTO submissions (claim_id, discord_id, proof_url, screenshot_url) VALUES ($1, $2, $3, $4) RETURNING submission_id;",
                    claim_id, discord_id, proof_url, screenshot_url
                )
                await conn.execute("UPDATE claims SET status = 'submitted' WHERE claim_id = $1;", claim_id)
                return sub_id

    async def update_submission_status(self, submission_id: int, status: str, hold_hours: int, reason: str | None) -> None:
        hold_expires = None
        if status == "pending_hold":
            hold_expires = datetime.now(timezone.utc) + dt.timedelta(hours=hold_hours)
        pool = await self._ensure_pool()
        
        async with pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(
                    """
                    UPDATE submissions 
                    SET status = $1, hold_expires_at = $2, rejection_reason = $3, last_checked_at = CURRENT_TIMESTAMP
                    WHERE submission_id = $4;
                    """,
                    status, hold_expires, reason, submission_id
                )
                info = await conn.fetchrow(
                    """
                    SELECT s.discord_id, t.reward, c.claim_id, c.task_id 
                    FROM submissions s
                    JOIN claims c ON s.claim_id = c.claim_id
                    JOIN tasks t ON c.task_id = t.task_id
                    WHERE s.submission_id = $1;
                    """,
                    submission_id
                )
                if info:
                    discord_id = info["discord_id"]
                    reward = info["reward"]
                    claim_id = info["claim_id"]
                    if status == "pending_hold":
                        await conn.execute("UPDATE users SET balance_pending = balance_pending + $1 WHERE discord_id = $2;", reward, discord_id)
                    elif status == "rejected":
                        await conn.execute("UPDATE claims SET status = 'completed' WHERE claim_id = $1;", claim_id)
                    elif status == "completed":
                        await conn.execute("UPDATE claims SET status = 'completed' WHERE claim_id = $1;", claim_id)
                        await conn.execute("UPDATE users SET balance_available = balance_available + $1 WHERE discord_id = $2;", reward, discord_id)

    async def request_withdrawal(self, discord_id: str, amount: float, method: str, info: str) -> int | None:
        user = await self.get_user(discord_id)
        if not user or user["balance_available"] < amount or amount <= 0:
            return None
        pool = await self._ensure_pool()
        async with pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute("UPDATE users SET balance_available = balance_available - $1 WHERE discord_id = $2;", amount, discord_id)
                withdrawal_id = await conn.fetchval(
                    "INSERT INTO withdrawals (discord_id, amount, payment_method, payment_info) VALUES ($1, $2, $3, $4) RETURNING withdrawal_id;",
                    discord_id, amount, method, info
                )
                return withdrawal_id

    async def get_pending_withdrawals(self) -> list[dict[str, Any]]:
        pool = await self._ensure_pool()
        rows = await pool.fetch("SELECT * FROM withdrawals WHERE status = 'pending';")
        return [dict(r) for r in rows]

    async def finalize_withdrawal(self, withdrawal_id: int, admin_id: str) -> None:
        pool = await self._ensure_pool()
        async with pool.acquire() as conn:
            async with conn.transaction():
                w = await conn.fetchrow("SELECT * FROM withdrawals WHERE withdrawal_id = $1;", withdrawal_id)
                if w:
                    paid_by = json.loads(w["marked_paid_by"])
                    if admin_id not in paid_by:
                        paid_by.append(admin_id)
                    await conn.execute(
                        "UPDATE withdrawals SET status = 'completed', marked_paid_by = $1 WHERE withdrawal_id = $2;",
                        json.dumps(paid_by), withdrawal_id
                    )

    async def create_campaign(self, campaign_id: str, subreddit: str, title: str, content: str, keyword: str | None) -> None:
        pool = await self._ensure_pool()
        await pool.execute(
            "INSERT INTO campaigns (campaign_id, subreddit, title, content, keyword) VALUES ($1, $2, $3, $4, $5);",
            campaign_id, subreddit, title, content, keyword
        )

    async def get_campaign(self, campaign_id: str) -> dict[str, Any] | None:
        pool = await self._ensure_pool()
        row = await pool.fetchrow("SELECT * FROM campaigns WHERE campaign_id = $1;", campaign_id)
        return dict(row) if row else None

    async def update_campaign_post_url(self, campaign_id: str, post_url: str) -> None:
        pool = await self._ensure_pool()
        async with pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute("UPDATE campaigns SET target_post_url = $1 WHERE campaign_id = $2;", post_url, campaign_id)
                await conn.execute("UPDATE tasks SET target_url = $1 WHERE campaign_id = $2 AND type = 'reddit_comment';", post_url, campaign_id)

    async def get_campaign_tasks(self, campaign_id: str) -> list[dict[str, Any]]:
        pool = await self._ensure_pool()
        rows = await pool.fetch("SELECT * FROM tasks WHERE campaign_id = $1 ORDER BY type DESC, comment_index ASC;", campaign_id)
        return [dict(r) for r in rows]


# ─── SQLITE PROVIDER (aiosqlite) ──────────────────────────────────────────────

class SQLiteProvider:
    async def close(self) -> None:
        pass

    async def execute(self, query: str, *args: Any) -> None:
        async with aiosqlite.connect(DB_PATH) as db:
            if args:
                await db.execute(query, args)
            else:
                await db.execute(query)
            await db.commit()

    async def fetch(self, query: str, *args: Any) -> list[dict[str, Any]]:
        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(query, args) as cursor:
                rows = await cursor.fetchall()
                return [dict(r) for r in rows]

    async def fetchrow(self, query: str, *args: Any) -> dict[str, Any] | None:
        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(query, args) as cursor:
                row = await cursor.fetchone()
                return dict(row) if row else None

    async def init_db(self) -> None:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("PRAGMA foreign_keys = ON;")
            await db.execute("""
            CREATE TABLE IF NOT EXISTS admin_users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password_hash TEXT,
                role TEXT DEFAULT 'client' NOT NULL,
                setup_token TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL,
                status TEXT DEFAULT 'active' NOT NULL,
                display_name TEXT,
                email TEXT,
                notes TEXT,
                applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL,
                approved_at TIMESTAMP,
                approved_by INTEGER,
                first_login_unlocked BOOLEAN DEFAULT false NOT NULL,
                discord_id TEXT,
                discord_username TEXT,
                discord_avatar TEXT,
                discord_linked_at TIMESTAMP
            );
            """)
            await db.execute("""
            CREATE TABLE IF NOT EXISTS campaigns (
                campaign_id TEXT PRIMARY KEY,
                subreddit TEXT NOT NULL,
                title TEXT NOT NULL,
                content TEXT NOT NULL,
                keyword TEXT,
                status TEXT DEFAULT 'open',
                target_post_url TEXT
            );
            """)
            await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                discord_id TEXT PRIMARY KEY,
                reddit_username TEXT UNIQUE,
                verified INTEGER DEFAULT 0,
                trust_score INTEGER DEFAULT 100,
                referral_code TEXT UNIQUE,
                balance_pending REAL DEFAULT 0.0,
                balance_available REAL DEFAULT 0.0,
                is_flagged INTEGER DEFAULT 0,
                flag_reason TEXT,
                upi_id TEXT,
                paypal_email TEXT,
                crypto_wallet TEXT,
                crypto_network TEXT,
                digest_enabled INTEGER DEFAULT 0,
                role TEXT DEFAULT 'user',
                password_hash TEXT,
                web_login_token TEXT
            );
            """)
            await db.execute("""
            CREATE TABLE IF NOT EXISTS referrals (
                referral_id INTEGER PRIMARY KEY AUTOINCREMENT,
                referrer_id TEXT NOT NULL,
                referee_id TEXT UNIQUE NOT NULL,
                code TEXT NOT NULL,
                credited INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (referrer_id) REFERENCES users(discord_id),
                FOREIGN KEY (referee_id) REFERENCES users(discord_id)
            );
            """)
            await db.execute("""
            CREATE TABLE IF NOT EXISTS tasks (
                task_id TEXT PRIMARY KEY,
                type TEXT NOT NULL,
                reward REAL NOT NULL,
                slots_total INTEGER NOT NULL,
                slots_filled INTEGER DEFAULT 0,
                time_limit INTEGER NOT NULL,
                hold_hours INTEGER NOT NULL,
                min_trust INTEGER DEFAULT 0,
                cooldown_minutes INTEGER DEFAULT 0,
                requires_image INTEGER DEFAULT 0,
                target_url TEXT NOT NULL,
                status TEXT DEFAULT 'open',
                campaign_id TEXT,
                comment_index INTEGER,
                parent_index TEXT,
                comment_body TEXT,
                FOREIGN KEY (campaign_id) REFERENCES campaigns(campaign_id) ON DELETE CASCADE
            );
            """)
            await db.execute("""
            CREATE TABLE IF NOT EXISTS claims (
                claim_id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id TEXT NOT NULL,
                discord_id TEXT NOT NULL,
                status TEXT DEFAULT 'active',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                expires_at TIMESTAMP NOT NULL,
                FOREIGN KEY (task_id) REFERENCES tasks(task_id) ON DELETE CASCADE,
                FOREIGN KEY (discord_id) REFERENCES users(discord_id)
            );
            """)
            await db.execute("""
            CREATE TABLE IF NOT EXISTS submissions (
                submission_id INTEGER PRIMARY KEY AUTOINCREMENT,
                claim_id INTEGER NOT NULL,
                discord_id TEXT NOT NULL,
                proof_url TEXT NOT NULL,
                screenshot_url TEXT,
                status TEXT DEFAULT 'pending_validation',
                rejection_reason TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                hold_expires_at TIMESTAMP,
                last_checked_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (claim_id) REFERENCES claims(claim_id) ON DELETE CASCADE,
                FOREIGN KEY (discord_id) REFERENCES users(discord_id)
            );
            """)
            await db.execute("""
            CREATE TABLE IF NOT EXISTS withdrawals (
                withdrawal_id INTEGER PRIMARY KEY AUTOINCREMENT,
                discord_id TEXT NOT NULL,
                amount REAL NOT NULL,
                payment_method TEXT NOT NULL,
                payment_info TEXT NOT NULL,
                status TEXT DEFAULT 'pending',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                marked_paid_by TEXT DEFAULT '[]',
                FOREIGN KEY (discord_id) REFERENCES users(discord_id)
            );
            """)
            def _hash_pwd(password: str) -> str:
                import hashlib
                import os
                import base64
                salt = os.urandom(16)
                key = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt, 100000)
                return base64.b64encode(salt + key).decode('utf-8')

            async with db.execute("SELECT COUNT(*) FROM admin_users;") as cursor:
                row = await cursor.fetchone()
                count = row[0] if row else 0
            if count == 0:
                dev_hash = _hash_pwd("devpass")
                client_hash = _hash_pwd("clientpass")
                staff_hash = _hash_pwd("staffpass")
                admin_hash = _hash_pwd("adminpass")
                await db.execute(
                    """
                    INSERT INTO admin_users (username, password_hash, role, display_name) VALUES
                    ('dev', ?, 'dev', 'Platform Developer'),
                    ('client', ?, 'client', 'Campaign Advertiser'),
                    ('staff', ?, 'staff', 'Moderation Team'),
                    ('admin', ?, 'admin', 'System Administrator');
                    """,
                    (dev_hash, client_hash, staff_hash, admin_hash)
                )
            await db.commit()

    async def get_admin_user(self, username: str) -> dict[str, Any] | None:
        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT * FROM admin_users WHERE LOWER(username) = LOWER(?);", (username,)) as cursor:
                row = await cursor.fetchone()
                return dict(row) if row else None

    async def create_admin_user(self, username: str, password_hash: str, role: str, email: str | None) -> None:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                """
                INSERT INTO admin_users (username, password_hash, role, email) 
                VALUES (?, ?, ?, ?)
                ON CONFLICT(username) DO UPDATE 
                SET password_hash = excluded.password_hash, role = excluded.role, email = excluded.email;
                """,
                (username, password_hash, role, email)
            )
            await db.commit()

    async def set_user_web_password(self, discord_id: str, password_hash: str) -> None:
        await self.register_user(discord_id)
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                "UPDATE users SET password_hash = ?, web_login_token = NULL WHERE discord_id = ?;",
                (password_hash, discord_id)
            )
            await db.commit()

    async def generate_web_login_token(self, discord_id: str) -> str:
        import secrets
        token = "".join(secrets.choice("0123456789") for _ in range(6))
        await self.register_user(discord_id)
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                "UPDATE users SET web_login_token = ? WHERE discord_id = ?;",
                (token, discord_id)
            )
            await db.commit()
        return token

    async def get_user(self, discord_id: str) -> dict[str, Any] | None:
        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT * FROM users WHERE discord_id = ?;", (discord_id,)) as cursor:
                row = await cursor.fetchone()
            if not row and discord_id == "dev":
                ref_code = str(uuid.uuid4())[:8].upper()
                await db.execute(
                    "INSERT INTO users (discord_id, reddit_username, verified, balance_available, balance_pending, trust_score, referral_code) "
                    "VALUES ('dev', 'dev_tester', 1, 1000.0, 0.0, 100, ?);",
                    (ref_code,)
                )
                await db.commit()
                async with db.execute("SELECT * FROM users WHERE discord_id = ?;", (discord_id,)) as cursor:
                    row = await cursor.fetchone()
            return dict(row) if row else None

    async def register_user(self, discord_id: str) -> dict[str, Any]:
        user = await self.get_user(discord_id)
        if user:
            return user
        ref_code = str(uuid.uuid4())[:8].upper()
        async with aiosqlite.connect(DB_PATH) as db:
            if discord_id == "dev":
                await db.execute(
                    "INSERT INTO users (discord_id, reddit_username, verified, balance_available, balance_pending, trust_score, referral_code) "
                    "VALUES ('dev', 'dev_tester', 1, 1000.0, 0.0, 100, ?);",
                    (ref_code,)
                )
            else:
                await db.execute("INSERT INTO users (discord_id, referral_code) VALUES (?, ?);", (discord_id, ref_code))
            await db.commit()
            async with db.execute("SELECT * FROM users WHERE discord_id = ?;", (discord_id,)) as cursor:
                row = await cursor.fetchone()
            return dict(row) if row else {}

    async def update_user_reddit(self, discord_id: str, reddit_username: str, verified: bool) -> bool:
        await self.register_user(discord_id)
        try:
            async with aiosqlite.connect(DB_PATH) as db:
                await db.execute(
                    "UPDATE users SET reddit_username = ?, verified = ? WHERE discord_id = ?;",
                    (reddit_username, 1 if verified else 0, discord_id)
                )
                await db.commit()
            return True
        except sqlite3.IntegrityError:
            return False

    async def get_user_by_reddit(self, reddit_username: str) -> dict[str, Any] | None:
        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT * FROM users WHERE LOWER(reddit_username) = LOWER(?);", (reddit_username,)) as cursor:
                row = await cursor.fetchone()
                return dict(row) if row else None

    async def get_user_by_referral(self, code: str) -> dict[str, Any] | None:
        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT * FROM users WHERE referral_code = ?;", (code.upper().strip(),)) as cursor:
                row = await cursor.fetchone()
                return dict(row) if row else None

    async def update_user_wallet(self, discord_id: str, method: str, value: str, network: str | None) -> None:
        await self.register_user(discord_id)
        async with aiosqlite.connect(DB_PATH) as db:
            if method == "upi":
                await db.execute("UPDATE users SET upi_id = ? WHERE discord_id = ?;", (value, discord_id))
            elif method == "paypal":
                await db.execute("UPDATE users SET paypal_email = ? WHERE discord_id = ?;", (value, discord_id))
            elif method == "crypto":
                await db.execute(
                    "UPDATE users SET crypto_wallet = ?, crypto_network = ? WHERE discord_id = ?;",
                    (value, network, discord_id)
                )
            await db.commit()

    async def set_user_flag(self, discord_id: str, flagged: bool, reason: str | None) -> None:
        await self.register_user(discord_id)
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                "UPDATE users SET is_flagged = ?, flag_reason = ? WHERE discord_id = ?;",
                (1 if flagged else 0, reason, discord_id)
            )
            await db.commit()

    async def toggle_user_digest(self, discord_id: str, enabled: bool) -> None:
        await self.register_user(discord_id)
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                "UPDATE users SET digest_enabled = ? WHERE discord_id = ?;",
                (1 if enabled else 0, discord_id)
            )
            await db.commit()

    async def apply_referral(self, referee_id: str, code: str) -> bool:
        referee = await self.register_user(referee_id)
        referrer = await self.get_user_by_referral(code)
        if not referrer or referrer["discord_id"] == referee_id:
            return False
        try:
            async with aiosqlite.connect(DB_PATH) as db:
                await db.execute(
                    "INSERT INTO referrals (referrer_id, referee_id, code) VALUES (?, ?, ?);",
                    (referrer["discord_id"], referee_id, code.upper())
                )
                await db.commit()
            return True
        except sqlite3.IntegrityError:
            return False

    async def get_task(self, task_id: str) -> dict[str, Any] | None:
        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT * FROM tasks WHERE task_id = ?;", (task_id,)) as cursor:
                row = await cursor.fetchone()
                return dict(row) if row else None

    async def create_task(self, task_id: str, task_type: str, reward: float, slots_total: int, time_limit: int, hold_hours: int, min_trust: int, cooldown_minutes: int, requires_image: bool, target_url: str, campaign_id: str | None) -> None:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                """
                INSERT INTO tasks (
                    task_id, type, reward, slots_total, time_limit, hold_hours,
                    min_trust, cooldown_minutes, requires_image, target_url, campaign_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                """,
                (task_id, task_type, reward, slots_total, time_limit, hold_hours,
                 min_trust, cooldown_minutes, 1 if requires_image else 0, target_url, campaign_id)
            )
            await db.commit()

    async def list_open_tasks(self) -> list[dict[str, Any]]:
        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT * FROM tasks WHERE status = 'open' AND slots_filled < slots_total;") as cursor:
                rows = await cursor.fetchall()
                return [dict(r) for r in rows]

    async def get_active_claim(self, discord_id: str) -> dict[str, Any] | None:
        now = datetime.now(timezone.utc).isoformat()
        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                """
                SELECT c.*, t.reward, t.type as task_type, t.target_url 
                FROM claims c 
                JOIN tasks t ON c.task_id = t.task_id 
                WHERE c.discord_id = ? AND c.status = 'active' AND c.expires_at > ?;
                """,
                (discord_id, now)
            ) as cursor:
                row = await cursor.fetchone()
                return dict(row) if row else None

    async def get_user_last_claim_time(self, discord_id: str, task_type: str) -> str | None:
        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute(
                """
                SELECT c.created_at 
                FROM claims c
                JOIN tasks t ON c.task_id = t.task_id
                WHERE c.discord_id = ? AND t.type = ?
                ORDER BY c.created_at DESC LIMIT 1;
                """,
                (discord_id, task_type)
            ) as cursor:
                row = await cursor.fetchone()
                return row[0] if row else None

    async def claim_task(self, discord_id: str, task_id: str, time_limit_minutes: int) -> int | None:
        await self.register_user(discord_id)
        task = await self.get_task(task_id)
        if not task or task["status"] != "open" or task["slots_filled"] >= task["slots_total"]:
            return None
        expires_at = (datetime.now(timezone.utc) + dt.timedelta(minutes=time_limit_minutes)).isoformat()
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                "UPDATE tasks SET slots_filled = slots_filled + 1 WHERE task_id = ? AND slots_filled < slots_total;",
                (task_id,)
            )
            await db.execute(
                "INSERT INTO claims (task_id, discord_id, expires_at) VALUES (?, ?, ?);",
                (task_id, discord_id, expires_at)
            )
            async with db.execute("SELECT last_insert_rowid();") as cursor:
                row = await cursor.fetchone()
                claim_id = row[0] if row else None
            await db.commit()
            return claim_id

    async def submit_proof(self, claim_id: int, discord_id: str, proof_url: str, screenshot_url: str | None) -> int:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                "INSERT INTO submissions (claim_id, discord_id, proof_url, screenshot_url) VALUES (?, ?, ?, ?);",
                (claim_id, discord_id, proof_url, screenshot_url)
            )
            await db.execute("UPDATE claims SET status = 'submitted' WHERE claim_id = ?;", (claim_id,))
            async with db.execute("SELECT last_insert_rowid();") as cursor:
                row = await cursor.fetchone()
                sub_id = row[0] if row else 0
            await db.commit()
            return sub_id

    async def update_submission_status(self, submission_id: int, status: str, hold_hours: int, reason: str | None) -> None:
        hold_expires = None
        if status == "pending_hold":
            hold_expires = (datetime.now(timezone.utc) + dt.timedelta(hours=hold_hours)).isoformat()
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                """
                UPDATE submissions 
                SET status = ?, hold_expires_at = ?, rejection_reason = ?, last_checked_at = CURRENT_TIMESTAMP
                WHERE submission_id = ?;
                """,
                (status, hold_expires, reason, submission_id)
            )
            db.row_factory = aiosqlite.Row
            async with db.execute(
                """
                SELECT s.discord_id, t.reward, c.claim_id, c.task_id 
                FROM submissions s
                JOIN claims c ON s.claim_id = c.claim_id
                JOIN tasks t ON c.task_id = t.task_id
                WHERE s.submission_id = ?;
                """,
                (submission_id,)
            ) as cursor:
                info = await cursor.fetchone()
            if info:
                discord_id = info["discord_id"]
                reward = info["reward"]
                claim_id = info["claim_id"]
                if status == "pending_hold":
                    await db.execute("UPDATE users SET balance_pending = balance_pending + ? WHERE discord_id = ?;", (reward, discord_id))
                elif status == "rejected":
                    await db.execute("UPDATE claims SET status = 'completed' WHERE claim_id = ?;", (claim_id,))
                elif status == "completed":
                    await db.execute("UPDATE claims SET status = 'completed' WHERE claim_id = ?;", (claim_id,))
                    await db.execute("UPDATE users SET balance_available = balance_available + ? WHERE discord_id = ?;", (reward, discord_id))
            await db.commit()

    async def request_withdrawal(self, discord_id: str, amount: float, method: str, info: str) -> int | None:
        user = await self.get_user(discord_id)
        if not user or user["balance_available"] < amount or amount <= 0:
            return None
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("UPDATE users SET balance_available = balance_available - ? WHERE discord_id = ?;", (amount, discord_id))
            await db.execute(
                "INSERT INTO withdrawals (discord_id, amount, payment_method, payment_info) VALUES (?, ?, ?, ?);",
                (discord_id, amount, method, info)
            )
            async with db.execute("SELECT last_insert_rowid();") as cursor:
                row = await cursor.fetchone()
                withdrawal_id = row[0] if row else None
            await db.commit()
            return withdrawal_id

    async def get_pending_withdrawals(self) -> list[dict[str, Any]]:
        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT * FROM withdrawals WHERE status = 'pending';") as cursor:
                rows = await cursor.fetchall()
                return [dict(r) for r in rows]

    async def finalize_withdrawal(self, withdrawal_id: int, admin_id: str) -> None:
        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT * FROM withdrawals WHERE withdrawal_id = ?;", (withdrawal_id,)) as cursor:
                w = await cursor.fetchone()
                if w:
                    paid_by = json.loads(w["marked_paid_by"])
                    if admin_id not in paid_by:
                        paid_by.append(admin_id)
                    await db.execute(
                        "UPDATE withdrawals SET status = 'completed', marked_paid_by = ? WHERE withdrawal_id = ?;",
                        (json.dumps(paid_by), withdrawal_id)
                    )
                    await db.commit()

    async def create_campaign(self, campaign_id: str, subreddit: str, title: str, content: str, keyword: str | None) -> None:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                "INSERT INTO campaigns (campaign_id, subreddit, title, content, keyword) VALUES (?, ?, ?, ?, ?);",
                (campaign_id, subreddit, title, content, keyword)
            )
            await db.commit()

    async def get_campaign(self, campaign_id: str) -> dict[str, Any] | None:
        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT * FROM campaigns WHERE campaign_id = ?;", (campaign_id,)) as cursor:
                row = await cursor.fetchone()
                return dict(row) if row else None

    async def update_campaign_post_url(self, campaign_id: str, post_url: str) -> None:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("UPDATE campaigns SET target_post_url = ? WHERE campaign_id = ?;", (post_url, campaign_id))
            await db.execute("UPDATE tasks SET target_url = ? WHERE campaign_id = ? AND type = 'reddit_comment';", (post_url, campaign_id))
            await db.commit()

    async def get_campaign_tasks(self, campaign_id: str) -> list[dict[str, Any]]:
        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT * FROM tasks WHERE campaign_id = ? ORDER BY type DESC, comment_index ASC;", (campaign_id,)) as cursor:
                rows = await cursor.fetchall()
                return [dict(r) for r in rows]


# ─── MODULE-LEVEL INSTANCE & PROXY FUNCTIONS ──────────────────────────────────

_active_db: DatabaseProvider | None = None


def get_db() -> DatabaseProvider:
    global _active_db
    if _active_db is None:
        url = settings.database_url
        if url and (url.startswith("postgresql://") or url.startswith("postgres://")):
            logger.info("Booting Campaign Database in POSTGRESQL Mode (Neon AWS Pooler)")
            _active_db = PostgresProvider(url)
        else:
            logger.info("Booting Campaign Database in LOCAL SQLITE Mode")
            _active_db = SQLiteProvider()
    return _active_db


async def init_db() -> None:
    await get_db().init_db()


async def close() -> None:
    if _active_db:
        await _active_db.close()


async def execute(query: str, *args: Any) -> None:
    await get_db().execute(query, *args)


async def fetch(query: str, *args: Any) -> list[dict[str, Any]]:
    return await get_db().fetch(query, *args)


async def fetchrow(query: str, *args: Any) -> dict[str, Any] | None:
    return await get_db().fetchrow(query, *args)


async def get_user(discord_id: str) -> dict[str, Any] | None:
    return await get_db().get_user(discord_id)


async def register_user(discord_id: str) -> dict[str, Any]:
    return await get_db().register_user(discord_id)


async def update_user_reddit(discord_id: str, reddit_username: str, verified: bool = True) -> bool:
    return await get_db().update_user_reddit(discord_id, reddit_username, verified)


async def get_user_by_reddit(reddit_username: str) -> dict[str, Any] | None:
    return await get_db().get_user_by_reddit(reddit_username)


async def get_user_by_referral(code: str) -> dict[str, Any] | None:
    return await get_db().get_user_by_referral(code)


async def update_user_wallet(discord_id: str, method: str, value: str, network: str | None = None) -> None:
    await get_db().update_user_wallet(discord_id, method, value, network)


async def set_user_flag(discord_id: str, flagged: bool, reason: str | None = None) -> None:
    await get_db().set_user_flag(discord_id, flagged, reason)


async def toggle_user_digest(discord_id: str, enabled: bool) -> None:
    await get_db().toggle_user_digest(discord_id, enabled)


async def apply_referral(referee_id: str, code: str) -> bool:
    return await get_db().apply_referral(referee_id, code)


async def get_task(task_id: str) -> dict[str, Any] | None:
    return await get_db().get_task(task_id)


async def create_task(
    task_id: str,
    task_type: str,
    reward: float,
    slots_total: int,
    time_limit: int,
    hold_hours: int,
    min_trust: int = 0,
    cooldown_minutes: int = 0,
    requires_image: bool = False,
    target_url: str = "",
    campaign_id: str | None = None,
) -> None:
    await get_db().create_task(
        task_id, task_type, reward, slots_total, time_limit, hold_hours,
        min_trust, cooldown_minutes, requires_image, target_url, campaign_id
    )


async def list_open_tasks() -> list[dict[str, Any]]:
    return await get_db().list_open_tasks()


async def get_active_claim(discord_id: str) -> dict[str, Any] | None:
    return await get_db().get_active_claim(discord_id)


async def get_user_last_claim_time(discord_id: str, task_type: str) -> str | None:
    return await get_db().get_user_last_claim_time(discord_id, task_type)


async def claim_task(discord_id: str, task_id: str, time_limit_minutes: int) -> int | None:
    return await get_db().claim_task(discord_id, task_id, time_limit_minutes)


async def submit_proof(claim_id: int, discord_id: str, proof_url: str, screenshot_url: str | None = None) -> int:
    return await get_db().submit_proof(claim_id, discord_id, proof_url, screenshot_url)


async def update_submission_status(submission_id: int, status: str, hold_hours: int = 0, reason: str | None = None) -> None:
    await get_db().update_submission_status(submission_id, status, hold_hours, reason)


async def request_withdrawal(discord_id: str, amount: float, method: str, info: str) -> int | None:
    return await get_db().request_withdrawal(discord_id, amount, method, info)


async def get_pending_withdrawals() -> list[dict[str, Any]]:
    return await get_db().get_pending_withdrawals()


async def finalize_withdrawal(withdrawal_id: int, admin_id: str) -> None:
    await get_db().finalize_withdrawal(withdrawal_id, admin_id)


async def create_campaign(campaign_id: str, subreddit: str, title: str, content: str, keyword: str | None = None) -> None:
    await get_db().create_campaign(campaign_id, subreddit, title, content, keyword)


async def get_campaign(campaign_id: str) -> dict[str, Any] | None:
    return await get_db().get_campaign(campaign_id)


async def update_campaign_post_url(campaign_id: str, post_url: str) -> None:
    await get_db().update_campaign_post_url(campaign_id, post_url)


async def get_campaign_tasks(campaign_id: str) -> list[dict[str, Any]]:
    return await get_db().get_campaign_tasks(campaign_id)


async def get_admin_user(username: str) -> dict[str, Any] | None:
    return await get_db().get_admin_user(username)


async def create_admin_user(username: str, password_hash: str, role: str, email: str | None = None) -> None:
    await get_db().create_admin_user(username, password_hash, role, email)


async def set_user_web_password(discord_id: str, password_hash: str) -> None:
    await get_db().set_user_web_password(discord_id, password_hash)


async def generate_web_login_token(discord_id: str) -> str:
    return await get_db().generate_web_login_token(discord_id)

