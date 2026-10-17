# Gateway fallback with a real model (17 Oct 2026)

`uv run python -m incidentops_ai.llm.gateway_check`: the route was
`["simulated-primary", "gpt-6-luna"]`; the simulated primary fails every call with a provider error
before any request is sent.

```
status=succeeded model=gpt-6-luna fallback_from=['simulated-primary'] tokens=48/12
```

One real call (48 input, 12 output tokens). The breaker, the rate limit and the statuses that must
not fall back are covered by `tests/test_gateway.py` with scripted models.
