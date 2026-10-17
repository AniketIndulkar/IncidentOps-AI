"""The ticket system's own storage: SQLite in the server process, as another system would have.

Creation is idempotent on `idempotency_key`: asking twice with the same key returns the first
ticket (and says it was not created), so a retry after a timeout never makes a second ticket.
"""

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

SEED = Path(__file__).resolve().parents[3] / "data" / "tickets" / "seed.json"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS tickets (
    number INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    body TEXT NOT NULL,
    service TEXT NOT NULL,
    severity TEXT NOT NULL,
    status TEXT NOT NULL,
    idempotency_key TEXT UNIQUE,
    approved_by TEXT,
    created_at TEXT NOT NULL
)
"""


def _ticket(row: sqlite3.Row) -> dict:
    data = dict(row)
    data["id"] = f"TCK-{data.pop('number'):04d}"
    return data


class TicketStore:
    def __init__(self, path: Path) -> None:
        self._db = sqlite3.connect(path)
        self._db.row_factory = sqlite3.Row
        self._db.execute(_SCHEMA)
        if self._db.execute("SELECT count(*) FROM tickets").fetchone()[0] == 0:
            for seed in json.loads(SEED.read_text()):
                self._insert(**seed, idempotency_key=None, approved_by=None)
        self._db.commit()

    def _insert(self, **fields) -> int:
        cursor = self._db.execute(
            "INSERT INTO tickets (title, body, service, severity, status, idempotency_key, "
            "approved_by, created_at) VALUES (:title, :body, :service, :severity, :status, "
            ":idempotency_key, :approved_by, :created_at)",
            {**fields, "created_at": datetime.now(UTC).isoformat()},
        )
        return cursor.lastrowid

    def search(self, query: str, *, service: str | None = None, limit: int = 5) -> list[dict]:
        """Tickets whose title or body contains every word of the query, newest first."""
        clauses, params = [], []
        for word in query.lower().split():
            clauses.append("(lower(title) LIKE ? OR lower(body) LIKE ?)")
            params += [f"%{word}%", f"%{word}%"]
        if service:
            clauses.append("service = ?")
            params.append(service)
        where = " AND ".join(clauses) or "1 = 1"
        rows = self._db.execute(
            f"SELECT * FROM tickets WHERE {where} ORDER BY number DESC LIMIT ?",
            [*params, limit],
        )
        return [_ticket(r) for r in rows]

    def get(self, ticket_id: str) -> dict | None:
        try:
            number = int(ticket_id.removeprefix("TCK-"))
        except ValueError:
            return None
        row = self._db.execute("SELECT * FROM tickets WHERE number = ?", (number,)).fetchone()
        return _ticket(row) if row else None

    def create(
        self,
        *,
        title: str,
        body: str,
        service: str,
        severity: str,
        idempotency_key: str,
        approved_by: str,
    ) -> tuple[dict, bool]:
        existing = self._db.execute(
            "SELECT * FROM tickets WHERE idempotency_key = ?", (idempotency_key,)
        ).fetchone()
        if existing:
            return _ticket(existing), False
        number = self._insert(
            title=title,
            body=body,
            service=service,
            severity=severity,
            status="open",
            idempotency_key=idempotency_key,
            approved_by=approved_by,
        )
        self._db.commit()
        row = self._db.execute("SELECT * FROM tickets WHERE number = ?", (number,)).fetchone()
        return _ticket(row), True

    def count(self) -> int:
        return self._db.execute("SELECT count(*) FROM tickets").fetchone()[0]
