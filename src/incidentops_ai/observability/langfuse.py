"""Spans to Langfuse's public ingestion API, without the SDK (AIENG-67).

POST {host}/api/public/ingestion with basic auth (public key, secret key) and a batch of events:
a `trace-create` for each root span, a `generation-create` for model spans (with model and token
usage), a `span-create` for the rest. Spans are buffered and sent on `flush()` (the app flushes
when a root span ends and at shutdown). Langfuse answers 207 with per-event errors; those are
logged, never raised. Tested against a fake ingestion server, not a live Langfuse.
"""

import base64
import logging
import uuid
from datetime import UTC, datetime

import httpx

from incidentops_ai.observability.tracing import Span, estimate_cost_usd

logger = logging.getLogger(__name__)


def _iso(ts: float | None) -> str | None:
    return None if ts is None else datetime.fromtimestamp(ts, UTC).isoformat()


def to_events(span: Span) -> list[dict]:
    now = datetime.now(UTC).isoformat()
    events = []
    if span.parent_id is None:
        events.append(
            {
                "id": uuid.uuid4().hex,
                "type": "trace-create",
                "timestamp": now,
                "body": {"id": span.trace_id, "name": span.name, "metadata": span.attributes},
            }
        )
    body = {
        "id": span.span_id,
        "traceId": span.trace_id,
        "parentObservationId": span.parent_id,
        "name": span.name,
        "startTime": _iso(span.start),
        "endTime": _iso(span.end),
        "metadata": {**span.attributes, "kind": span.kind},
        "level": "ERROR" if span.status == "error" else "DEFAULT",
        "statusMessage": span.error,
    }
    if span.kind == "model":
        a = span.attributes
        body |= {
            "model": a.get("model"),
            "usage": {
                "input": a.get("input_tokens"),
                "output": a.get("output_tokens"),
                "unit": "TOKENS",
            },
            "costDetails": {
                "total": estimate_cost_usd(a.get("input_tokens"), a.get("output_tokens"))
            },
        }
    events.append(
        {
            "id": uuid.uuid4().hex,
            "type": "generation-create" if span.kind == "model" else "span-create",
            "timestamp": now,
            "body": body,
        }
    )
    return events


class LangfuseExporter:
    def __init__(
        self,
        host: str,
        public_key: str,
        secret_key: str,
        *,
        transport: httpx.BaseTransport | None = None,
        timeout_s: float = 5.0,
    ) -> None:
        auth = base64.b64encode(f"{public_key}:{secret_key}".encode()).decode()
        self._http = httpx.Client(
            base_url=host.rstrip("/"),
            headers={"Authorization": f"Basic {auth}"},
            transport=transport,
            timeout=timeout_s,
        )
        self._buffer: list[dict] = []
        self.failed_events = 0

    def export(self, span: Span) -> None:
        self._buffer.extend(to_events(span))
        if span.parent_id is None:
            self.flush()

    def flush(self) -> None:
        if not self._buffer:
            return
        batch, self._buffer = self._buffer, []
        try:
            response = self._http.post("/api/public/ingestion", json={"batch": batch})
        except httpx.HTTPError as error:
            self.failed_events += len(batch)
            logger.warning("Langfuse ingestion failed: %s", type(error).__name__)
            return
        if response.status_code >= 400:
            self.failed_events += len(batch)
            logger.warning("Langfuse ingestion refused the batch: HTTP %s", response.status_code)
            return
        errors = (response.json() or {}).get("errors", []) if response.content else []
        if errors:
            self.failed_events += len(errors)
            logger.warning("Langfuse rejected %d events", len(errors))

    def close(self) -> None:
        self.flush()
        self._http.close()
