# Sprint 1 — Yard Operations & Vehicle Fleet Service (`YardService`)

> **Target Flow**: Flow 1 (End-of-Line to Yard Parking, Outbound Truck Scheduling, Fleet Traffic)  
> **Target Files**:
> - `src/marshal_api/services/yard_service.py` (Full implementation)
> - `src/marshal_api/api/v1/yard.py` (Router rewiring, removing `_stubs.py`)
> - `src/marshal_api/core/deps.py` (Injecting `YardService` dependencies)
> - `tests/test_yard_service.py` (Comprehensive unit and integration tests)

---

## 1. Overview & Scope

`YardService` orchestrates physical plant operations, vehicle inventory tracking, zone accessibility, truck departure staging, and autonomous driving missions. It is the primary data source for the factory yard visualizer and operations dashboard in `marshal-fe`.

### Endpoints Covered (10 Endpoints)

| Method | Path | Role Guard | Summary & Side Effects |
|:---|:---|:---|:---|
| `GET` | `/yard` | Any Auth | Returns aggregated yard snapshot: summary metrics, cars, trucks, zones, rework bays. |
| `GET` | `/cars` | Any Auth | Paginated car inventory query with multi-field search and status filters. |
| `GET` | `/cars/{vin}` | Any Auth | Detailed vehicle view with joined `BuildSheet` specification. |
| `GET` | `/map` | Any Auth | Static or database-backed 2D layout coordinates for plant map rendering. |
| `POST`| `/trucks/{truck_id}/eta` | `supervisor` | Updates truck ETA. If delayed >15m, emits exception event triggering Gemini replan. |
| `POST`| `/trucks/{truck_id}/load`| `supervisor` | Verifies assigned cars are in `yard` state; transitions car states to `shipped`. |
| `POST`| `/zones/{zone}/closure` | `supervisor` | Toggles zone closed/open state; updates `PlantState` and publishes routing notice. |
| `POST`| `/cars/{vin}/missions` | `supervisor` | Dispatches destination mission via `MissionClient`; transitions car to `driving`. |
| `POST`| `/cars/{vin}/hold` | `supervisor` | Halts vehicle via `MissionClient`; marks state as `held`; sets `"held"` flag. |
| `POST`| `/cars/{vin}/resume` | `supervisor` | Resumes vehicle movement; transitions state to `waiting_dispatch`; clears flag. |

---

## 2. Service Implementation Blueprint (`services/yard_service.py`)

### 2.1 Dependencies & Initialization
```python
class YardService:
    def __init__(
        self,
        session: AsyncSession,
        mission_client: Optional[MissionClient] = None,
        event_bus: Optional[EventBus] = None,
        plant_state: Optional[PlantState] = None,
    ) -> None:
        self.session = session
        self.mission_client = mission_client
        self.event_bus = event_bus
        self.plant_state = plant_state
```

### 2.2 Method Specifications

#### 1. `get_yard_snapshot() -> Yard`
- **Query**:
  - Query all `Car` records for the tenant/plant.
  - Query active `Truck` records.
  - Query active `Zone` records.
  - Query `ReworkBay` records.
- **Aggregation**:
  - Compute total cars, in-transit count, waiting dispatch count, held count, and parked count into `YardSummary`.
- **Return**: `Yard(plant=..., updated_at=..., summary=..., cars=..., trucks=..., zones=..., bays=...)`.

#### 2. `list_cars(q, state, location, truck, flags, page, limit) -> Page[CarSummary]`
- **Query Filter**:
  - `q`: Case-insensitive partial match on `Car.vin`.
  - `state`: Exact match on `Car.state`.
  - `location`: Exact match on `Car.location`.
  - `truck`: Match on `Car.truck_id`.
  - `flags`: Check if JSON flags contain any/all requested tags.
- **Pagination**: Calculate `total`, `offset = (page - 1) * limit`, apply `.offset().limit()`.
- **Return**: `Page[CarSummary]` with total and last_page metadata.

#### 3. `get_car(vin: str) -> CarDetail`
- **Query**: Select `Car` where `vin == vin`, eager-loading `build_sheet`.
- **Error Handling**: Raise `HTTPException(404, detail="Vehicle not found")` if missing.
- **Return**: `CarDetail` matching frontend schema.

#### 4. `get_map() -> YardMap`
- **Resolution**:
  - Query `PlantMapPoint` table for points ordered by `point_id`.
  - If table is empty, parse fallback coordinates from `marshal_ai/plant_map.yaml`.
- **Return**: `YardMap(points=[MapPoint(...)])`.

#### 5. `update_truck_eta(truck_id: str, eta_str: str, reason: Optional[str]) -> dict`
- **Mutation**:
  - Find `Truck` by `truck_id` (or primary ULID).
  - Parse ISO string into UTC datetime.
  - Calculate delay relative to scheduled arrival: `delay_min = (new_eta - scheduled) / 60s`.
  - Update `truck.eta = new_eta`.
- **Side Effect**:
  - If `delay_min > 15`: Emit `late_truck` event to `EventBus`.
  - Update in-memory `PlantState.trucks[truck_id]`.
- **Return**: `{"ok": True, "truck_id": truck_id, "delay_min": delay_min}`.

#### 6. `load_truck(truck_id: str) -> dict`
- **Validation**:
  - Fetch all cars assigned to `truck_id`.
  - Verify every car is in `yard` state. If any vehicle is still in `inspection_gate` or `rework`, raise `HTTPException(400, "Assigned vehicles not ready for shipping")`.
- **Mutation**:
  - Set all assigned vehicles' state to `CarState.SHIPPED`.
  - Mark truck status as `departed`.
- **Return**: `{"ok": True, "shipped_vins": [c.vin for c in cars]}`.

#### 7. `set_zone_closure(zone_name: str, closed: bool, reason: Optional[str]) -> dict`
- **Mutation**:
  - Update or insert `Zone` record in DB: `is_closed = closed`, `reason = reason`.
  - Update `PlantState.closed_zones`: add or remove `zone_name`.
- **Side Effect**:
  - Publish `zone.closure` event to `EventBus` to notify approaching vehicles.
- **Return**: `{"ok": True, "zone": zone_name, "closed": closed}`.

#### 8. `send_mission(vin: str, destination: str, reason: Optional[str]) -> dict`
- **Validation**:
  - Retrieve car; ensure `state != CarState.HELD` and `flags` does not include `"do_not_ship"` or `"held"`.
- **Mutation**:
  - Create a new `Mission` record with status `IN_PROGRESS`.
  - Update car state to `CarState.DRIVING` and `mission_id = mission.id`.
- **Side Effect**:
  - If `mission_client` is connected: Call `await self.mission_client.send(MissionGo(vin=vin, destination=destination))`.
- **Return**: `{"ok": True, "mission_id": mission.id, "state": "driving"}`.

#### 9. `hold_car(vin: str, reason: str, duration_minutes: Optional[int]) -> dict`
- **Mutation**:
  - Update car: `state = CarState.HELD`, append `"held"` to `flags` JSON array.
- **Side Effect**:
  - Emit emergency stop command to `MissionClient`.
- **Return**: `{"ok": True, "vin": vin, "state": "held"}`.

#### 10. `resume_car(vin: str) -> dict`
- **Mutation**:
  - Update car: `state = CarState.WAITING_DISPATCH`, remove `"held"` from `flags`.
- **Side Effect**:
  - Emit resume command to `MissionClient`.
- **Return**: `{"ok": True, "vin": vin, "state": "waiting_dispatch"}`.

---

## 3. Router Rewiring (`src/marshal_api/api/v1/yard.py`)

All stub functions (`_stubs.yard()`, `_stubs.page()`, etc.) are removed. The router delegates 100% of its execution to `YardService`:

```python
@router.get("/yard", response_model=Yard)
async def get_yard(
    service: YardService = Depends(get_yard_service),
) -> Yard:
    return await service.get_yard_snapshot()

@router.get("/cars", response_model=Page[CarSummary])
async def list_cars(
    q: Optional[str] = None,
    state: Optional[str] = None,
    location: Optional[str] = None,
    truck: Optional[str] = None,
    flags: Optional[list[str]] = Query(default=None),
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=20, ge=1, le=200),
    service: YardService = Depends(get_yard_service),
) -> Page[CarSummary]:
    return await service.list_cars(q, state, location, truck, flags, page, limit)

@router.get("/cars/{vin}", response_model=CarDetail)
async def get_car(
    vin: str,
    service: YardService = Depends(get_yard_service),
) -> CarDetail:
    return await service.get_car(vin)
```

---

## 4. Testing & Verification

1. **`test_list_cars_filtering`**: Seed test DB with 10 vehicles in various states; test exact state filtering, substring VIN search, and pagination limits.
2. **`test_hold_and_resume_lifecycle`**: Call `POST /cars/{vin}/hold`, assert DB status is `held`, verify `"held"` flag added, then call `POST /cars/{vin}/resume` and assert state transitions back to `waiting_dispatch`.
3. **`test_truck_load_guard`**: Assign cars to a truck; ensure loading fails if any car is still in `rework`; ensure it succeeds and marks cars as `shipped` once all are in `yard`.
4. **`test_role_enforcement`**: Assert non-supervisors receive HTTP 403 Forbidden when calling `/missions`, `/hold`, `/resume`, `/trucks/*/eta`.

