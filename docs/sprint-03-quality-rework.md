# Sprint 3 — Quality Inspection, Reports & Rework Service (`QualityService`)

> **Target Flows**: Flows 3, 4 & 5 (Visual Gate Inspection, Functional Dynamic Check, Quality Certificate & Closed-Loop Rework)  
> **Target Files**:
> - `src/marshal_api/services/quality_service.py` (Full implementation)
> - `src/marshal_api/api/v1/quality.py` (Router rewiring, removing `_stubs.py`)
> - `tests/test_quality_service.py` (Verification test suite)

---

## 1. Overview & Scope

`QualityService` manages factory quality assurance and vehicle certification across three interconnected phases:
1. **Flow 3 (Visual Gate Inspection)**: Static high-resolution camera analysis across 8 body zones checking for paint scratches, panel misalignments, and build-sheet attribute mismatches (e.g. wrong wheels or missing roof rails).
2. **Flow 4 (Functional Dynamic Check)**: Dynamic test track driving checks (headlights, turn signals, autonomous braking) comparing onboard telemetry against stationary CCTV camera observations.
3. **Flow 5 (Vehicle Passport & Rework Loop)**: Aggregating visual and functional verdicts into a single `CarReport` (Vehicle Passport). If defects exist, a `ReworkTicket` is dispatched to physical rework bays. Once repaired, a technician marks rework complete via `POST /cars/{vin}/rework/done`, automatically routing the vehicle back to the gate for **re-inspection of failed items only**.

---

## 2. Endpoints Covered (8 Endpoints)

| Method | Path | Role Guard | Summary & Side Effects |
|:---|:---|:---|:---|
| `GET` | `/inspection/results` | Any Auth | Lists paginated inspection records with aggregated defect counts. |
| `GET` | `/inspection/kpis` | Any Auth | Factory First-Pass Yield (FPY), review share, and total failure metrics. |
| `GET` | `/cars/{vin}/inspection/visual` | Any Auth | 8-zone findings, defect measurements, and build sheet mismatch details. |
| `GET` | `/cars/{vin}/inspection/functional` | Any Auth | Telemetry vs camera checklists and telemetry-camera disagreement flags. |
| `GET` | `/reports` | Any Auth | Paginated vehicle quality passport certificates. |
| `GET` | `/cars/{vin}/report` | Any Auth | Single vehicle comprehensive certificate with rework ticket and re-inspection status. |
| `GET` | `/rework/bays` | Any Auth | Current occupancy and capacity across specialized rework bays (paint, body, electrical). |
| `POST`| `/cars/{vin}/rework/done` | `worker` | Marks rework ticket `DONE`; resets report to `WAITING`; dispatches car back to gate. |

---

## 3. Service Implementation Blueprint (`services/quality_service.py`)

### 3.1 Method Specifications

#### 1. `list_results(kind, verdict, page, limit) -> Page[InspectionResult]`
- **Filter**: Filter `InspectionResult` table by `kind` (`"visual"` | `"functional"`) and `verdict` (`"PASS"` | `"REVIEW"` | `"FAIL"`).
- **Subquery / Join**: Count associated `VisualFinding` or `FunctionalStepCheck` records with defects to populate `defects_count`.
- **Return**: `Page[InspectionResult]` ordered by `created_at DESC`.

#### 2. `get_inspection_kpis() -> InspectionKpis`
- **Calculation**:
  - `total_inspected`: Total vehicles completing Flow 3 & Flow 4.
  - `first_pass_yield`: Ratio of vehicles receiving `verdict == PASS` on initial inspection with 0 defects.
  - `review_share`: Percentage of inspections routed to supervisor review (`verdict == REVIEW`).
  - `total_failed`: Total vehicle inspections marked `FAIL`.
- **Return**: `InspectionKpis(first_pass_yield=..., review_share=..., total_failed=...)`.

#### 3. `get_visual(vin: str) -> VisualInspection`
- **Query**: Select latest visual `InspectionResult` for VIN.
- **Eager Loading**:
  - Load all child `VisualFinding` records (zone, defect_type, size_cm, confidence, observation, media_id).
  - Load all child `BuildsheetMismatch` records (attribute, expected, found, confidence).
- **Return**: `VisualInspection` DTO matching frontend contract.

#### 4. `get_functional(vin: str) -> FunctionalInspection`
- **Query**: Select latest functional `InspectionResult` for VIN.
- **Eager Loading**:
  - Load all child `FunctionalStepCheck` records (step_number, name, telemetry_ok, camera_ok, disagree, camera_confidence).
- **Return**: `FunctionalInspection` DTO.

#### 5. `list_reports(verdict, page, limit) -> Page[Report]`
- **Query**: Paginated query on `CarReport` table, optionally filtered by `verdict`.
- **Return**: `Page[Report]`.

#### 6. `get_report(vin: str) -> Report`
- **Query**: Select `CarReport` joined with vehicle's `ReworkTicket` records.
- **Return**: `Report` containing summary, verdict, findings breakdown, and `reinspection_status`.

#### 7. `get_rework_bays() -> ReworkBayList`
- **Query**:
  - Query all `ReworkBay` records (bay_code, name, capacity, occupied_count, current_vins).
  - Cross-check against in-memory `PlantState.bay_occupants` for real-time synchronization.
- **Return**: `ReworkBayList(items=[...])`.

#### 8. `complete_rework(vin: str, actor_id: str) -> dict`
- **Validation**:
  - Verify actor has `worker` role.
  - Find active `ReworkTicket` for this vehicle where `status == "OPEN"`.
  - 404 if ticket not found; 400 if already resolved.
- **State Transition**:
  - Update `ReworkTicket`: `status = "DONE"`, `done_by = actor_id`, `done_at = utc_now()`.
  - Update `CarReport`: `reinspection_status = "WAITING"`.
  - Update `Car`: `state = CarState.WAITING_DISPATCH`.
  - Release bay in `PlantState.release(vin)`.
- **Mission Dispatch (Closed-Loop Re-inspection)**:
  - Call `MissionClient.send(MissionGo(vin=vin, destination="inspection_gate"))`.
  - Emit `rework.completed` event to `EventBus` specifying `reinspect_failed_only=True`.
- **Return**: `{"ok": True, "vin": vin, "status": "reinspection_queued"}`.

---

## 4. Router Rewiring (`api/v1/quality.py`)

Replace all `_stubs` invocations with `QualityService`:

```python
_worker = RequireRole(["worker", "supervisor"])

@router.get("/inspection/results", response_model=Page[InspectionResult])
async def list_results(
    kind: Optional[str] = None,
    verdict: Optional[str] = None,
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=20, ge=1, le=200),
    service: QualityService = Depends(get_quality_service),
) -> Page[InspectionResult]:
    return await service.list_results(kind, verdict, page, limit)

@router.get("/cars/{vin}/inspection/visual", response_model=VisualInspection)
async def get_visual_inspection(
    vin: str,
    service: QualityService = Depends(get_quality_service),
) -> VisualInspection:
    return await service.get_visual(vin)

@router.get("/cars/{vin}/inspection/functional", response_model=FunctionalInspection)
async def get_functional_inspection(
    vin: str,
    service: QualityService = Depends(get_quality_service),
) -> FunctionalInspection:
    return await service.get_functional(vin)

@router.post("/cars/{vin}/rework/done", dependencies=[Depends(_worker)])
async def mark_rework_done(
    vin: str,
    body: dict[str, Any],
    service: QualityService = Depends(get_quality_service),
) -> dict[str, Any]:
    actor_id = body.get("actor_id", "worker-unknown")
    return await service.complete_rework(vin, actor_id)
```

---

## 5. Verification & Testing

1. **`test_visual_and_functional_joins`**: Seed an inspection result with 3 visual findings and 1 mismatch; assert `GET /cars/{vin}/inspection/visual` correctly includes nested child items.
2. **`test_rework_done_loop`**:
   - Seed a car with a failing report and open rework ticket in `rework.paint`.
   - Call `POST /cars/{vin}/rework/done` with worker credentials.
   - Assert ticket is updated to `DONE`, car report is set to `WAITING`, and bay occupancy is decremented.
3. **`test_worker_role_guard`**: Verify calling `/rework/done` without `worker` or `supervisor` role returns HTTP 403.

