"""Where does a burst of simultaneous requests spend its time? (W12, found after AIENG-69)

    INCIDENTOPS_DATABASE_URL=<scratch database> uv run python -m incidentops_ai.reliability.burst

Bursts of 1, 25 and 100 simultaneous requests, on warmed connections, of two kinds: `GET /health`
(no database, no work) and `POST /incidents` (one insert), against 1 and then 2 uvicorn workers.
If `/health` is slow in a burst, the time goes to queueing on the event loop, not to the database;
if a second worker halves it, the queue is CPU.
"""

import asyncio
import os
import time
from datetime import UTC, datetime
from pathlib import Path

import httpx

from incidentops_ai.reliability.load import TOKEN, _free_port, _pct, start_server, wait_until_up

BODY = {
    "title": "Orders API timing out",
    "description": "Writes time out after 30 seconds.",
    "service": "orders-api",
    "severity": "SEV2",
    "environment": "production",
}


async def _burst(client: httpx.AsyncClient, n: int, method: str, url: str, **kw) -> tuple:
    async def one() -> float:
        started = time.perf_counter()
        response = await client.request(method, url, **kw)
        response.raise_for_status()
        return (time.perf_counter() - started) * 1000

    await asyncio.gather(*(client.get("/health") for _ in range(n)))  # warm n connections
    ms = await asyncio.gather(*(one() for _ in range(n)))
    return _pct(ms, 0.5), _pct(ms, 0.95)


async def run(database_url: str) -> list[dict]:
    rows = []
    for workers in (1, 2):
        port = _free_port()
        server = start_server(database_url, Path(os.devnull), port, workers=workers)
        try:
            await wait_until_up(f"http://127.0.0.1:{port}")
            async with httpx.AsyncClient(
                base_url=f"http://127.0.0.1:{port}",
                headers={"Authorization": f"Bearer {TOKEN}"},
                limits=httpx.Limits(max_connections=200),
                timeout=60,
            ) as client:
                for n in (1, 25, 100):
                    health = await _burst(client, n, "GET", "/health")
                    create = await _burst(client, n, "POST", "/incidents", json=BODY)
                    rows.append(
                        {"workers": workers, "burst": n, "health": health, "create": create}
                    )
        finally:
            server.terminate()
            server.wait(timeout=10)
    return rows


def render(rows: list[dict]) -> str:
    lines = [
        f"# Request bursts ({datetime.now(UTC).date()})",
        "",
        "Warmed connections; the load generator and the API share one laptop.",
        "",
        "| Workers | Burst | /health P50 / P95 ms | POST /incidents P50 / P95 ms |",
        "|---|---|---|---|",
    ]
    for r in rows:
        lines.append(
            f"| {r['workers']} | {r['burst']} | {r['health'][0]} / {r['health'][1]} | "
            f"{r['create'][0]} / {r['create'][1]} |"
        )
    return "\n".join(lines) + "\n"


def main() -> None:
    rows = asyncio.run(run(os.environ["INCIDENTOPS_DATABASE_URL"]))
    out = Path(f"evals/reliability/runs/{datetime.now(UTC).date()}-burst.md")
    out.write_text(render(rows))
    print(render(rows))


if __name__ == "__main__":
    main()
