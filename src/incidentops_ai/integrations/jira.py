"""A Jira-style ticket adapter (AIENG-55), following docs/notes/integration-contracts.md.

Reads (`search`, `get`) are retried on 429 (after `Retry-After`) and on 5xx or transport errors, at
most three attempts. A create is never retried: whether it happened is unknown after a failure, so
`JiraTicketSink` searches for the run's label first and creates only when nothing is there. Errors
carry a kind and a status, never the token or a response body.
"""

import asyncio
from collections.abc import Awaitable, Callable
from typing import Literal

import httpx
from pydantic import BaseModel, ValidationError

ErrorKind = Literal[
    "unauthorized",
    "not_found",
    "rate_limited",
    "unavailable",
    "invalid_response",
    "rejected",
    "not_approved",
]
RETRYABLE = {429, 500, 502, 503, 504}
MAX_RETRY_AFTER_S = 10.0


class JiraError(Exception):
    def __init__(self, kind: ErrorKind, status: int | None, operation: str) -> None:
        super().__init__(
            f"jira {operation} failed: {kind}" + (f" (HTTP {status})" if status else "")
        )
        self.kind, self.status, self.operation = kind, status, operation


class _Status(BaseModel):
    name: str


class _Fields(BaseModel):
    summary: str
    description: str | None = None
    labels: list[str] = []
    status: _Status


class _ApiIssue(BaseModel):
    key: str
    fields: _Fields


class _SearchResult(BaseModel):
    issues: list[_ApiIssue]


class _Created(BaseModel):
    key: str


class JiraIssue(BaseModel):
    key: str
    summary: str
    description: str | None
    labels: list[str]
    status: str

    @classmethod
    def from_api(cls, issue: _ApiIssue) -> "JiraIssue":
        f = issue.fields
        return cls(
            key=issue.key,
            summary=f.summary,
            description=f.description,
            labels=f.labels,
            status=f.status.name,
        )


def _kind(status: int) -> ErrorKind:
    return {401: "unauthorized", 403: "unauthorized", 404: "not_found", 429: "rate_limited"}.get(
        status, "unavailable" if status >= 500 else "rejected"
    )


class JiraClient:
    def __init__(
        self,
        base_url: str,
        token: str,
        *,
        project: str = "OPS",
        transport: httpx.AsyncBaseTransport | None = None,
        timeout_s: float = 5.0,
        max_attempts: int = 3,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self.project = project
        self._http = httpx.AsyncClient(
            base_url=base_url,
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
            transport=transport,
            timeout=timeout_s,
        )
        self._timeout_s = timeout_s
        self._max_attempts = max_attempts
        self._sleep = sleep

    async def aclose(self) -> None:
        await self._http.aclose()

    async def _send(self, operation: str, method: str, path: str, *, retry: bool, **kwargs):
        attempts = self._max_attempts if retry else 1
        for attempt in range(1, attempts + 1):
            try:
                # httpx enforces timeouts on network transports; this bound holds for any transport.
                async with asyncio.timeout(self._timeout_s):
                    response = await self._http.request(method, path, **kwargs)
            except (httpx.TransportError, TimeoutError):
                if attempt == attempts:
                    raise JiraError("unavailable", None, operation) from None
                await self._sleep(0.2 * 2 ** (attempt - 1))
                continue
            if response.status_code < 400:
                try:
                    return response.json()
                except ValueError:
                    raise JiraError("invalid_response", response.status_code, operation) from None
            if response.status_code not in RETRYABLE or attempt == attempts:
                raise JiraError(_kind(response.status_code), response.status_code, operation)
            if response.status_code == 429 and (after := response.headers.get("Retry-After")):
                try:
                    wait = min(float(after), MAX_RETRY_AFTER_S)
                except ValueError:
                    wait = 1.0
            else:
                wait = 0.2 * 2 ** (attempt - 1)
            await self._sleep(wait)
        raise AssertionError("unreachable")  # pragma: no cover

    @staticmethod
    def _parse(model: type[BaseModel], data, operation: str):
        try:
            return model.model_validate(data)
        except ValidationError:
            raise JiraError("invalid_response", None, operation) from None

    async def search(self, jql: str, *, max_results: int = 20) -> list[JiraIssue]:
        data = await self._send(
            "search",
            "GET",
            "/rest/api/3/search",
            retry=True,
            params={"jql": jql, "maxResults": max_results},
        )
        result = self._parse(_SearchResult, data, "search")
        return [JiraIssue.from_api(i) for i in result.issues]

    async def get(self, key: str) -> JiraIssue:
        data = await self._send("get", "GET", f"/rest/api/3/issue/{key}", retry=True)
        return JiraIssue.from_api(self._parse(_ApiIssue, data, "get"))

    async def create(self, *, summary: str, description: str, labels: list[str]) -> str:
        """One attempt only. Returns the new issue's key."""
        payload = {
            "fields": {
                "project": {"key": self.project},
                "issuetype": {"name": "Incident"},
                "summary": summary[:255],
                "description": description,
                "labels": labels,
            }
        }
        data = await self._send("create", "POST", "/rest/api/3/issue", retry=False, json=payload)
        return self._parse(_Created, data, "create").key


class JiraTicketSink:
    """The workflow's ticket write through Jira: approved, labelled with the run, created once."""

    def __init__(self, client: JiraClient) -> None:
        self._client = client

    async def create(self, proposal: dict, *, run_id: str, decision: dict) -> dict:
        if decision.get("decision") != "approve":
            raise JiraError("not_approved", None, "create")
        label = f"incidentops-run-{run_id}"
        existing = await self._client.search(f'labels = "{label}"', max_results=1)
        if existing:
            issue, created = existing[0], False
        else:
            description = (
                f"{proposal['body']}\n\nApproved by {decision['reviewer']} "
                f"(decision {decision['decision_id']}) in IncidentOps run {run_id}."
            )
            key = await self._client.create(
                summary=proposal["title"],
                description=description,
                labels=[label, f"service-{proposal['service']}"],
            )
            issue, created = await self._client.get(key), True
        return {
            "id": issue.key,
            "created": created,
            "title": issue.summary,
            "status": issue.status,
            "approved_by": decision["reviewer"],
            "system": "jira",
        }
