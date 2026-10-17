"""Spans for workflow runs, steps, model calls, retrieval and tools (AIENG-67).

`with get_tracer().span("step.diagnose", kind="step") as span:` opens a span whose parent is the
current one; the current span lives in a context variable, so concurrent branches (asyncio tasks
copy the context they start in) get the right parent. A root span can name its trace id: a
workflow run uses its run id, so its start, decision and resume are one trace.

Exporting never breaks the request: an exporter that fails is logged and skipped. Without
configuration the tracer is a no-op.
"""

import contextvars
import json
import logging
import threading
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal, Protocol

logger = logging.getLogger(__name__)

Kind = Literal["workflow", "step", "model", "retrieval", "tool"]
ASSUMED_USD_PER_M = {"input": 2.50, "output": 10.00}  # the assumption used since W06


def estimate_cost_usd(input_tokens: int | None, output_tokens: int | None) -> float:
    return round(
        (
            (input_tokens or 0) * ASSUMED_USD_PER_M["input"]
            + (output_tokens or 0) * ASSUMED_USD_PER_M["output"]
        )
        / 1e6,
        6,
    )


@dataclass
class Span:
    trace_id: str
    span_id: str
    parent_id: str | None
    name: str
    kind: Kind
    start: float
    end: float | None = None
    status: Literal["ok", "error"] = "ok"
    error: str | None = None  # exception type only, never its message
    attributes: dict[str, Any] = field(default_factory=dict)

    def set(self, **attributes: Any) -> None:
        self.attributes.update({k: v for k, v in attributes.items() if v is not None})

    @property
    def duration_ms(self) -> float:
        return round(((self.end or self.start) - self.start) * 1000, 1)


class Exporter(Protocol):
    def export(self, span: Span) -> None: ...


class MemoryExporter:
    def __init__(self) -> None:
        self.spans: list[Span] = []

    def export(self, span: Span) -> None:
        self.spans.append(span)


class JsonlExporter:
    """One finished span per line, appended (INCIDENTOPS_TRACE_FILE)."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._lock = threading.Lock()

    def export(self, span: Span) -> None:
        line = json.dumps(asdict(span), default=str)
        with self._lock, self.path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")


_current: contextvars.ContextVar[Span | None] = contextvars.ContextVar("span", default=None)


class Tracer:
    def __init__(self, exporters: list[Exporter] | None = None) -> None:
        self.exporters = list(exporters or [])

    @property
    def enabled(self) -> bool:
        return bool(self.exporters)

    @contextmanager
    def span(
        self,
        name: str,
        *,
        kind: Kind,
        trace_id: str | None = None,
        ok_exceptions: tuple[type[BaseException], ...] = (),
        **attributes: Any,
    ) -> Iterator[Span]:
        """`ok_exceptions` end the span without an error: LangGraph pauses a run by raising."""
        parent = _current.get()
        span = Span(
            trace_id=trace_id or (parent.trace_id if parent else uuid.uuid4().hex),
            span_id=uuid.uuid4().hex[:16],
            parent_id=parent.span_id if parent and not trace_id else None,
            name=name,
            kind=kind,
            start=time.time(),
        )
        span.set(**attributes)
        token = _current.set(span)
        try:
            yield span
        except ok_exceptions:
            span.set(paused=True)
            raise
        except BaseException as error:
            span.status, span.error = "error", type(error).__name__
            raise
        finally:
            _current.reset(token)
            span.end = time.time()
            if self.enabled:
                for exporter in self.exporters:
                    try:
                        exporter.export(span)
                    except Exception:  # tracing must never break the request
                        logger.exception("Trace exporter %s failed", type(exporter).__name__)


_tracer = Tracer()


def get_tracer() -> Tracer:
    return _tracer


def set_tracer(tracer: Tracer) -> None:
    global _tracer
    _tracer = tracer
