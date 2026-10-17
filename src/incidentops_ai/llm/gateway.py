"""The one door every model call goes through (AIENG-66).

Each task (named after its prompt) has an ordered route of models; a task without one uses the
configured model. A call falls back to the next model only on a failure another model could fix
(timeout, rate limit, provider error), never on invalid output, truncation, refusal or a rejected
request: another model would only hide those. Per model, a circuit breaker skips a model after
`breaker_threshold` consecutive retryable failures for `breaker_cooldown_s`, then lets one trial
call decide; and an optional rate limit hands calls to the next model once a model has had its
share of the last minute. The answering model is in the result's metadata, with the ones that
failed before it in `fallback_from`.
"""

import time
from collections import deque
from collections.abc import Callable

from incidentops_ai.llm.provider import LLMProvider, LLMStatus, ModelConfig, Prompt
from incidentops_ai.observability.tracing import estimate_cost_usd, get_tracer

RETRYABLE = frozenset({LLMStatus.TIMED_OUT, LLMStatus.RATE_LIMITED, LLMStatus.PROVIDER_ERROR})
TASKS = {
    "extract_incident": "extraction",
    "diagnose_incident": "diagnosis",
    "transcribe_image": "transcription",
    "answer_from_text": "benchmark",
    "answer_from_image": "benchmark",
    "related_incidents": "related_incidents",
    "text_to_sql": "text_to_sql",
    "judge_claim": "judge",
}


def task_of(prompt_name: str) -> str:
    return TASKS.get(prompt_name, prompt_name)


class _Health:
    def __init__(self) -> None:
        self.failures = 0
        self.open_until: float | None = None
        self.calls: deque[float] = deque()


class Gateway:
    def __init__(
        self,
        inner: LLMProvider,
        routes: dict[str, list[str]] | None = None,
        *,
        breaker_threshold: int = 3,
        breaker_cooldown_s: float = 30.0,
        per_model_per_minute: int | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._inner = inner
        self._routes = {task: list(models) for task, models in (routes or {}).items()}
        self._threshold = breaker_threshold
        self._cooldown = breaker_cooldown_s
        self._per_minute = per_model_per_minute
        self._clock = clock
        self._health: dict[str, _Health] = {}

    def route(self, prompt: Prompt, config: ModelConfig) -> list[str]:
        return (
            self._routes.get(task_of(prompt.name))
            or self._routes.get(prompt.name)
            or [config.model]
        )

    def models(self, default: str) -> list[str]:
        """Every model any route can use, the configured default included."""
        seen = [default]
        for models in self._routes.values():
            seen += [m for m in models if m not in seen]
        return seen

    def open_models(self) -> list[str]:
        now = self._clock()
        return sorted(m for m, h in self._health.items() if h.open_until and now < h.open_until)

    def _available(self, model: str) -> bool:
        health = self._health.setdefault(model, _Health())
        now = self._clock()
        if health.open_until is not None and now < health.open_until:
            return False  # breaker open
        if self._per_minute is not None:
            while health.calls and now - health.calls[0] >= 60:
                health.calls.popleft()
            if len(health.calls) >= self._per_minute:
                return False
        return True

    def _record(self, model: str, status: LLMStatus) -> None:
        health = self._health.setdefault(model, _Health())
        health.calls.append(self._clock())
        if status in RETRYABLE:
            health.failures += 1
            if health.failures >= self._threshold:
                health.open_until = self._clock() + self._cooldown
        else:
            health.failures, health.open_until = 0, None  # any real answer closes the breaker

    async def _through(self, call, prompt: Prompt, config: ModelConfig):
        tried: list[str] = []
        result = None
        models = self.route(prompt, config)
        candidates = [m for m in models if self._available(m)] or models[-1:]
        for model in candidates:
            with get_tracer().span(
                f"model.{task_of(prompt.name)}",
                kind="model",
                model=model,
                prompt=f"{prompt.name}/{prompt.version}",
                fallback_from=tried[:] or None,
            ) as span:
                result = await call(config.model_copy(update={"model": model}))
                meta = result.metadata
                span.set(
                    outcome=result.status.value,
                    input_tokens=meta.input_tokens,
                    output_tokens=meta.output_tokens,
                    attempts=meta.attempts,
                    cost_usd=estimate_cost_usd(meta.input_tokens, meta.output_tokens),
                )
                if result.status is not LLMStatus.SUCCEEDED:
                    span.status, span.error = "error", result.status.value
            self._record(model, result.status)
            result = result.model_copy(
                update={"metadata": result.metadata.model_copy(update={"fallback_from": tried[:]})}
            )
            if result.status not in RETRYABLE:
                return result
            tried.append(model)
        return result

    async def generate_structured(self, *, prompt: Prompt, schema, config: ModelConfig):
        return await self._through(
            lambda c: self._inner.generate_structured(prompt=prompt, schema=schema, config=c),
            prompt,
            config,
        )

    async def tool_call(
        self, *, prompt: Prompt, tools, config: ModelConfig, transcript=None, outputs=()
    ):
        return await self._through(
            lambda c: self._inner.tool_call(
                prompt=prompt, tools=tools, config=c, transcript=transcript, outputs=outputs
            ),
            prompt,
            config,
        )

    async def aclose(self) -> None:
        await self._inner.aclose()
