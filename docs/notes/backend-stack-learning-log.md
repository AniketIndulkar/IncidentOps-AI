# IncidentOps AI — Backend learning log

Date: 10 October 2026

## 1. What this application currently does

IncidentOps AI currently records and retrieves software/service incidents: API outages, elevated errors, slow responses, payment failures, growing queues, or data loss. Reports identify the affected service, severity, and environment: production, staging, or development.

An engineer or monitoring system can submit a report through the API. Authentication is not implemented yet.

- `POST /incidents`: create an incident.
- `GET /incidents/{id}`: retrieve one by UUID.
- `/health`: report that the API process is responding; it does not check database connectivity.
- `/docs`: interactive API documentation.

The server assigns the UUID, timestamps, and initial `open` status. There is no frontend, listing endpoint, or status-update endpoint yet. AI extraction, evidence retrieval, diagnosis, and human approval workflows are future roadmap work.

## 2. Libraries and their responsibilities

| Library | Purpose in this project |
|---|---|
| FastAPI | HTTP routing, dependency injection, and API documentation. |
| Pydantic | Input/output models and runtime data validation. |
| pydantic-core | The validation engine used by Pydantic. |
| pydantic-settings | Load and validate application configuration. |
| Uvicorn | Listen for HTTP requests and serve the FastAPI app. |
| SQLAlchemy | ORM mapping, queries, sessions, and transactions. |
| asyncpg | Asynchronous communication with PostgreSQL. |
| Alembic | Versioned database schema migrations. |
| pytest | Automated tests. |
| pytest-asyncio | Support asynchronous tests. |
| HTTPX | Send API requests in tests. |
| Ruff | Linting and formatting. |
| uv_build | Build the Python package. |

Supporting tools: uv manages dependencies; PostgreSQL stores data and executes SQL; Docker Compose runs the API and database; GitHub Actions runs linting, tests, and image builds. AWS Lightsail deployment is documented as a manual process.

## 3. FastAPI and Uvicorn

FastAPI defines what happens when a request reaches an endpoint. Its built-in validation uses Pydantic, which is installed as a dependency. Invalid incident inputs produce HTTP `422` responses.

Uvicorn makes the app reachable over the network: it receives requests, calls FastAPI, and sends responses back. It is an ASGI server, where ASGI is the standard interface between the server and the Python app. Alternatives include Hypercorn, Daphne, and Granian.

The project starts the development server with:

```bash
uvicorn app.main:app --app-dir apps/api --reload
```

`--reload` restarts the development server when code changes.

## 4. Pydantic models, types, and settings

Pydantic validates values against Python model definitions, not against database tables. It can be used without a database.

- `Severity` and `Environment` inherit from `StrEnum`: they resemble Kotlin enum classes, with string values.
- `IncidentCreate` resembles a Kotlin data class with additional runtime validation and serialization.
- `BaseModel` provides field validation, conversion of compatible inputs, validation errors, dictionary/JSON serialization, and schema generation.
- `Annotated` attaches metadata to a type; Pydantic reads that metadata to enforce constraints.

```python
Title = Annotated[str, Field(min_length=1, max_length=200)]
```

This declares a string with a length of 1–200 characters. `Annotated` itself does not validate anything.

Pydantic Settings is a separate library built on Pydantic. It reads configuration from environment variables and `.env`, validates it, and creates a Python `Settings` object. In this app, variables use the `INCIDENTOPS_` prefix.

```python
settings = Settings()
engine = create_async_engine(
    settings.database_url,
    echo=settings.database_echo,
)
```

`database_echo` controls SQLAlchemy's SQL logging. The database URL tells SQLAlchemy how to connect. Pydantic does not need a database URL itself; the app chooses to load that setting through Pydantic Settings. The URL is declared as a string, so validation does not prove that it is a valid or working database connection.

Both libraries run in the same Python process. Configuration values live in the settings object and are explicitly passed through function arguments; there is no automatic communication between the libraries.

Pydantic, pydantic-core, and pydantic-settings are open source under the MIT license.

## 5. SQLAlchemy and the Room comparison

SQLAlchemy maps Python objects to relational database rows and provides query-building tools. In this app, `IncidentRow` maps to the `incidents` table, and `IncidentRepository` performs database operations.

| Room concept | Similar concept here |
|---|---|
| `@Entity` | SQLAlchemy mapped class such as `IncidentRow`. |
| DAO | `IncidentRepository`, an application-defined persistence boundary. |
| Transaction | Session transaction with commit/rollback. |
| SQLite | PostgreSQL in this app; SQLAlchemy also supports SQLite and other databases. |

Room bundles mapping, DAO generation, and migration support for local SQLite use. This Python stack exposes those responsibilities through separate libraries. Room still relies on a database driver and SQLite engine underneath. It does not replace HTTP input validation or arbitrary business rules.

This modular setup allows libraries to be replaced independently, at the cost of more setup. Python does not require this arrangement: frameworks such as Django bundle an ORM and migrations.

## 6. SQLAlchemy sessions and concurrent updates

A session tracks ORM objects and coordinates database work. Each HTTP request in this app gets its own session.

- **Identity map:** retrieving the same row within one session gives the same tracked Python object.
- **Change tracking:** changing a loaded ORM object's fields can generate an `UPDATE` when changes are flushed.
- **Unit of work:** coordinates pending additions, modifications, and deletions.
- **Flush:** sends pending SQL inside the current transaction; it does not commit.
- **Commit:** flushes pending work and commits the transaction.
- **Rollback:** reverses the transaction's database changes.

For example, changing a loaded `IncidentRow.title` and committing can persist the change without a separate update method. This tracking applies to mapped ORM objects, not to arbitrary Pydantic models. Room also supports transactions; SQLAlchemy's tracked object lifecycle is the distinctive session capability discussed here.

Separate sessions have separate objects. Under PostgreSQL's default Read Committed isolation, if both sessions change the same title, a later successful update can overwrite the earlier one. Overlapping writes wait on row locks, but that does not automatically detect stale edits.

We added a planned improvement to the roadmap: before introducing incident updates, add SQLAlchemy optimistic locking with a version counter, require the client's expected version, return structured HTTP `409` conflicts for stale updates, and test the behaviour with two independent sessions. Clients should reload and reconcile a conflict rather than blindly retry.

## 7. asyncpg and Alembic

asyncpg is a separate dependency, not bundled by SQLAlchemy. The URL prefix `postgresql+asyncpg://` selects SQLAlchemy's asyncpg driver integration.

SQLAlchemy generates SQL and controls the transaction. asyncpg encodes parameters, sends commands, and receives results. PostgreSQL executes SQL and enforces constraints such as uniqueness, foreign keys, and allowed values. asyncpg checks parameter encoding/type compatibility; it does not validate the incident's business rules.

Alembic changes existing database schemas using versioned migration scripts. Changing a SQLAlchemy model alone does not change an existing table. The current migrations create `incidents` and then `idempotency_keys`.

Each script has `upgrade()` and `downgrade()` functions. Alembic records the current revision in `alembic_version`. `alembic upgrade head` applies pending migrations; the Docker entrypoint runs it before starting the API.

## 8. How everything connects

```text
Client → Uvicorn → FastAPI → IncidentRepository → SQLAlchemy → asyncpg → PostgreSQL
                     ↕
                  Pydantic → pydantic-core

.env / environment → pydantic-settings → database configuration → SQLAlchemy

Startup: Alembic → SQLAlchemy + driver → PostgreSQL schema migrations
```

Responses travel back through the request layers. Settings and migrations are supporting setup, rather than steps repeated for every request. We also created an image illustrating this architecture.

## 9. Reliability already implemented

The optional `Idempotency-Key` header prevents duplicate incident creation when clients retry. The same key and validated body replay the existing incident; a different body with the same key returns `422`. Without a key, each request creates a new incident.

The incident and key are committed together. A database uniqueness constraint handles concurrent duplicate submissions. This protects creation retries; it is different from the planned optimistic locking for updates.

Errors use consistent problem-detail JSON with stable codes: validation errors return `422`, missing incidents `404`, database unavailability `503`, and unexpected failures `500`. Error bodies avoid exposing submitted values and internal details.

The repository contains 26 tests covering validation, persistence across app restarts, idempotency, concurrent submissions, and failure responses. We reviewed them during this chat but did not execute them.

## Files to revisit

- [API setup](../../apps/api/app/main.py)
- [Incident routes](../../apps/api/app/routes/incidents.py)
- [Pydantic models](../../src/incidentops_ai/incidents/models.py)
- [Configuration](../../src/incidentops_ai/config.py)
- [Engine and session factory](../../src/incidentops_ai/db.py)
- [Per-request session dependency](../../apps/api/app/deps.py)
- [Incident repository](../../src/incidentops_ai/incidents/repository.py)
- [ORM table mapping](../../src/incidentops_ai/incidents/orm.py)
- [Idempotency implementation](../../src/incidentops_ai/idempotency.py)
- [Migration scripts](../../migrations/versions)
- [Roadmap](../ROADMAP.md)

## Official references checked during the chat

- [Pydantic license](https://github.com/pydantic/pydantic/blob/main/LICENSE), [pydantic-core license](https://github.com/pydantic/pydantic-core/blob/main/LICENSE), [pydantic-settings license](https://github.com/pydantic/pydantic-settings/blob/main/LICENSE)
- [FastAPI and ASGI servers](https://fastapi.tiangolo.com/deployment/manually/#asgi-servers)
- [SQLAlchemy session basics](https://docs.sqlalchemy.org/en/21/orm/session_basics.html)
- [Room DAO operations](https://developer.android.com/training/data-storage/room/accessing-data)
- [PostgreSQL transaction isolation](https://www.postgresql.org/docs/current/transaction-iso.html)
- [SQLAlchemy optimistic version checks](https://docs.sqlalchemy.org/en/21/orm/versioning.html)
