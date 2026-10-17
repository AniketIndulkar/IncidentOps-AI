"""A real fallback, seen to work (H47): the primary route is a simulated outage.

    uv run python -m incidentops_ai.llm.gateway_check

One real model call. The route is ["simulated-primary", <configured model>]; the simulated
primary fails with a provider error before any request is sent, so the configured model must
answer, and the metadata must say it fell back.
"""

import asyncio

from pydantic import BaseModel, ConfigDict

from incidentops_ai.config import Settings
from incidentops_ai.llm.factory import build_model_config, build_provider
from incidentops_ai.llm.fake_provider import Fail, FakeProvider
from incidentops_ai.llm.gateway import Gateway
from incidentops_ai.llm.provider import LLMStatus, Prompt


class Ack(BaseModel):
    model_config = ConfigDict(extra="forbid")  # strict structured output requires it

    ok: bool


class Outage:
    """Fails every call for `down`, passes the rest to the real provider."""

    def __init__(self, inner, down: str) -> None:
        self._inner, self._down = inner, down

    async def generate_structured(self, *, prompt, schema, config):
        if config.model == self._down:
            return await FakeProvider([Fail(LLMStatus.PROVIDER_ERROR)]).generate_structured(
                prompt=prompt, schema=schema, config=config
            )
        return await self._inner.generate_structured(prompt=prompt, schema=schema, config=config)

    async def aclose(self):
        await self._inner.aclose()


async def main() -> None:
    settings = Settings()
    config = build_model_config(settings)
    provider = Outage(build_provider(settings), "simulated-primary")
    gateway = Gateway(provider, {"health_check": ["simulated-primary", config.model]})
    prompt = Prompt(
        name="health_check", version="v1", instructions="Answer ok: true.", input="Are you there?"
    )
    try:
        result = await gateway.generate_structured(prompt=prompt, schema=Ack, config=config)
    finally:
        await gateway.aclose()
    print(
        f"status={result.status.value} model={result.metadata.model} "
        f"fallback_from={result.metadata.fallback_from} "
        f"tokens={result.metadata.input_tokens}/{result.metadata.output_tokens}"
    )


if __name__ == "__main__":
    asyncio.run(main())
