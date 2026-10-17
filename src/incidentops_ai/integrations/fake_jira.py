"""A fake of the small part of the Jira REST API the adapter uses, with switches for failures.

Supports `GET /rest/api/3/search?jql=` (clauses `labels = "x"` and `text ~ "word"`, joined by AND),
`GET /rest/api/3/issue/{key}` and `POST /rest/api/3/issue`. Tests drive it in process through
`httpx.ASGITransport`; `requests` records what reached it.
"""

import asyncio
import re
from dataclasses import dataclass

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse

_CLAUSE = re.compile(r'(labels|text)\s*(=|~)\s*"([^"]*)"')


@dataclass
class Seen:
    method: str
    path: str


class FakeJira:
    def __init__(self, *, token: str, project: str = "OPS") -> None:
        self.token = token
        self.project = project
        self.issues: dict[str, dict] = {}
        self.requests: list[Seen] = []
        self._failures: list[tuple[int, str | None, str]] = []
        self._malformed = 0
        self._post_delay = 0.0
        self.app = self._build()

    # --- switches -------------------------------------------------------------------------------

    def fail_next(self, *statuses: int, retry_after: str | None = None, body: str = "error"):
        self._failures += [(status, retry_after, body) for status in statuses]

    def malformed_next(self) -> None:
        self._malformed += 1

    def delay_next_post(self, seconds: float) -> None:
        """The next create stores the issue, then waits before answering."""
        self._post_delay = seconds

    def add(self, *, summary: str, description: str = "", labels: list[str] | None = None) -> str:
        key = f"{self.project}-{len(self.issues) + 1}"
        self.issues[key] = {
            "summary": summary,
            "description": description,
            "labels": labels or [],
            "status": "Open",
        }
        return key

    # --- the API --------------------------------------------------------------------------------

    def _issue(self, key: str) -> dict:
        issue = self.issues[key]
        return {
            "id": str(10000 + int(key.split("-")[1])),
            "key": key,
            "fields": {
                "summary": issue["summary"],
                "description": issue["description"],
                "labels": issue["labels"],
                "status": {"name": issue["status"]},
            },
        }

    def _matches(self, issue: dict, jql: str) -> bool:
        for field, _, value in _CLAUSE.findall(jql):
            if field == "labels" and value not in issue["labels"]:
                return False
            text = f"{issue['summary']} {issue['description']}".lower()
            if field == "text" and value.lower() not in text:
                return False
        return True

    def _build(self) -> FastAPI:
        app = FastAPI()

        @app.middleware("http")
        async def gate(request: Request, call_next):
            self.requests.append(Seen(request.method, request.url.path))
            if request.headers.get("authorization") != f"Bearer {self.token}":
                return JSONResponse({"errorMessages": ["unauthorized"]}, status_code=401)
            if self._failures:
                status, retry_after, body = self._failures.pop(0)
                headers = {"Retry-After": retry_after} if retry_after else {}
                return Response(body, status_code=status, headers=headers)
            if self._malformed:
                self._malformed -= 1
                return Response("{not json", media_type="application/json")
            return await call_next(request)

        @app.get("/rest/api/3/search")
        async def search(jql: str = "", maxResults: int = 50):
            keys = [k for k, issue in self.issues.items() if self._matches(issue, jql)]
            issues = [self._issue(k) for k in reversed(keys)][:maxResults]
            return {"issues": issues, "total": len(keys)}

        @app.get("/rest/api/3/issue/{key}")
        async def get(key: str):
            if key not in self.issues:
                return JSONResponse({"errorMessages": ["Issue does not exist"]}, status_code=404)
            return self._issue(key)

        @app.post("/rest/api/3/issue", status_code=201)
        async def create(payload: dict):
            fields = payload["fields"]
            key = self.add(
                summary=fields["summary"],
                description=fields.get("description", ""),
                labels=fields.get("labels", []),
            )
            if self._post_delay:
                delay, self._post_delay = self._post_delay, 0.0
                await asyncio.sleep(delay)
            return {"id": self._issue(key)["id"], "key": key, "self": f"/rest/api/3/issue/{key}"}

        return app
