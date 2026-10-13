# LLM provider layer: design (AIENG-19)

Written 13 Oct 2026. Design only; implementation starts in AIENG-20.

**Core idea:** the app never talks to OpenAI directly. It talks to a small interface we own
(`LLMProvider`). One class implements it with the OpenAI SDK; another is a fake for tests.

```
extraction service ──▶ LLMProvider (our interface)
                          ├─ OpenAIProvider   real calls, used when running the app
                          └─ FakeProvider     scripted answers and failures, used in tests and CI
```

Why bother:

1. **Tests cost nothing and need no key.** CI runs on a public repo with no secrets.
2. **Failures become ours to define.** Every vendor error is translated into a small set of our own
   failure states (AIENG-21), so the rest of the app never sees SDK exceptions.
3. **Swappable.** W11 replaces the provider with a gateway (LiteLLM); only this layer changes.
4. **One place for metadata.** Model, prompt version, tokens, latency and attempts are recorded
   the same way for every call (needed Thursday, and for W11 tracing).

Kotlin comparison: a repository interface with a real and a fake implementation.

---

## 1. Two patterns this week uses

**Structured output.** Instead of asking "please reply in JSON" and hoping, we give the model a
schema and the API constrains the reply to match it. We pass a Pydantic class; we get back an
instance of that class.

**Tool calling.** We describe functions the model may ask us to run. The model does not run
anything: it returns "call `get_recent_incidents` with these arguments". Our code validates the
arguments, runs the function, and sends the result back. The model may ask for several calls at
once (parallel tool calls).

---

## 2. What the OpenAI SDK gives us

Checked against the installed package `openai` **3.29.0** (not from memory).

| Need | SDK fact |
|---|---|
| Async client | `AsyncOpenAI(api_key=..., project=..., timeout=..., max_retries=...)` |
| Structured output | `responses.parse(..., text_format=MyPydanticModel)` exists, but we use `responses.create(..., text={"format": {type: json_schema, strict: true, schema}})` and validate `response.output_text` ourselves (see the note below) |
| Output cap | `max_output_tokens=` |
| Finished or not | `response.status`: `completed`, `incomplete`, `failed`, …; `response.incomplete_details.reason`: `max_output_tokens`, `content_filter`, … |
| Refusal | an output content part of type `refusal` (`ResponseOutputRefusal`) instead of text |
| Tools | function tools with `name`, `description`, `parameters` (JSON Schema), `strict`; helper `openai.pydantic_function_tool(Model)` builds one from a Pydantic class |
| Tool calls returned | output items of type function call with `name`, `arguments` (a JSON **string**), `call_id` |
| Parallel tool calls | request parameter `parallel_tool_calls` |
| Token usage | `response.usage.input_tokens`, `output_tokens`, `total_tokens` |
| SDK retries | default **2** (`DEFAULT_MAX_RETRIES`) |
| SDK timeout | default **600 s** read, 5 s connect |
| HTTP library | `httpx2`; tests can use `httpx2.MockTransport` to simulate HTTP failures |

**Exceptions the SDK raises** (all under `openai.OpenAIError`):

| Exception | Meaning | Retry? |
|---|---|---|
| `APITimeoutError` | No answer in time | Yes |
| `APIConnectionError` | Network problem | Yes |
| `RateLimitError` (429) | Too many requests, or out of quota | Yes for rate; **no** if quota is exhausted |
| `InternalServerError` (5xx) | Provider-side failure | Yes |
| `ConflictError` (409) | Transient conflict | Yes |
| `BadRequestError` (400), `UnprocessableEntityError` (422) | Our request is wrong | No |
| `AuthenticationError` (401), `PermissionDeniedError` (403) | Key problem | No |
| `NotFoundError` (404) | Wrong model name | No |
| `LengthFinishReasonError` | Output cut off at the token cap while parsing | Once, with a higher cap, at most |
| `ContentFilterFinishReasonError` | Output blocked by the content filter | No |

**Found while implementing (AIENG-20):** `responses.parse` validates the reply *before* returning,
so a reply cut off at the token cap raises a validation error and the response (status, token
usage, request id) is lost. Calling `responses.create` with a strict JSON Schema and validating
ourselves lets us tell `truncated` from `invalid_output` and keep the metadata for failed replies.
The provider also checks that the schema is strict-compatible (all fields required, no extras)
before sending it.

**Two more decisions that follow from this:**

- We set **`max_retries=0`** on the SDK client and do retries in our own layer. Otherwise the SDK
  retries silently and we cannot count attempts, test them, or cap total time.
- We set an explicit **timeout of 30 s** per attempt. The 600 s default is far too long for an API
  request path.

---

## 3. The interface

```python
class LLMProvider(Protocol):
    async def generate_structured(
        self,
        *,
        prompt: Prompt,                 # versioned instructions + the user's input text
        schema: type[T],                # a Pydantic model class
        config: ModelConfig,
    ) -> StructuredResult[T]: ...

    async def tool_call(
        self,
        *,
        prompt: Prompt,
        tools: Sequence[ToolSpec],      # name, description, Pydantic argument model
        config: ModelConfig,
        prior_results: Sequence[ToolResult] = (),   # results of earlier calls, to continue the turn
    ) -> ToolCallResult: ...
```

`Protocol` is Python's version of an interface: any class with these methods fits, no inheritance needed.

### Inputs

```python
class ModelConfig(BaseModel):          # frozen
    model: str                          # from INCIDENTOPS_LLM_MODEL
    max_output_tokens: int = 2000
    timeout_s: float = 30.0             # per attempt
    max_attempts: int = 3               # total tries, including the first
    reasoning_effort: str | None = None # passed through only if set

class Prompt(BaseModel):               # frozen
    name: str                           # e.g. "extract_incident"
    version: str                        # e.g. "v1"
    instructions: str                   # the system-level text, loaded from a file
    input: str                          # the incident text

class ToolSpec(BaseModel):
    name: str
    description: str
    arguments: type[BaseModel]          # Pydantic model = the argument schema
```

Per-task config: extraction and (later) diagnosis can use different models and caps without code changes.

### Outputs

Both results carry the same metadata, and neither ever raises a vendor exception.

```python
class RunMetadata(BaseModel):
    provider: str                       # "openai" | "fake"
    model: str                          # model that actually answered
    prompt_name: str
    prompt_version: str
    attempts: int
    latency_ms: int                     # total, across attempts
    input_tokens: int | None
    output_tokens: int | None
    request_id: str | None              # for support tickets and tracing

class StructuredResult(BaseModel, Generic[T]):
    status: LLMStatus
    value: T | None                     # set only when status is SUCCEEDED
    error_detail: str | None            # short, safe to log; never the raw incident text
    metadata: RunMetadata

class ToolCallResult(BaseModel):
    status: LLMStatus
    calls: list[ToolCall]               # name, validated arguments, call_id
    rejected: list[RejectedToolCall]    # unknown tool or invalid arguments, with the reason
    final_text: str | None              # set when the model answered without (more) tool calls
    metadata: RunMetadata
```

### Failure states

```python
class LLMStatus(StrEnum):
    SUCCEEDED = "succeeded"
    INVALID_OUTPUT = "invalid_output"   # reply did not match the schema, or failed our validators
    TRUNCATED = "truncated"             # hit max_output_tokens
    REFUSED = "refused"                 # model declined, or content filter
    TIMED_OUT = "timed_out"             # every attempt timed out
    RATE_LIMITED = "rate_limited"       # still 429 after all attempts
    PROVIDER_ERROR = "provider_error"   # 5xx or network, after all attempts
    REJECTED = "rejected"               # our request was wrong: 400, 401, 403, 404; never retried
```

**Rule: a result is a value, not an exception.** Callers must check `status`. A failed call returns
`value=None`; it never returns partly filled or guessed data ("no coercion", AIENG-21).

### Retry rule (implemented in AIENG-21)

| Failure | Action |
|---|---|
| Timeout, connection error, 429 (rate), 5xx, 409 | Retry with exponential backoff and jitter, honour `Retry-After`, stop at `max_attempts` |
| Truncated or invalid output | One extra attempt at most |
| 400, 401, 403, 404, 422, quota exhausted, refusal | No retry |

---

## 4. Where things live

```
src/incidentops_ai/llm/
  provider.py         LLMProvider protocol, ModelConfig, Prompt, results, LLMStatus
  openai_provider.py  OpenAIProvider: the only file that imports `openai`
  fake_provider.py    FakeProvider: scripted replies and failures for tests
  retry.py            bounded retry with backoff (AIENG-21)
  prompts.py          loads prompts/<name>/<version>.md
src/incidentops_ai/prompts/
  extract_incident/v1.md   inside the package, so it ships in the Docker image
```

**Rule:** `import openai` appears in exactly one file. If another file needs it, the abstraction has leaked.

## 5. Prompts and versions

- A prompt is a file: `src/incidentops_ai/prompts/extract_incident/v1.md`.
- A change to the text means a **new file** (`v2.md`), never an edit to `v1.md`.
- Every run records `prompt_name` + `prompt_version` + `model`, so results can be compared later
  (W11 evals) and a regression can be traced to the change that caused it.

## 6. Configuration and secrets

| Setting | Env var | Notes |
|---|---|---|
| API key | `OPENAI_API_KEY` | In `.env` only. Loaded by `Settings` as a `SecretStr` (hidden in logs and reprs) and passed to the client once |
| Model | `INCIDENTOPS_LLM_MODEL` | No default in code: startup fails clearly if it is missing |
| Timeout | `INCIDENTOPS_LLM_TIMEOUT_S` | Default 30 |
| Max attempts | `INCIDENTOPS_LLM_MAX_ATTEMPTS` | Default 3 |

The provider is created once at app startup (like the DB engine) and injected with `Depends`, so
tests swap in `FakeProvider` the same way they swap the database.

## 7. Privacy and logging

- Incident text may contain secrets or personal data. **Never log prompts or model output** at INFO;
  log metadata only (status, tokens, latency, request id).
- `error_detail` holds a short category message, not vendor error bodies that might echo the input.
- Requests are sent with response storage off unless there is a reason to keep them.

## 8. Still to decide

| Question | When |
|---|---|
| **Which model** (from OpenAI's official pricing page; start mid or small tier) | Before the first real call in AIENG-20 |
| Token cap for extraction (2000 is a first guess) | After running the 10 samples |
| Whether `reasoning_effort` helps extraction | After running the 10 samples |
| Fallback model on provider outage | ADR-001, Friday |

## 9. Reading list for this ticket

- *AI Engineering* (Chip Huyen): the sections on model APIs / inference interfaces and on
  structured outputs.
- OpenAI docs: Structured Outputs, Function calling, Error codes, Rate limits, and the Pricing page.
