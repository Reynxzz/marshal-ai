# Marshal Multi-Tenant Database Architecture, ERD, and Data Modeling Plan

> **Scope**: Relational data modeling, schema definition, and migration engineering for the **Marshal Control Tower & Test Lab Backend**.  
> **Tech Stack**: **SQLModel** (SQLAlchemy 2.0 + Pydantic v2), **Alembic**, **Pydantic v2 DTOs**, **ULID** (Universally Unique Lexicographically Sortable Identifier).  
> **Database Engines**: **Dual-Engine Architecture** — **SQLite (Local Dev, Testing & Standalone Demos)** seamlessly interchangeable with **PostgreSQL 16+ (Staging & Production)** with zero model code divergence.  
> **Architecture Pattern**: Multi-Tenant Shared Database with Shared Schema, ORM-Level Tenant Interceptors, and Database-Level Row-Level Security (RLS in PostgreSQL).

---

## 1. Primary Key Strategy: Why ULID instead of UUID?

In high-throughput manufacturing, tracking, and event-heavy environments like Marshal, the choice of primary key directly dictates database write scalability, cross-engine compatibility, and cache efficiency.

### Comparison: ULID vs. UUIDv4 vs. Auto-Increment BigInt

| Metric | Auto-Increment BigInt | UUIDv4 (RFC 4122) | **ULID (Chosen)** |
| :--- | :--- | :--- | :--- |
| **Global Uniqueness** | ❌ Per-table only |  Yes (128-bit random) |  **Yes (128-bit: 48-bit time + 80-bit random)** |
| **Sortability** |  Sequential | ❌ Completely Random |  **Monotonically / Lexicographically Sortable** |
| **B-Tree Index Health** |  Append-only | ❌ High fragmentation, page splits |  **High B-Tree locality; append-optimized** |
| **Information Leakage** | ❌ Sequential IDs leak order volume |  None |  Timestamp visible, but payload random |
| **URL Ergonomics** |  Short | ❌ 36 chars with hyphens |  **26 chars, Crockford's Base32 (no ambiguous chars)** |
| **SQLite & Postgres Parity** | Engine-specific sequence quirks | ❌ Needs uuid extensions in Postgres; stored as raw text in SQLite |  **Uniform `VARCHAR(26)` across both SQLite and Postgres** |

### Database Storage Representation for ULID
* **Storage Type**: `VARCHAR(26)` / `CHAR(26)` (or `TEXT` in SQLite).
* **String Format**: 26 characters using Crockford’s Base32 alphabet (`0123456789ABCDEFGHJKMNPQRSTVWXYZ`, excluding `I`, `L`, `O`, `U` to prevent visual ambiguity and transcription errors).
* **B-Tree Indexing Impact**: Because the first 10 characters encode the millisecond UNIX timestamp, new records are inserted sequentially at the right-hand edge of the B-Tree index. This eliminates random disk page lookups, prevents B-Tree page splits, and preserves index cache residency in RAM across both PostgreSQL and SQLite.

---

## 2. Dual-Engine Architecture: Seamless SQLite ⇋ PostgreSQL Parity

To enable rapid local development without requiring local Docker/PostgreSQL containers while guaranteeing zero code friction when deploying to production PostgreSQL, Marshal utilizes a **Dual-Engine Strategy**.

```mermaid
flowchart TD
    subgraph EnvConfig["Environment Configuration (DATABASE_URL)"]
        LocalEnv["Local Dev / Pytest / Standalone Demo<br/>sqlite+aiosqlite:///./marshal.db"]
        ProdEnv["Staging / Production Cluster<br/>postgresql+asyncpg://user:pass@host/marshal"]
    end

    subgraph DataLayer["Unified SQLModel & Alembic Data Layer"]
        Models["Dialect-Agnostic SQLModel Schemas<br/>(ULID VARCHAR(26), Dialect-Variant JSON/JSONB)"]
        ORMInterceptor["Level 1 Isolation: ORM Tenant Interceptor<br/>(Enforced in BOTH SQLite and PostgreSQL)"]
        AlembicBatch["Alembic Migration Engine<br/>(render_as_batch=True)"]
    end

    subgraph EngineInstances["Database Engines"]
        SQLiteEngine["SQLite Engine<br/>- PRAGMA foreign_keys = ON<br/>- PRAGMA journal_mode = WAL"]
        PostgresEngine["PostgreSQL Engine<br/>- Level 2 Isolation: Native DB RLS<br/>- Native Partitioning & GIN"]
    end

    LocalEnv --> Models
    ProdEnv --> Models
    Models --> ORMInterceptor
    ORMInterceptor --> SQLiteEngine
    ORMInterceptor --> PostgresEngine
    AlembicBatch --> SQLiteEngine
    AlembicBatch --> PostgresEngine
```

### The 6 Engine Parity Bridges

| Feature | SQLite (Local Dev) | PostgreSQL 16+ (Production) | How Parity is Guaranteed |
| :--- | :--- | :--- | :--- |
| **1. Primary Keys** | `VARCHAR(26)` | `VARCHAR(26)` | ULID generates identical 26-char Base32 strings on both engines. |
| **2. JSON Fields** | Stored as text; validated as JSON | Binary `JSONB` for deep querying | Use `sa.JSON().with_variant(postgresql.JSONB, "postgresql")`. |
| **3. Foreign Keys** | Disabled by default in SQLite | Enabled natively | Automatic SQLAlchemy connection hook: `PRAGMA foreign_keys = ON`. |
| **4. Concurrency** | Default journal can lock | Native MVCC | Enable `PRAGMA journal_mode = WAL;` on SQLite connection. |
| **5. Multi-Tenancy** | Emulated via ORM query filter | Dual: ORM filter + Database RLS | SQLModel session listener injects `WHERE tenant_id = ...` on all queries. |
| **6. Migrations** | Limited `ALTER TABLE` | Full DDL `ALTER TABLE` | Alembic configured with `render_as_batch=True`. RLS DDL conditional on dialect. |

---

## 3. Disciplined Indexing Strategy (Anti-Over-Indexing Policy)

An uncontrolled number of indexes degrades write throughput, inflates WAL volume, and consumes buffer cache. We adhere to the **Strict Indexing Policy**:

```
Total Indexes per Table ≤ 3 to 4 (including PK and Unique constraints)
```

### 3.1 The "Left-Prefix" Indexing Rule
In composite multi-tenant queries:
* When an index is defined as `(tenant_id, plant_id, state)`, PostgreSQL and SQLite can satisfy queries filtering on:
  1. `tenant_id`
  2. `(tenant_id, plant_id)`
  3. `(tenant_id, plant_id, state)`
* **Rule**: We **never** create standalone indexes on `tenant_id` or `plant_id` if they already serve as the leading column in a composite index.

### 3.2 What We DO Index (High-Traffic Query Paths)
1. **Primary Keys & Foreign Keys**: Primary keys (`id`) are automatically indexed as B-Trees. Foreign keys participating in frequent JOINs (e.g. `car_id`, `case_id`, `inspection_result_id`) are indexed.
2. **Multi-Tenant State Filtering**:
   - `cars`: Composite `(tenant_id, plant_id, state)` — supports the yard overview dashboard.
   - `proposals`: Composite `(tenant_id, status)` — powers the supervisor approval inbox.
   - `assistance_cases`: Partial index `(tenant_id, plant_id)` `WHERE status != 'RESOLVED'` — keeps active incident lookups fast while keeping the index footprint minimal. Supported by both PostgreSQL and modern SQLite.
   - `async_jobs`: Composite `(tenant_id, status)` — prevents table scans during frontend job status polling (`/jobs/:id`).
   - `event_logs`: Composite `(tenant_id, plant_id, seq)` — powers the `/events` cursor stream (`since=seq`).

### 3.3 What We DELIBERATELY DO NOT Index (Preventing Over-Indexing)
* ❌ **Low-Cardinality Booleans**: We do **not** index standalone booleans such as `do_not_ship`, `moving_people_or_equipment`, `disagree`, `available`, or `camera_applicable`. Sequential scans or bitmap filter steps are faster.
* ❌ **Unfiltered Text & JSON Payloads**: Columns like `summary`, `observation`, `notes`, `spec`, and `payload` are **not** indexed with B-Tree or GIN unless a specific full-text search capability is required.
* ❌ **Intermediate Foreign Keys in 1:1 Relationships**: E.g., `build_sheets.car_id` already carries a `UNIQUE` constraint, which natively creates an index; no additional index is added.

---

## 4. Entity-Relationship Diagram (ERD)

```mermaid
erDiagram
    TENANTS ||--o{ PLANTS : "owns"
    TENANTS ||--o{ USERS : "employs"
    PLANTS ||--o{ USERS : "assigned_to"

    PLANTS ||--o{ ZONES : "contains"
    PLANTS ||--o{ REWORK_BAYS : "operates"
    PLANTS ||--o{ PLANT_MAP_POINTS : "locates"
    PLANTS ||--o{ TRUCKS : "schedules"

    PLANTS ||--o{ CARS : "processes"
    CARS ||--|| BUILD_SHEETS : "specifies"
    CARS ||--o{ MISSIONS : "receives"
    CARS ||--o{ ASSISTANCE_CASES : "raises"
    CARS ||--o{ INSPECTION_RESULTS : "undergoes"
    CARS ||--o{ CAR_REPORTS : "consolidates"
    CARS ||--o{ REWORK_TICKETS : "routed_to"
    CARS ||--o{ PROPOSALS : "targeted_in"

    ASSISTANCE_CASES ||--o{ ASSISTANCE_OPTIONS : "presents"
    INSPECTION_RESULTS ||--o{ VISUAL_FINDINGS : "records"
    INSPECTION_RESULTS ||--o{ BUILDSHEET_MISMATCHES : "flags"
    INSPECTION_RESULTS ||--o{ FUNCTIONAL_STEP_CHECKS : "evaluates"

    PLANTS ||--o{ TEST_REQUESTS : "originates"
    TEST_REQUESTS ||--o| SCENARIOS : "compiled_into"
    PLANTS ||--o{ SCENARIOS : "stores"
    SCENARIOS ||--o{ TEST_RUNS : "executes"
    TENANTS ||--o{ RELEASES : "evaluates"
    RELEASES ||--o{ RELEASE_EVAL_TESTS : "gates"
    TEST_RUNS ||--o{ RELEASE_EVAL_TESTS : "verified_by"

    PLANTS ||--o{ ASYNC_JOBS : "tracks"
    PLANTS ||--o{ MEDIA_ARTIFACTS : "hosts"
    PLANTS ||--o{ EVENT_LOGS : "logs"

    TENANTS {
        string id PK "ulid(26)"
        string name
        string slug UK
        json settings
        timestamp created_at
    }

    PLANTS {
        string id PK "ulid(26)"
        string tenant_id FK "ulid(26)"
        string code
        string name
        string timezone
        timestamp created_at
    }

    USERS {
        string id PK "ulid(26)"
        string tenant_id FK "ulid(26)"
        string plant_id FK "ulid(26)"
        string actor_id UK
        string email UK
        string display_name
        string role
        json permissions
        boolean is_active
        timestamp created_at
    }

    CARS {
        string id PK "ulid(26)"
        string tenant_id FK "ulid(26)"
        string plant_id FK "ulid(26)"
        string vin UK
        string state
        string location
        string mission_id
        string yard_slot
        string truck_id FK "ulid(26)"
        boolean do_not_ship
        json flags
        timestamp released_at
        timestamp yard_at
        timestamp updated_at
    }

    BUILD_SHEETS {
        string id PK "ulid(26)"
        string car_id FK,UK "ulid(26)"
        string model
        string trim
        string color
        json options
    }

    MISSIONS {
        string id PK "ulid(26)"
        string tenant_id FK "ulid(26)"
        string plant_id FK "ulid(26)"
        string car_id FK "ulid(26)"
        string mission_type
        string destination
        string status
        timestamp issued_at
        timestamp completed_at
    }

    ASSISTANCE_CASES {
        string id PK "ulid(26)"
        string tenant_id FK "ulid(26)"
        string plant_id FK "ulid(26)"
        string case_id UK
        string car_id FK "ulid(26)"
        string location
        string situation
        string status
        boolean moving_people_or_equipment
        string recommended_option_id
        float confidence
        string choice
        string resolved_by
        timestamp opened_at
        timestamp resolved_at
        string clip_media_id FK "ulid(26)"
    }

    ASSISTANCE_OPTIONS {
        string id PK "ulid(26)"
        string case_id FK "ulid(26)"
        string option_id
        string label
        string risk
        string reason
        integer rank
    }

    INSPECTION_RESULTS {
        string id PK "ulid(26)"
        string tenant_id FK "ulid(26)"
        string plant_id FK "ulid(26)"
        string result_id UK
        string car_id FK "ulid(26)"
        string kind
        string verdict
        string review_status
        string reviewed_by
        string review_decision
        string proposal_id FK "ulid(26)"
        timestamp created_at
    }

    VISUAL_FINDINGS {
        string id PK "ulid(26)"
        string inspection_result_id FK "ulid(26)"
        string zone
        string defect_type
        float size_cm
        float confidence
        string observation
        string media_id FK "ulid(26)"
    }

    BUILDSHEET_MISMATCHES {
        string id PK "ulid(26)"
        string inspection_result_id FK "ulid(26)"
        string attribute
        string expected
        string found
        float confidence
    }

    FUNCTIONAL_STEP_CHECKS {
        string id PK "ulid(26)"
        string inspection_result_id FK "ulid(26)"
        integer step_number
        string name
        boolean telemetry_ok
        boolean camera_ok
        float camera_confidence
        string observation
        boolean disagree
        boolean camera_applicable
        string media_id FK "ulid(26)"
    }

    CAR_REPORTS {
        string id PK "ulid(26)"
        string tenant_id FK "ulid(26)"
        string plant_id FK "ulid(26)"
        string car_id FK,UK "ulid(26)"
        string verdict
        string summary
        string confirmed_by
        timestamp confirmed_at
        string reinspection_status
        timestamp updated_at
    }

    REWORK_TICKETS {
        string id PK "ulid(26)"
        string tenant_id FK "ulid(26)"
        string plant_id FK "ulid(26)"
        string ticket_id UK
        string car_id FK "ulid(26)"
        string report_id FK "ulid(26)"
        string bay_code
        string defect_type
        string status
        string reason
        string confirmed_by
        string done_by
        timestamp created_at
        timestamp done_at
    }

    PROPOSALS {
        string id PK "ulid(26)"
        string tenant_id FK "ulid(26)"
        string plant_id FK "ulid(26)"
        string proposal_id UK
        string kind
        string category
        string car_id FK "ulid(26)"
        string summary
        json payload
        string status
        json allowed_actions
        json effects
        string decision
        string decided_by
        timestamp created_at
        timestamp decided_at
    }

    SCENARIOS {
        string id PK "ulid(26)"
        string tenant_id FK "ulid(26)"
        string plant_id FK "ulid(26)"
        string scenario_id UK
        string source
        string status
        json tags
        string owner
        string summary
        json spec
        json validator_notes
        json dry_run_metrics
        timestamp created_at
    }

    TEST_RUNS {
        string id PK "ulid(26)"
        string tenant_id FK "ulid(26)"
        string scenario_id FK "ulid(26)"
        string run_id UK
        string variant
        json params
        json metrics
        boolean passed
        json failed_criteria
        string clip_media_id FK "ulid(26)"
        timestamp created_at
    }

    RELEASES {
        string id PK "ulid(26)"
        string tenant_id FK "ulid(26)"
        string release_id UK
        string name
        string evaluation_status
        string gate
        integer passed_count
        integer failed_count
        string approved_by
        timestamp approved_at
        timestamp updated_at
    }

    RELEASE_EVAL_TESTS {
        string id PK "ulid(26)"
        string release_id FK "ulid(26)"
        string scenario_id FK "ulid(26)"
        string test_run_id FK "ulid(26)"
        string test_id
        string metric
        string criteria
        string result
        boolean passed
        string clip_media_id FK "ulid(26)"
    }

    ASYNC_JOBS {
        string id PK "ulid(26)"
        string tenant_id FK "ulid(26)"
        string plant_id FK "ulid(26)"
        string job_id UK
        string kind
        string status
        integer progress
        string message
        string error
        json result
        timestamp created_at
        timestamp updated_at
    }

    MEDIA_ARTIFACTS {
        string id PK "ulid(26)"
        string tenant_id FK "ulid(26)"
        string plant_id FK "ulid(26)"
        string media_id UK
        string kind
        string storage_uri
        string content_type
        integer file_size_bytes
        boolean available
        timestamp created_at
    }

    EVENT_LOGS {
        string id PK "ulid(26)"
        string tenant_id FK "ulid(26)"
        string plant_id FK "ulid(26)"
        bigint seq
        integer flow_number
        string source
        string title
        string detail
        string level
        json payload
        timestamp recorded_at
    }
```

---

## 5. Index Catalog & Optimization Matrix

Below is the definitive catalog of all indexes across the database. Every index is justified by an access pattern and verified against both SQLite and PostgreSQL.

| Table | Index Name | Type / Columns | Query Justification |
| :--- | :--- | :--- | :--- |
| `tenants` | `tenants_pkey` | `PRIMARY KEY (id)` | Standard ULID PK lookup |
| | `uq_tenants_slug` | `UNIQUE (slug)` | Tenant lookup by URL slug during auth |
| `plants` | `plants_pkey` | `PRIMARY KEY (id)` | Standard ULID PK lookup |
| | `uq_tenant_plant_code` | `UNIQUE (tenant_id, code)` | Resolves plant by code within tenant |
| `users` | `users_pkey` | `PRIMARY KEY (id)` | Standard ULID PK lookup |
| | `uq_users_actor_id` | `UNIQUE (actor_id)` | Resolves actor identity from token/header |
| | `uq_users_email` | `UNIQUE (email)` | User login by email |
| `cars` | `cars_pkey` | `PRIMARY KEY (id)` | Standard ULID PK lookup |
| | `uq_cars_tenant_plant_vin`| `UNIQUE (tenant_id, plant_id, vin)` | Fast VIN lookups; enforces VIN uniqueness per plant |
| | `idx_cars_tenant_plant_state` | `BTREE (tenant_id, plant_id, state)` | Yard dashboard filtering cars by operational state |
| `build_sheets` | `build_sheets_pkey` | `PRIMARY KEY (id)` | Standard ULID PK lookup |
| | `uq_build_sheets_car_id` | `UNIQUE (car_id)` | 1:1 join between Car and its BuildSheet |
| `assistance_cases`| `assistance_cases_pkey` | `PRIMARY KEY (id)` | Standard ULID PK lookup |
| | `uq_cases_case_id` | `UNIQUE (case_id)` | Fast lookup by business case ID (e.g. `CASE-0101`) |
| | `idx_cases_open_partial` | `BTREE (tenant_id, plant_id) WHERE status != 'RESOLVED'` | **Partial Index**: Fetches active cases for remote desk without scanning historical resolved cases (supported in both SQLite & Postgres) |
| `proposals` | `proposals_pkey` | `PRIMARY KEY (id)` | Standard ULID PK lookup |
| | `uq_proposals_proposal_id`| `UNIQUE (proposal_id)` | Resolves proposal by code (`P-001`) |
| | `idx_proposals_tenant_status` | `BTREE (tenant_id, status)` | Fast retrieval of pending approval queue |
| `inspection_results`| `inspection_results_pkey` | `PRIMARY KEY (id)` | Standard ULID PK lookup |
| | `uq_results_result_id` | `UNIQUE (result_id)` | Resolves result by ID (`IR-VIN-DARK-functional`) |
| | `idx_results_car_id` | `BTREE (car_id)` | Fetches all visual/functional inspections for a vehicle |
| `visual_findings` | `visual_findings_pkey` | `PRIMARY KEY (id)` | Standard ULID PK lookup |
| | `idx_vf_result_id` | `BTREE (inspection_result_id)` | Joins findings onto their parent inspection result |
| `functional_step_checks`| `fsc_pkey` | `PRIMARY KEY (id)` | Standard ULID PK lookup |
| | `idx_fsc_result_id` | `BTREE (inspection_result_id)` | Joins checklist steps onto parent functional result |
| `scenarios` | `scenarios_pkey` | `PRIMARY KEY (id)` | Standard ULID PK lookup |
| | `uq_scenarios_scenario_id`| `UNIQUE (scenario_id)` | Resolves scenario code (`SC-0143`) |
| | `idx_scenarios_tenant_status` | `BTREE (tenant_id, status)` | Filters library scenarios by status |
| `test_runs` | `test_runs_pkey` | `PRIMARY KEY (id)` | Standard ULID PK lookup |
| | `uq_test_runs_run_id` | `UNIQUE (run_id)` | Resolves run by ID |
| | `idx_runs_scenario_id` | `BTREE (scenario_id)` | Retrieves all variants executed for a scenario |
| `async_jobs` | `async_jobs_pkey` | `PRIMARY KEY (id)` | Standard ULID PK lookup |
| | `uq_jobs_job_id` | `UNIQUE (job_id)` | Polling endpoint lookup (`GET /api/v1/jobs/{job_id}`) |
| | `idx_jobs_tenant_status` | `BTREE (tenant_id, status)` | Checks active worker queues |
| `event_logs` | `event_logs_pkey` | `PRIMARY KEY (id)` | Standard ULID PK lookup |
| | `idx_events_stream` | `BTREE (tenant_id, plant_id, seq)` | Powers the SSE stream (`/events?since=seq`) |

---

## 6. SQLModel & Database Session Implementation

### 6.1 Database Session & Dialect-Agnostic JSON Helper

```python
# src/marshal_api/db/session.py
import os
from sqlite3 import Connection as SQLite3Connection
from sqlalchemy import JSON, Column, event
from sqlalchemy.dialects import postgresql
from sqlalchemy.engine import Engine
from sqlmodel import SQLModel, create_engine
from sqlmodel.ext.asyncio.session import AsyncSession
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.orm import sessionmaker

# Configurable database URL: defaults to local SQLite for rapid dev
DATABASE_URL = os.getenv("MARSHAL_DB_URL", "sqlite+aiosqlite:///./marshal.db")

# Cross-dialect JSON column: JSON text in SQLite, binary JSONB in PostgreSQL
DialectJSON = JSON().with_variant(postgresql.JSONB, "postgresql")

# Engine options for async operation
engine_kwargs = {}
if DATABASE_URL.startswith("sqlite"):
    engine_kwargs["connect_args"] = {"check_same_thread": False}

engine = create_async_engine(DATABASE_URL, echo=False, **engine_kwargs)


# Crucial SQLite Pragmas: Foreign Key Enforcement & WAL mode for concurrency
@event.listens_for(Engine, "connect")
def set_sqlite_pragma(dbapi_connection, connection_record):
    if isinstance(dbapi_connection, SQLite3Connection):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON;")
        cursor.execute("PRAGMA journal_mode=WAL;")
        cursor.close()


async def get_session() -> AsyncSession:
    async_session = sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )
    async with async_session() as session:
        yield session
```

### 6.2 ULID Helper & TenantScoped Base Model

```python
# src/marshal_api/models/base.py
import ulid
from datetime import datetime, timezone
from typing import Optional
from sqlmodel import Field, SQLModel


def generate_ulid() -> str:
    """Generate a 26-character monotonically sortable ULID string."""
    return ulid.new().str


def utc_now() -> datetime:
    """Return timezone-aware current UTC time."""
    return datetime.now(timezone.utc)


class TenantScopedModel(SQLModel):
    """Abstract base model enforcing multi-tenancy with ULID keys and audit timestamps."""

    tenant_id: str = Field(
        foreign_key="tenants.id",
        max_length=26,
        nullable=False,
        description="Tenant / Organization ULID"
    )
    plant_id: str = Field(
        foreign_key="plants.id",
        max_length=26,
        nullable=False,
        description="Physical plant facility ULID"
    )
    created_at: datetime = Field(
        default_factory=utc_now,
        nullable=False
    )
    updated_at: datetime = Field(
        default_factory=utc_now,
        nullable=False,
        sa_column_kwargs={"onupdate": utc_now}
    )
```

### 6.3 Tenancy, Plant, and Car Models

```python
# src/marshal_api/models/yard.py
from enum import Enum
from typing import Optional, List, Dict, Any
from sqlalchemy import Column, Index, UniqueConstraint
from sqlmodel import Field, Relationship, SQLModel
from marshal_api.db.session import DialectJSON
from marshal_api.models.base import TenantScopedModel, generate_ulid, utc_now


class CarState(str, Enum):
    LINE_END = "line_end"
    INSPECTION_GATE = "inspection_gate"
    REVIEW = "review"
    TEST_TRACK = "test_track"
    REPORT = "report"
    REWORK = "rework"
    YARD = "yard"
    SHIPPED = "shipped"
    PARKED = "parked"
    DRIVING = "driving"
    HELD = "held"
    WAITING_DISPATCH = "waiting_dispatch"
    EXCEPTION = "exception"


class Tenant(SQLModel, table=True):
    __tablename__ = "tenants"

    id: str = Field(default_factory=generate_ulid, primary_key=True, max_length=26)
    name: str = Field(max_length=100, nullable=False)
    slug: str = Field(max_length=50, unique=True, nullable=False)
    settings: Dict[str, Any] = Field(default_factory=dict, sa_column=Column(DialectJSON))
    created_at: datetime = Field(default_factory=utc_now, nullable=False)

    plants: List["Plant"] = Relationship(back_populates="tenant")


class Plant(SQLModel, table=True):
    __tablename__ = "plants"
    __table_args__ = (
        UniqueConstraint("tenant_id", "code", name="uq_tenant_plant_code"),
    )

    id: str = Field(default_factory=generate_ulid, primary_key=True, max_length=26)
    tenant_id: str = Field(foreign_key="tenants.id", max_length=26, nullable=False)
    code: str = Field(max_length=20, nullable=False)
    name: str = Field(max_length=100, nullable=False)
    timezone: str = Field(default="UTC", max_length=50)
    created_at: datetime = Field(default_factory=utc_now, nullable=False)

    tenant: Optional[Tenant] = Relationship(back_populates="plants")
    cars: List["Car"] = Relationship(back_populates="plant")


class BuildSheet(SQLModel, table=True):
    __tablename__ = "build_sheets"

    id: str = Field(default_factory=generate_ulid, primary_key=True, max_length=26)
    # Unique constraint natively creates the required index for 1:1 join
    car_id: str = Field(foreign_key="cars.id", unique=True, max_length=26, nullable=False)
    model: str = Field(max_length=50, nullable=False)
    trim: str = Field(default="standard", max_length=50)
    color: str = Field(max_length=50, nullable=False)
    options: List[str] = Field(default_factory=list, sa_column=Column(DialectJSON))

    car: Optional["Car"] = Relationship(back_populates="build_sheet")


class Car(TenantScopedModel, table=True):
    __tablename__ = "cars"
    __table_args__ = (
        # 1. Enforces VIN uniqueness within the tenant's plant (creates B-Tree index)
        UniqueConstraint("tenant_id", "plant_id", "vin", name="uq_cars_tenant_plant_vin"),
        # 2. Composite index satisfying yard overview queries: (tenant_id, plant_id, state)
        Index("idx_cars_tenant_plant_state", "tenant_id", "plant_id", "state"),
    )

    id: str = Field(default_factory=generate_ulid, primary_key=True, max_length=26)
    vin: str = Field(max_length=50, nullable=False)
    state: CarState = Field(default=CarState.LINE_END, max_length=30)
    location: str = Field(default="line_end", max_length=50)
    mission_id: Optional[str] = Field(default=None, max_length=50)
    yard_slot: Optional[str] = Field(default=None, max_length=20)
    truck_id: Optional[str] = Field(default=None, foreign_key="trucks.id", max_length=26)
    do_not_ship: bool = Field(default=False)
    flags: List[str] = Field(default_factory=list, sa_column=Column(DialectJSON))
    released_at: Optional[datetime] = Field(default=None)
    yard_at: Optional[datetime] = Field(default=None)

    plant: Optional[Plant] = Relationship(back_populates="cars")
    build_sheet: Optional[BuildSheet] = Relationship(
        back_populates="car", sa_relationship_kwargs={"uselist": False}
    )
    assistance_cases: List["AssistanceCase"] = Relationship(back_populates="car")
```

### 6.4 Remote Assistance & Proposals Models

```python
# src/marshal_api/models/assistance.py
from enum import Enum
from typing import Optional, List, Dict, Any
from sqlalchemy import Column, Index
from sqlmodel import Field, Relationship, SQLModel
from marshal_api.db.session import DialectJSON
from marshal_api.models.base import TenantScopedModel, generate_ulid, utc_now


class AssistanceStatus(str, Enum):
    OPEN = "OPEN"
    ESCALATED = "ESCALATED"
    RESOLVED = "RESOLVED"


class RiskLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class AssistanceCase(TenantScopedModel, table=True):
    __tablename__ = "assistance_cases"
    __table_args__ = (
        # Partial Index: index active cases only. Works in both SQLite and PostgreSQL.
        Index(
            "idx_cases_open_partial",
            "tenant_id",
            "plant_id",
            postgresql_where=Column("status") != AssistanceStatus.RESOLVED.value,
            sqlite_where=Column("status") != AssistanceStatus.RESOLVED.value,
        ),
    )

    id: str = Field(default_factory=generate_ulid, primary_key=True, max_length=26)
    case_id: str = Field(max_length=50, unique=True, nullable=False)
    car_id: str = Field(foreign_key="cars.id", max_length=26, index=True, nullable=False)
    location: str = Field(max_length=50, nullable=False)
    situation: str = Field(default="", nullable=False)
    status: AssistanceStatus = Field(default=AssistanceStatus.OPEN, max_length=20)
    moving_people_or_equipment: bool = Field(default=False)
    recommended_option_id: Optional[str] = Field(default=None, max_length=50)
    confidence: Optional[float] = Field(default=None)
    choice: Optional[str] = Field(default=None, max_length=50)
    resolved_by: Optional[str] = Field(default=None, max_length=50)
    clip_media_id: Optional[str] = Field(default=None, foreign_key="media_artifacts.id", max_length=26)
    opened_at: datetime = Field(default_factory=utc_now)
    resolved_at: Optional[datetime] = Field(default=None)

    car: Optional["Car"] = Relationship(back_populates="assistance_cases")
    options: List["AssistanceOption"] = Relationship(back_populates="case")


class AssistanceOption(SQLModel, table=True):
    __tablename__ = "assistance_options"

    id: str = Field(default_factory=generate_ulid, primary_key=True, max_length=26)
    case_id: str = Field(foreign_key="assistance_cases.id", max_length=26, index=True, nullable=False)
    option_id: str = Field(max_length=50, nullable=False)
    label: str = Field(max_length=100, nullable=False)
    risk: RiskLevel = Field(default=RiskLevel.LOW, max_length=20)
    reason: Optional[str] = Field(default=None)
    rank: Optional[int] = Field(default=None)

    case: Optional[AssistanceCase] = Relationship(back_populates="options")


class Proposal(TenantScopedModel, table=True):
    __tablename__ = "proposals"
    __table_args__ = (
        # Fast queue access: supervisor inbox queries pending proposals
        Index("idx_proposals_tenant_status", "tenant_id", "status"),
    )

    id: str = Field(default_factory=generate_ulid, primary_key=True, max_length=26)
    proposal_id: str = Field(max_length=50, unique=True, nullable=False)
    kind: str = Field(max_length=30, nullable=False)  # replan, rework, inspection_review
    category: str = Field(max_length=50, nullable=False)
    car_id: Optional[str] = Field(default=None, foreign_key="cars.id", max_length=26, index=True)
    summary: str = Field(nullable=False)
    payload: Dict[str, Any] = Field(default_factory=dict, sa_column=Column(DialectJSON))
    status: str = Field(default="pending", max_length=20)
    allowed_actions: List[str] = Field(default_factory=list, sa_column=Column(DialectJSON))
    effects: Dict[str, Any] = Field(default_factory=dict, sa_column=Column(DialectJSON))
    decision: Optional[str] = Field(default=None, max_length=50)
    decided_by: Optional[str] = Field(default=None, max_length=50)
    decided_at: Optional[datetime] = Field(default=None)
```

---

## 7. Dual-Engine Multi-Tenancy Enforcement

Multi-tenancy isolation operates at two levels depending on the database engine in use:

### Level 1: ORM-Level Interceptor (Active in BOTH SQLite & PostgreSQL)
In local development with SQLite, native RLS does not exist. An SQLAlchemy event interceptor injects tenant filters into every ORM execution:

```python
# src/marshal_api/db/tenant_interceptor.py
from contextvars import ContextVar
from sqlalchemy import event
from sqlalchemy.orm import Session
from sqlmodel.sql.expression import Select

current_tenant_var: ContextVar[str] = ContextVar("current_tenant", default="")


@event.listens_for(Session, "do_orm_execute")
def enforce_tenant_isolation(orm_execute_state):
    """Automatically append tenant_id filter to all Select queries if tenant-scoped."""
    tenant_id = current_tenant_var.get()
    if orm_execute_state.is_select and tenant_id:
        orm_execute_state.statement = orm_execute_state.statement.filter_by(
            tenant_id=tenant_id
        )
```

### Level 2: Database-Level Row-Level Security (Active in PostgreSQL)
When running against PostgreSQL in staging/production, native RLS provides defense-in-depth at the connection level:

```python
# migrations/utils/rls.py
from alembic import op


def enable_rls(table_name: str) -> None:
    """Enforce RLS only when running on PostgreSQL; gracefully skip on SQLite."""
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute(f"ALTER TABLE {table_name} ENABLE ROW LEVEL SECURITY;")
        op.execute(f"ALTER TABLE {table_name} FORCE ROW LEVEL SECURITY;")
        op.execute(f"""
            CREATE POLICY tenant_isolation_policy ON {table_name}
            USING (
                tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')
            )
            WITH CHECK (
                tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')
            );
        """)


def disable_rls(table_name: str) -> None:
    """Disable RLS conditionally on PostgreSQL."""
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation_policy ON {table_name};")
        op.execute(f"ALTER TABLE {table_name} DISABLE ROW LEVEL SECURITY;")
```

---

## 8. Alembic Migration Implementation Plan (Batch Mode Enabled)

Because SQLite does not support standard `ALTER COLUMN` operations natively, Alembic must be configured with **`render_as_batch=True`**. This allows Alembic to manage migrations identically across SQLite and PostgreSQL.

### `migrations/env.py`

```python
# migrations/env.py
from logging.config import fileConfig
from sqlalchemy import engine_from_config, pool
from sqlmodel import SQLModel
from alembic import context

import marshal_api.models  # noqa: F401 registers models to metadata

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = SQLModel.metadata


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            compare_server_default=True,
            # render_as_batch=True allows SQLite to execute table alterations via copy-and-move
            render_as_batch=True,
        )

        with context.begin_transaction():
            context.run_migrations()


run_migrations_online()
```

---

## 9. Migration Rollout & Switching Checklist

```mermaid
flowchart LR
    P1["Phase 1: Tenancy & IAM<br/>(SQLite file created)"] --> P2["Phase 2: Yard & Operations<br/>(ULID PKs + Composite Indices)"]
    P2 --> P3["Phase 3: Quality & Assistance<br/>(Partial Index test)"]
    P3 --> P4["Phase 4: Lab & Governance<br/>(Scenarios, Runs, Jobs)"]
    P4 --> P5["Phase 5: Switch to Postgres<br/>(Change MARSHAL_DB_URL, run migrations)"]
```

### Switching from SQLite to PostgreSQL in Production
To move from local SQLite to PostgreSQL without any code changes:
1. Provision PostgreSQL instance (e.g., Cloud SQL or Amazon RDS).
2. Set the environment variable:
   ```bash
   export MARSHAL_DB_URL="postgresql+asyncpg://marshal_app:secure_password@prod-db.internal:5432/marshal_db"
   ```
3. Run migrations against PostgreSQL:
   ```bash
   alembic upgrade head
   ```
   * Alembic will automatically create the PostgreSQL tables, apply binary `JSONB` column types, create all composite B-Tree indexes, and invoke `enable_rls` to activate PostgreSQL native Row-Level Security.
4. Run integration verification:
   ```bash
   pytest tests/test_db_parity.py
   ```

---

## 10. Applying Migrations — Operator Runbook

This section is the practical companion to Section 8: exactly how to install, apply, extend, and verify the schema this document describes.

### 10.1 One-Time Setup

```bash
# Install the data layer. `postgres` adds asyncpg for staging/production.
uv pip install -e ".[dev]"
uv pip install -e ".[postgres]"     # only when targeting PostgreSQL

# The database URL is read from the environment (or a local .env).
# Defaults to local SQLite, so no setup is required to start.
export MARSHAL_DB_URL="sqlite+aiosqlite:///./marshal.db"
```

### 10.2 Apply the Migration

Run from the repository root (where `alembic.ini` and `migrations/` live):

```bash
.venv/bin/python -m alembic upgrade head
```

This creates all 26 tables, every composite/partial index from Section 5, and — on PostgreSQL only — enables and forces Row-Level Security with the `tenant_isolation_policy` on all 19 tenant-scoped tables.

Day-to-day Alembic commands:

```bash
.venv/bin/python -m alembic current          # the revision the database is at
.venv/bin/python -m alembic history          # the migration list
.venv/bin/python -m alembic upgrade head     # apply everything pending
.venv/bin/python -m alembic downgrade base   # drop everything (resets SQLite dev)
.venv/bin/python -m alembic check            # fail if models drifted from the migration
```

### 10.3 After Changing a Model

```bash
.venv/bin/python -m alembic revision --autogenerate -m "add xyz"
# Review the new file under migrations/versions/ (batch ops, enum/JSON types),
# then apply it:
.venv/bin/python -m alembic upgrade head
```

`render_as_batch=True` (Section 8) rewrites unsupported `ALTER TABLE` statements into copy-and-move for SQLite, so the same revision runs on both engines.

### 10.4 Inspect the Generated SQL Without a Database

Offline mode prints the DDL Alembic would run for a given dialect:

```bash
MARSHAL_DB_URL="postgresql+asyncpg://u:p@host/marshal" \
  .venv/bin/python -m alembic upgrade head --sql
```

### 10.5 Switch to PostgreSQL (Section 9, Phase 5)

No code changes; only the URL changes.

```bash
export MARSHAL_DB_URL="postgresql+asyncpg://marshal_app:secure_password@prod-db.internal:5432/marshal_db"
.venv/bin/python -m alembic upgrade head
```

The app role must **not** have `BYPASSRLS`. For RLS to bind, every request runs its queries inside a tenant context:

```python
from marshal_api.db import async_session_maker
from marshal_api.db.tenant_interceptor import use_tenant
from marshal_api.models import Car
from sqlalchemy import select

with use_tenant(tenant_id):
    async with async_session_maker() as session:
        cars = (await session.execute(select(Car))).scalars().all()
```

On PostgreSQL the session's `after_begin` hook publishes `app.current_tenant_id` (transaction-local) so the RLS policies and the Level 1 ORM filter agree on the tenant.

### 10.6 Verify

```bash
MARSHAL_LLM=offline PYTHONPATH=src .venv/bin/python -m pytest tests/test_db_parity.py -q
```

`tests/test_db_parity.py` asserts the table/index/unique catalog, ULID format and sort order, JSON/JSONB dialect parity, portable enums, the partial index on both engines, ORM tenant isolation, and an Alembic upgrade/downgrade round-trip.

---

## Appendix A — Implementation Notes & Deliberate Deviations

These points differ from the illustrative snippets in Sections 6–8 in order to keep the engines truly interchangeable.

| Area | Section snippet | Shipped implementation | Why |
| :--- | :--- | :--- | :--- |
| **Enum columns** | `Field(default=..., max_length=...)` | `enum_column(..., native_enum=False)` storing the enum `.value` | Native `sa.Enum` produces PostgreSQL-specific `CREATE TYPE` types, breaking the "zero model divergence" guarantee; `native_enum=False` yields `VARCHAR` on both engines and avoids `CREATE/DROP TYPE` in migrations. Values are stored as `line_end`, matching `marshal_ai.records`. |
| **Relationship annotations** | none stated | No `from __future__ import annotations` in model modules | With postponed annotations SQLModel stringifies `Optional["Car"]` and fails mapper configuration; the doc's snippets already rely on eager annotations. |
| **SQLite pragmas** | listener on `Engine` + `isinstance(..., SQLite3Connection)` | listener on `engine.sync_engine`, gated by the URL scheme | The async `aiosqlite` DBAPI connection is not a `sqlite3.Connection`, so the documented check silently never fired. |
| **Alembic `env.py`** | sync `engine_from_config` | Async-aware (`run_sync`) with fallback to sync | The app URL is async (`+aiosqlite` / `+asyncpg`); driving migrations through `run_sync` makes the exact same `MARSHAL_DB_URL` work for both the app and Alembic. |
| **RLS runtime wiring** | policies only | `after_begin` hook `set_config('app.current_tenant_id', ..., true)` | `FORCE ROW LEVEL SECURITY` subjects even the table owner to the policy; without setting the GUC per transaction, PostgreSQL would return zero rows. |
| **ORM interceptor** | `filter_by` on all selects | skips models without `tenant_id`; `include_all_tenants` opt-out | A blind `filter_by(tenant_id=...)` raises on tables that have no such column; cross-tenant admin queries need an escape hatch. |
