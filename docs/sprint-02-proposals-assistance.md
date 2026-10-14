# Sprint 2 — Proposals & Remote Assistance Services (`ProposalService` & `AssistanceService`)

> **Target Flow**: Flow 2 (Remote Assistance Desk & Human Authority Decision Gates)  
> **Target Files**:
> - `src/marshal_api/services/proposal_service.py` (Full implementation)
> - `src/marshal_api/services/assist_service.py` (Full implementation)
> - `src/marshal_api/api/v1/proposals.py` (Router rewiring, removing `_stubs.py`)
> - `src/marshal_api/api/v1/assistance.py` (Router rewiring, removing `_stubs.py`)
> - `tests/test_proposals_service.py` & `tests/test_assistance_service.py` (Verification test suites)

---

## 1. Overview & Scope

Sprint 2 delivers the critical safety and supervisor intervention systems:
1. **`ProposalService`**: Implements the **Human Authority Decision Gate**. AI agents (such as Gemini Yard Optimizer or Inspection Review) cannot unilaterally make high-impact factory re-routing, replanning, or rework decisions. They issue `Proposal` records which human supervisors review, approve, or reject.
2. **`AssistanceService`**: Powers the **Remote Assistance Desk**. When an autonomous vehicle encounters an edge condition in the yard (pedestrians crossing, blocked pathways, sensor confusion), it halts, generates an `AssistanceCase` with ranked recovery options, and waits for a supervisor to choose an option via `POST /assistance/cases/{id}/decide`.

---

## 2. Proposal Service (`services/proposal_service.py`)

### 2.1 Endpoints Covered

| Method | Path | Role Guard | Description |
|:---|:---|:---|:---|
| `GET` | `/proposals` | Any Auth | Returns paginated list of proposals sorted chronologically (newest first). |
| `POST`| `/proposals/{id}/decide`| `supervisor` | Executes supervisor decision (`approved`, `rejected`), executing side-effects. |

### 2.2 Core Logic & Side Effects

#### `list_proposals(kind, status, page, limit) -> Page[Proposal]`
- Filter by `kind` (`replan`, `rework`, `inspection_review`) and `status` (`pending`, `approved`, `rejected`).
- Order by `created_at DESC`.
- Paginate with `total` and `last_page` envelope.

#### `decide_proposal(proposal_id, decision, actor_id, note) -> dict`
1. **Validation**:
   - Fetch proposal by `proposal_id` (or primary ULID). 404 if not found.
   - Verify `proposal.status == "pending"`. If already decided, return 400 Conflict.
   - Verify `decision` exists in `proposal.allowed_actions` (e.g. `["approve_replan", "reject"]`).
2. **State Transition**:
   - Update `proposal.status = "approved"` if approved else `"rejected"`.
   - Record `decided_by = actor_id`, `decided_at = utc_now()`, `decision = decision`.
3. **Action Execution (Side Effects)**:
   - **Case `replan`**: Read target parking slots from `proposal.payload["slots"]`. Dispatch new destination missions to assigned vehicles via `MissionClient`.
   - **Case `rework`**: Extract defect type from payload; create `ReworkTicket` for vehicle and transition car state to `rework`.
   - **Case `inspection_review`**: If supervisor overrides finding as benign, clear vehicle visual flags and dispatch car forward to `test_track`.
4. **Audit Logging**:
   - Emit `proposal.decided` event on `EventBus`.
   - Return `{"ok": True, "proposal_id": proposal_id, "decision": decision}`.

---

## 3. Remote Assistance Service (`services/assist_service.py`)

### 3.1 Endpoints Covered

| Method | Path | Role Guard | Description |
|:---|:---|:---|:---|
| `GET` | `/assistance/cases` | Any Auth | Lists active and historical cases (`OPEN`, `ESCALATED`, `RESOLVED`). |
| `GET` | `/assistance/cases/{id}` | Any Auth | Returns detailed incident card with ranked options and video clip ID. |
| `GET` | `/assistance/kpis` | Any Auth | Returns open/resolved counts and average resolution latency. |
| `POST`| `/assistance/cases/{id}/decide`| `supervisor` | Dispatches operator choice to BehaviorAgent and marks incident resolved. |

### 3.2 Core Logic & Side Effects

#### `list_cases(status, page, limit) -> Page[AssistanceCase]`
- Filter by `status` (default: all active cases first).
- Eager-load child `AssistanceOption` rows via SQLModel relationship.
- Return `Page[AssistanceCase]`.

#### `get_case(case_id: str) -> AssistanceCase`
- Query by `case_id` (e.g. `"CASE-0101"`).
- Join `AssistanceOption` items ordered by `rank ASC`.
- 404 if case does not exist.

#### `get_kpis() -> AssistanceKpis`
- Query aggregates:
  - `open_count`: count where `status IN ('OPEN', 'ESCALATED')`.
  - `resolved_count`: count where `status == 'RESOLVED'`.
  - `avg_resolution_seconds`: average difference between `resolved_at` and `opened_at` for resolved cases.
- Return `AssistanceKpis(open=open_count, resolved=resolved_count, avg_resolution_time_s=avg_seconds)`.

#### `decide_case(case_id, option_id, actor_id) -> dict`
1. **Validation**:
   - Retrieve case and options; ensure case is not already `RESOLVED`.
   - Verify `option_id` matches one of the valid options registered on this case.
2. **BehaviorAgent Dispatch**:
   - Call `await mission_client.send(AssistChoose(case_id=case_id, choice=option_id))`.
3. **State Transition**:
   - Mark `case.status = AssistanceStatus.RESOLVED`.
   - Set `case.choice = option_id`, `case.resolved_by = actor_id`, `case.resolved_at = utc_now()`.
   - Transition the associated car out of exception state back into its active mission.
4. **Return**: `{"ok": True, "case_id": case_id, "status": "RESOLVED", "choice": option_id}`.

---

## 4. Router Updates (`api/v1/proposals.py` & `api/v1/assistance.py`)

Replace all `_stubs` invocations with dependency-injected service calls:

```python
# api/v1/proposals.py
@router.get("/proposals", response_model=Page[Proposal])
async def list_proposals(
    kind: Optional[str] = None,
    status: Optional[str] = None,
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=20, ge=1, le=200),
    service: ProposalService = Depends(get_proposal_service),
) -> Page[Proposal]:
    return await service.list_proposals(kind, status, page, limit)

@router.post("/proposals/{proposal_id}/decide", dependencies=[Depends(_supervisor)])
async def decide_proposal(
    proposal_id: str,
    body: DecideProposalRequest,
    service: ProposalService = Depends(get_proposal_service),
) -> dict[str, Any]:
    return await service.decide_proposal(proposal_id, body.decision, body.actor_id, body.note)
```

```python
# api/v1/assistance.py
@router.get("/assistance/cases", response_model=Page[AssistanceCase])
async def list_cases(
    status: Optional[str] = None,
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=20, ge=1, le=200),
    service: AssistanceService = Depends(get_assistance_service),
) -> Page[AssistanceCase]:
    return await service.list_cases(status, page, limit)

@router.post("/assistance/cases/{case_id}/decide", dependencies=[Depends(_supervisor)])
async def decide_assistance(
    case_id: str,
    body: DecideAssistanceRequest,
    service: AssistanceService = Depends(get_assistance_service),
) -> dict[str, Any]:
    return await service.decide_case(case_id, body.option, body.actor_id)
```

---

## 5. Verification & Testing

1. **`test_proposal_lifecycle`**:
   - Seed proposal with `allowed_actions = ["accept", "reject"]`.
   - Ensure an invalid action raises HTTP 400.
   - Accept proposal; verify status changes to `approved` and side-effect is triggered.
   - Attempt to decide the same proposal again; ensure HTTP 400 conflict is raised.
2. **`test_assistance_resolution`**:
   - Seed `AssistanceCase` with 3 ranked options.
   - Check `GET /assistance/cases/{id}` returns complete option list.
   - Submit choice via `POST /assistance/cases/{id}/decide`; verify `AssistChoose` message dispatched and case marked `RESOLVED`.
   - Verify `GET /assistance/kpis` reflects updated resolution metrics.

