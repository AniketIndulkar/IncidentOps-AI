# Python asyncio, from the ground up

**Core idea:** asyncio gives you *concurrency on one thread*. Tasks take turns, and they only hand over control at `await` points. It's great when you spend most of your time *waiting* (network, DB, timers) and useless for heavy computation.

If you know Kotlin coroutines, the mapping is close: `async def` ≈ `suspend fun`, `Task` ≈ `Job`/`Deferred`, `TaskGroup` ≈ `coroutineScope`, and `to_thread` ≈ `withContext(Dispatchers.IO)`. The big difference is that calling an `async def` function does *not* start anything.

---

## 1. `async def` and `await`

```python
import asyncio


async def fetch(n: int) -> int:
    await asyncio.sleep(1)  # pretend this is network I/O
    return n * 2


async def main():
    result = await fetch(21)
    print(result)  # 42


asyncio.run(main())  # the entry point: starts the event loop
```

- `async def` defines a **coroutine function**. Calling it returns a **coroutine object** and runs *nothing* yet.
- `await x` means: "run `x` to completion, and while it's waiting, let others run."
- `await` only works inside `async def`.

```python
coro = fetch(1)
print(type(coro))  # <class 'coroutine'>, nothing has executed
# forgetting `await` gives "coroutine was never awaited" warnings
```

**Gotcha:** `await a(); await b()` is still *sequential*. `await` doesn't make things concurrent; Tasks do (section 4).

---

## 2. The event loop

The event loop is a scheduler running on one thread. Conceptually:

```
while there is work:
    pick a ready task
    run it until it hits an `await` that can't finish immediately
    park it; when its I/O/timer is ready, mark it runnable again
```

- `asyncio.run(main())` creates the loop, runs `main()`, then closes the loop.
- Only **one** piece of Python code runs at a time. Switches happen *only* at `await`.
- That's why it's called **cooperative** multitasking: a task that never awaits never gives anyone else a turn (see section 7).

---

## 3. Coroutine vs. Task

| | Coroutine | Task |
|---|---|---|
| What is it | A paused-at-start function call | A coroutine **wrapped and scheduled** on the loop |
| Starts running | When you `await` it | Soon after creation (next time the loop gets control) |
| Concurrent with others | No, it runs inline in the awaiter | Yes |
| Has `.cancel()`, `.done()`, `.result()` | No | Yes |

```python
async def main():
    # Sequential, ~2s total
    await fetch(1)
    await fetch(2)

    # Concurrent, ~1s total
    t1 = asyncio.create_task(fetch(1))
    t2 = asyncio.create_task(fetch(2))
    print(await t1, await t2)
```

---

## 4. `asyncio.create_task()`

Schedules a coroutine to run in the background and returns a `Task`.

```python
async def main():
    task = asyncio.create_task(fetch(5))
    print("task is running in the background...")
    await asyncio.sleep(0.1)  # yields control; task gets to start
    result = await task  # wait for the result
```

Things to know:

- **Keep a reference** to tasks. The loop only holds weak references, so a fire-and-forget task can be garbage-collected mid-flight.
  ```python
  background = set()
  t = asyncio.create_task(work())
  background.add(t)
  t.add_done_callback(background.discard)
  ```
- **Unawaited exceptions are easy to lose.** If a task fails and nobody awaits it, you only get a "Task exception was never retrieved" log at GC time.
- **Cancellation:** `task.cancel()` raises `asyncio.CancelledError` inside the task at its next `await`. Don't swallow it; re-raise it after cleanup.

---

## 5. `gather()` vs. `TaskGroup`

### `asyncio.gather()`: the classic

```python
results = await asyncio.gather(fetch(1), fetch(2), fetch(3))
# [2, 4, 6], in the order you passed them, not completion order
```

Error behavior (the tricky part):
- Default: the **first exception propagates** to you, but the *other tasks keep running* in the background.
- `return_exceptions=True`: exceptions are returned in the results list instead of raised.

```python
results = await asyncio.gather(a(), b(), return_exceptions=True)
for r in results:
    if isinstance(r, Exception):
        ...
```

### `asyncio.TaskGroup` (Python 3.11+): the modern, structured way

```python
async def main():
    async with asyncio.TaskGroup() as tg:
        t1 = tg.create_task(fetch(1))
        t2 = tg.create_task(fetch(2))
    # leaving the `async with` block waits for ALL tasks
    print(t1.result(), t2.result())
```

Why prefer it:
- **Structured concurrency:** no task outlives the block.
- If any task fails, the group **cancels the siblings** and raises an `ExceptionGroup`.
- It keeps references to tasks for you, so there's no GC gotcha.

```python
try:
    async with asyncio.TaskGroup() as tg:
        tg.create_task(might_fail())
        tg.create_task(other())
except* ValueError as eg:  # note `except*`
    print("value errors:", eg.exceptions)
```

**Rule of thumb:** default to `TaskGroup`. Use `gather(..., return_exceptions=True)` when you want "run all, collect every result/error, don't cancel anything."

Bonus: `async with asyncio.timeout(5): ...` (3.11+) cancels the block if it takes too long.

---

## 6. Async context managers

Like `with`, but setup and teardown can `await`. Used for connections, sessions, locks, transactions.

```python
class Connection:
    async def __aenter__(self):
        await asyncio.sleep(0.1)  # e.g. open connection
        print("opened")
        return self

    async def __aexit__(self, exc_type, exc, tb):
        await asyncio.sleep(0.1)  # e.g. close connection
        print("closed")  # runs even on exceptions


async def main():
    async with Connection() as conn:
        ...
```

The shortcut with a decorator:

```python
from contextlib import asynccontextmanager


@asynccontextmanager
async def connection():
    print("opened")
    try:
        yield "conn"
    finally:
        print("closed")


async with connection() as c:
    ...
```

Related: `async for` iterates over **async iterators/generators** (`async def` with `yield`), e.g. streaming responses.

Real examples: `aiohttp.ClientSession()`, `asyncio.Lock()`, `asyncio.Semaphore()`, and `TaskGroup` itself.

---

## 7. What happens when blocking code runs in the event loop

Since everything shares one thread, **a blocking call freezes every task.**

```python
import time


async def heartbeat():
    while True:
        print("tick")
        await asyncio.sleep(0.5)


async def bad():
    time.sleep(3)  # BLOCKS the whole loop for 3s


async def main():
    async with asyncio.TaskGroup() as tg:
        tg.create_task(heartbeat())
        tg.create_task(bad())
        await asyncio.sleep(5)
```

You'll see a tick or so, then **silence for 3 seconds**, then ticking resumes. Swap in `await asyncio.sleep(3)` and the heartbeat keeps going.

Common offenders:
- `time.sleep()`, `requests.get()`, sync DB drivers, `open().read()` on big files
- CPU-heavy loops (parsing, hashing, image processing, big JSON)
- Anything with a long stretch of Python code and no `await`

Consequences in real services: timeouts, a stalled server, health checks failing, latency spikes for *every* request.

**Finding it:** run `asyncio.run(main(), debug=True)`, which logs "Executing ... took 3.002 seconds" for slow callbacks.

---

## 8. When `ThreadPoolExecutor` is useful

It's your escape hatch when you must call **blocking, synchronous code** from async code without freezing the loop.

```python
# Simplest: asyncio.to_thread (3.9+)
import requests


async def main():
    resp = await asyncio.to_thread(requests.get, "https://example.com")
    print(resp.status_code)


# Run many blocking calls concurrently
results = await asyncio.gather(*(asyncio.to_thread(requests.get, u) for u in urls))
```

With your own pool (to cap concurrency or isolate workloads):

```python
from concurrent.futures import ThreadPoolExecutor

pool = ThreadPoolExecutor(max_workers=4)


async def main():
    loop = asyncio.get_running_loop()
    data = await loop.run_in_executor(pool, blocking_fn, arg)
```

**Good fit:**
- Sync-only libraries (`requests`, legacy DB drivers, some SDKs)
- Blocking file or disk I/O
- Wrapping a sync call you can't easily rewrite

**Bad fit:**
- **CPU-bound work.** Threads share the GIL, so there's no speedup (and you may still starve the loop). Use `ProcessPoolExecutor` via `run_in_executor`, or a separate service/worker.
- Anything that already has a native async library (prefer `aiohttp`/`httpx` over `requests` in a thread).

**Caveats:**
- Cancelling the awaiting task does **not** stop the thread; it keeps running until it finishes.
- Your code now runs on multiple threads, so thread-safety matters for shared state. Don't touch loop objects from inside the worker thread.

---

## Cheat sheet

| Situation | Use |
|---|---|
| Run one async thing | `await coro()` |
| Run several concurrently, all must succeed | `async with TaskGroup()` |
| Run several, collect all outcomes | `gather(..., return_exceptions=True)` |
| Background work | `create_task()` + **keep a reference** |
| Need a time limit | `async with asyncio.timeout(n)` |
| Sync blocking call | `await asyncio.to_thread(fn, ...)` |
| CPU-heavy work | `ProcessPoolExecutor` / separate process |
| Need to limit concurrency | `asyncio.Semaphore(n)` |
