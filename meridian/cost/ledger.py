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
        now: Optional[datetime] = None,
    ) -> None:
        raise NotImplementedError

    def query(
        self,
        *,
        org_id: Optional[str] = None,
        team_id: Optional[str] = None,
        window_days: int = 30,
        now: Optional[datetime] = None,
    ) -> List[CostRow]:
        raise NotImplementedError


class InMemoryCostLedger(CostLedger):
    def __init__(self) -> None:
        self._lock = threading.Lock()
        # (org, team, model, day) -> [prompt, completion, requests, cost]
        self._data: Dict[Tuple[str, str, str, str], List[float]] = defaultdict(
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
        now: Optional[datetime] = None,
    ) -> None:
        key = (org_id or "", team_id or "", model or "", _day(now))
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
        window_days: int = 30,
        now: Optional[datetime] = None,
    ) -> List[CostRow]:
        # ponytail: filter by day string sort; fine for small ledgers
        today = _day(now)
        with self._lock:
            items = list(self._data.items())
        out: List[CostRow] = []
        for (o, t, m, d), vals in items:
            if org_id is not None and o != org_id:
                continue
            if team_id is not None and t != team_id:
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
                    prompt_tokens=int(vals[0]),
                    completion_tokens=int(vals[1]),
                    requests=int(vals[2]),
                    cost=float(vals[3]),
                )
            )
        out.sort(key=lambda r: (r.day, r.org_id, r.team_id, r.model))
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
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS cost_ledger (
                org_id TEXT NOT NULL,
                team_id TEXT NOT NULL,
                model TEXT NOT NULL,
                day TEXT NOT NULL,
                prompt_tokens REAL NOT NULL DEFAULT 0,
                completion_tokens REAL NOT NULL DEFAULT 0,
                requests REAL NOT NULL DEFAULT 0,
                cost REAL NOT NULL DEFAULT 0,
                PRIMARY KEY (org_id, team_id, model, day)
            )
            """
        )
        self._conn.commit()
        self._lock = threading.Lock()

    def record(
        self,
        *,
        org_id: str,
        team_id: str,
        model: str,
        prompt_tokens: int,
        completion_tokens: int,
        cost: float,
        now: Optional[datetime] = None,
    ) -> None:
        day = _day(now)
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO cost_ledger
                (org_id, team_id, model, day, prompt_tokens, completion_tokens, requests, cost)
                VALUES (?, ?, ?, ?, ?, ?, 1, ?)
                ON CONFLICT(org_id, team_id, model, day) DO UPDATE SET
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
        window_days: int = 30,
        now: Optional[datetime] = None,
    ) -> List[CostRow]:
        today = _day(now)
        start = _day_offset(today, -(window_days - 1)) if window_days > 0 else "0000-01-01"
        sql = """
            SELECT org_id, team_id, model, day,
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
        sql += " ORDER BY day, org_id, team_id, model"
        with self._lock:
            rows = self._conn.execute(sql, args).fetchall()
        return [
            CostRow(
                org_id=r[0],
                team_id=r[1],
                model=r[2],
                day=r[3],
                prompt_tokens=int(r[4]),
                completion_tokens=int(r[5]),
                requests=int(r[6]),
                cost=float(r[7]),
            )
            for r in rows
        ]
