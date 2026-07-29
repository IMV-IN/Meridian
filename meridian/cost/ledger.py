"""Cost / token ledger — memory or sqlite, query by org/team/day.

# ponytail: separate from budget UsageMeter (caps vs finance reports).
"""

from __future__ import annotations

import sqlite3
import threading
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple


@dataclass(frozen=True)
class CostRow:
    org_id: str
    team_id: str
    model: str
    day: str  # UTC YYYY-MM-DD
    prompt_tokens: int
    completion_tokens: int
    requests: int
    cost: float
    key_id: str = ""  # non-secret key id; "" for rows from pre-0.12 or unauthed traffic


def _day(now: Optional[datetime] = None) -> str:
    n = now or datetime.now(timezone.utc)
    return n.astimezone(timezone.utc).strftime("%Y-%m-%d")


class CostLedger:
    def record(
        self,
        *,
        org_id: str,
        team_id: str,
        model: str,
        prompt_tokens: int,
        completion_tokens: int,
        cost: float,
        key_id: str = "",
        now: Optional[datetime] = None,
    ) -> None:
        raise NotImplementedError

    def query(
        self,
        *,
        org_id: Optional[str] = None,
        team_id: Optional[str] = None,
        key_id: Optional[str] = None,
        window_days: int = 30,
        now: Optional[datetime] = None,
    ) -> List[CostRow]:
        raise NotImplementedError


class InMemoryCostLedger(CostLedger):
    def __init__(self) -> None:
        self._lock = threading.Lock()
        # (org, team, model, key_id, day) -> [prompt, completion, requests, cost]
        self._data: Dict[Tuple[str, str, str, str, str], List[float]] = defaultdict(
            lambda: [0.0, 0.0, 0.0, 0.0]
        )

    def record(
        self,
        *,
        org_id: str,
        team_id: str,
        model: str,
        prompt_tokens: int,
        completion_tokens: int,
        cost: float,
        key_id: str = "",
        now: Optional[datetime] = None,
    ) -> None:
        key = (org_id or "", team_id or "", model or "", key_id or "", _day(now))
        with self._lock:
            row = self._data[key]
            row[0] += prompt_tokens
            row[1] += completion_tokens
            row[2] += 1
            row[3] += cost

    def query(
        self,
        *,
        org_id: Optional[str] = None,
        team_id: Optional[str] = None,
        key_id: Optional[str] = None,
        window_days: int = 30,
        now: Optional[datetime] = None,
    ) -> List[CostRow]:
        # ponytail: filter by day string sort; fine for small ledgers
        today = _day(now)
        with self._lock:
            items = list(self._data.items())
        out: List[CostRow] = []
        for (o, t, m, k, d), vals in items:
            if org_id is not None and o != org_id:
                continue
            if team_id is not None and t != team_id:
                continue
            if key_id is not None and k != key_id:
                continue
            # window: keep last window_days calendar days by string compare ok for ISO dates
            if window_days > 0 and d < _day_offset(today, -(window_days - 1)):
                continue
            out.append(
                CostRow(
                    org_id=o,
                    team_id=t,
                    model=m,
                    day=d,
                    key_id=k,
                    prompt_tokens=int(vals[0]),
                    completion_tokens=int(vals[1]),
                    requests=int(vals[2]),
                    cost=float(vals[3]),
                )
            )
        out.sort(key=lambda r: (r.day, r.org_id, r.team_id, r.model, r.key_id))
        return out


def _day_offset(day: str, delta: int) -> str:
    from datetime import timedelta

    base = datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    return (base + timedelta(days=delta)).strftime("%Y-%m-%d")


class SqliteCostLedger(CostLedger):
    def __init__(self, path: str) -> None:
        self._conn = sqlite3.connect(path, check_same_thread=False)
        # Enterprise: WAL improves concurrent readers under request write load.
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._lock = threading.Lock()
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        """Create the table; migrate pre-0.12 schemas by rebuilding.

        Pre-0.12 tables have PK (org, team, model, day) and no key_id. The
        rebuild carries old rows over with key_id = '' so historical sums are
        preserved while new rows split per key.

        The rebuild runs as ONE transaction (BEGIN IMMEDIATE … COMMIT): a
        crash mid-copy rolls everything back to the pre-0.12 table instead of
        stranding rows in an orphaned staging table. It is also resumable —
        a leftover ``cost_ledger_v1`` (from an older non-transactional run)
        means a prior migration died partway; INSERT OR IGNORE restarts the
        copy idempotently (PK collisions skip already-copied rows).
        """
        with self._lock:
            tables = {
                r[0]
                for r in self._conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            has_ledger = "cost_ledger" in tables
            has_v1 = "cost_ledger_v1" in tables
            cols = (
                {r[1] for r in self._conn.execute("PRAGMA table_info(cost_ledger)")}
                if has_ledger
                else set()
            )
            modern = has_ledger and "key_id" in cols

            if has_ledger and not modern and has_v1:
                raise RuntimeError(
                    "cost_ledger schema is in an inconsistent state: both the "
                    "legacy table and a cost_ledger_v1 staging table exist. "
                    "Merge them manually (or drop cost_ledger_v1) and restart."
                )

            if not has_v1 and (not has_ledger or modern):
                self._create_table()
                self._conn.commit()
                return

            try:
                self._conn.execute("BEGIN IMMEDIATE")
                if has_ledger and not modern:
                    self._conn.execute(
                        "ALTER TABLE cost_ledger RENAME TO cost_ledger_v1"
                    )
                self._create_table()
                self._conn.execute(
                    """
                    INSERT OR IGNORE INTO cost_ledger
                    (org_id, team_id, model, day, key_id,
                     prompt_tokens, completion_tokens, requests, cost)
                    SELECT org_id, team_id, model, day, '',
                           prompt_tokens, completion_tokens, requests, cost
                    FROM cost_ledger_v1
                    """
                )
                self._conn.execute("DROP TABLE cost_ledger_v1")
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise

    def _create_table(self) -> None:
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS cost_ledger (
                org_id TEXT NOT NULL,
                team_id TEXT NOT NULL,
                model TEXT NOT NULL,
                day TEXT NOT NULL,
                key_id TEXT NOT NULL DEFAULT '',
                prompt_tokens REAL NOT NULL DEFAULT 0,
                completion_tokens REAL NOT NULL DEFAULT 0,
                requests REAL NOT NULL DEFAULT 0,
                cost REAL NOT NULL DEFAULT 0,
                PRIMARY KEY (org_id, team_id, model, day, key_id)
            )
            """
        )

    def record(
        self,
        *,
        org_id: str,
        team_id: str,
        model: str,
        prompt_tokens: int,
        completion_tokens: int,
        cost: float,
        key_id: str = "",
        now: Optional[datetime] = None,
    ) -> None:
        day = _day(now)
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO cost_ledger
                (org_id, team_id, model, day, key_id,
                 prompt_tokens, completion_tokens, requests, cost)
                VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?)
                ON CONFLICT(org_id, team_id, model, day, key_id) DO UPDATE SET
                    prompt_tokens = prompt_tokens + excluded.prompt_tokens,
                    completion_tokens = completion_tokens + excluded.completion_tokens,
                    requests = requests + 1,
                    cost = cost + excluded.cost
                """,
                (
                    org_id or "",
                    team_id or "",
                    model or "",
                    day,
                    key_id or "",
                    float(prompt_tokens),
                    float(completion_tokens),
                    float(cost),
                ),
            )
            self._conn.commit()

    def query(
        self,
        *,
        org_id: Optional[str] = None,
        team_id: Optional[str] = None,
        key_id: Optional[str] = None,
        window_days: int = 30,
        now: Optional[datetime] = None,
    ) -> List[CostRow]:
        today = _day(now)
        start = _day_offset(today, -(window_days - 1)) if window_days > 0 else "0000-01-01"
        sql = """
            SELECT org_id, team_id, model, day, key_id,
                   prompt_tokens, completion_tokens, requests, cost
            FROM cost_ledger
            WHERE day >= ?
        """
        args: list = [start]
        if org_id is not None:
            sql += " AND org_id = ?"
            args.append(org_id)
        if team_id is not None:
            sql += " AND team_id = ?"
            args.append(team_id)
        if key_id is not None:
            sql += " AND key_id = ?"
            args.append(key_id)
        sql += " ORDER BY day, org_id, team_id, model, key_id"
        with self._lock:
            rows = self._conn.execute(sql, args).fetchall()
        return [
            CostRow(
                org_id=r[0],
                team_id=r[1],
                model=r[2],
                day=r[3],
                key_id=r[4],
                prompt_tokens=int(r[5]),
                completion_tokens=int(r[6]),
                requests=int(r[7]),
                cost=float(r[8]),
            )
            for r in rows
        ]
