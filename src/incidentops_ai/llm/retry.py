"""Bounded retry around any LLMProvider.

Three limits apply together, so a call can never run away:
- attempts:   at most `config.max_attempts` tries
- time:       at most `config.total_timeout_s` across all tries and waits
- bad output: at most one extra try for a reply that was invalid or cut off

What is retried:
- transient provider trouble (timeout, rate limit, 5xx/network): yes, with backoff
- bad output (invalid, truncated): once
- our own mistakes and refusals (rejected, refused): never; a retry cannot fix them
"""

import asyncio
import random
import time
from collections.abc import Awaitable, Callable, Sequence

from pydantic import BaseModel

from incidentops_ai.llm.provider import (
    LLMProvider,
    LLMStatus,
    ModelConfig,
    Prompt,
    StructuredResult,
    ToolOutput,
    ToolSpec,
    ToolTurn,
)

BASE_DELAY_S = 0.5
MAX_DELAY_S = 8.0
MAX_RETRY_AFTER_S = 30.0  # if the provider asks us to wait longer than this, give up instead

_TRANSIENT = {LLMStatus.TIMED_OUT, LLMStatus.RATE_LIMITED, LLMStatus.PROVIDER_ERROR}
_BAD_OUTPUT = {LLMStatus.INVALID_OUTPUT, LLMStatus.TRUNCATED}


class RetryingProvider:
    def __init__(
        self,
        inner: LLMProvider,
        *,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        clock: Callable[[], float] = time.monotonic,
        rand: Callable[[], float] = random.random,
    ) -> None:
        self._inner = inner
        self._sleep = sleep
        self._clock = clock
        self._rand = rand

    async def generate_structured[T: BaseModel](
        self, *, prompt: Prompt, schema: type[T], config: ModelConfig
    ) -> StructuredResult[T]:
        return await self._run(
            config,
            lambda cfg: self._inner.generate_structured(prompt=prompt, schema=schema, config=cfg),
        )

    async def tool_call(
        self,
        *,
        prompt: Prompt,
        tools: Sequence[ToolSpec],
        config: ModelConfig,
        transcript: list[dict] | None = None,
        outputs: Sequence[ToolOutput] = (),
    ) -> ToolTurn:
        # Safe to repeat: a turn only asks the model what to do next; it runs no tool itself.
        return await self._run(
            config,
            lambda cfg: self._inner.tool_call(
                prompt=prompt, tools=tools, config=cfg, transcript=transcript, outputs=outputs
            ),
        )

    async def _run[R: (StructuredResult, ToolTurn)](
        self, config: ModelConfig, attempt: Callable[[ModelConfig], Awaitable[R]]
    ) -> R:
        deadline = self._clock() + config.total_timeout_s
        attempt_config = config
        bad_output_retry_used = False
        statuses: list[LLMStatus] = []
        latency_ms = 0
        input_tokens: int | None = None
        output_tokens: int | None = None

        while True:
            # Never let one attempt outlive the overall budget.
            remaining = deadline - self._clock()
            if remaining < attempt_config.timeout_s:
                attempt_config = attempt_config.model_copy(
                    update={"timeout_s": max(remaining, 0.1)}
                )

            result = await attempt(attempt_config)
            statuses.append(result.status)
            latency_ms += result.metadata.latency_ms
            input_tokens = _add(input_tokens, result.metadata.input_tokens)
            output_tokens = _add(output_tokens, result.metadata.output_tokens)

            delay = self._delay_before_retry(result, len(statuses), bad_output_retry_used)
            if (
                delay is None
                or len(statuses) >= config.max_attempts
                or self._clock() + delay >= deadline
            ):
                break

            if result.status in _BAD_OUTPUT:
                bad_output_retry_used = True
                if result.status is LLMStatus.TRUNCATED:
                    attempt_config = attempt_config.model_copy(
                        update={"max_output_tokens": attempt_config.max_output_tokens * 2}
                    )
            if delay > 0:
                await self._sleep(delay)

        metadata = result.metadata.model_copy(
            update={
                "attempts": len(statuses),
                "attempt_statuses": statuses,
                "latency_ms": latency_ms,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
            }
        )
        return result.model_copy(update={"metadata": metadata})

    def _delay_before_retry(
        self, result: StructuredResult | ToolTurn, attempts_made: int, bad_output_retry_used: bool
    ) -> float | None:
        """Seconds to wait before trying again, or None if this result must not be retried."""
        if result.status in _TRANSIENT:
            if result.retry_after_s is not None:
                return result.retry_after_s if result.retry_after_s <= MAX_RETRY_AFTER_S else None
            # Exponential backoff with jitter: 0.5s, 1s, 2s, ... capped, each scaled to 50-100%
            # so many clients retrying at once do not all hit the provider at the same instant.
            ceiling = min(BASE_DELAY_S * 2 ** (attempts_made - 1), MAX_DELAY_S)
            return ceiling * (0.5 + 0.5 * self._rand())
        if result.status in _BAD_OUTPUT and not bad_output_retry_used:
            return 0.0  # the provider is healthy; no reason to wait
        return None


def _add(total: int | None, more: int | None) -> int | None:
    if more is None:
        return total
    return (total or 0) + more
