"""Yard operations: cars, fleet actions, zones (Flow 1).

The DB carries the durable records (cars, trucks, zones, bays); the live, fast-
changing values (truck ETAs, closed zones) live in the in-memory ``PlantState``
bridge, because the schema has no columns for them yet.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy.orm import selectinload
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from marshal_ai.driving import MissionClient, MissionGo, MissionHold, MissionResume
from marshal_ai.events import EventBus, Topic
from marshal_ai.plant import load_plant_map
from marshal_ai.plant.state import PlantState, Truck as PlantTruck

from marshal_api.core.errors import ApiError
from marshal_api.models import (
    BuildSheet as BuildSheetRow,
    Car,
    CarState,
    Mission,
    Plant,
    PlantMapPoint,
    ReworkBay as ReworkBayRow,
    Truck as TruckRow,
    Zone as ZoneRow,
)
from marshal_api.models.base import utc_now
from marshal_api.schemas import (
    BuildSheet,
    CarDetail,
    CarSummary,
    MapPoint,
    Page,
    Pagination,
    ReworkBay,
    Truck,
    Yard,
    YardMap,
    Zone,
)
from marshal_api.schemas.common import CarState as DashboardState
from marshal_api.schemas.yard import MapPointKind, ReworkBayStatus, YardSummary

log = logging.getLogger("marshal.api.yard")

# The DB tracks 13 process states; the dashboard has 8. This is the projection.
_DASHBOARD_STATE: dict[CarState, DashboardState] = {
    CarState.LINE_END: DashboardState.READY,
    CarState.INSPECTION_GATE: DashboardState.INSPECTING,
    CarState.REVIEW: DashboardState.INSPECTING,
    CarState.TEST_TRACK: DashboardState.INSPECTING,
    CarState.REPORT: DashboardState.INSPECTING,
    CarState.REWORK: DashboardState.REWORK,
    CarState.YARD: DashboardState.PARKED,
    CarState.SHIPPED: DashboardState.READY,
    CarState.PARKED: DashboardState.PARKED,
    CarState.DRIVING: DashboardState.DRIVING,
    CarState.HELD: DashboardState.HELD,
    CarState.WAITING_DISPATCH: DashboardState.WAITING_DISPATCH,
    CarState.EXCEPTION: DashboardState.EXCEPTION,
}


def _parse_eta(value: str) -> datetime:
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def rework_bay_dto(bay: ReworkBayRow, plant_state: PlantState | None) -> ReworkBay:
    """Shared by the yard snapshot and the rework-bays endpoint."""
    occupants = list(plant_state.bay_occupants.get(bay.code, [])) if plant_state else []
    capacity = bay.capacity or 1
    full = len(occupants) >= capacity
    return ReworkBay(
        bay_id=bay.code,
        name=bay.name,
        capacity=capacity,
        occupied=len(occupants),
        status=ReworkBayStatus.FULL if full else ReworkBayStatus.OPEN,
        active_vins=occupants,
    )


class YardService:
    def __init__(
        self,
        session: AsyncSession,
        mission_client: Optional[MissionClient] = None,
        event_bus: Optional[EventBus] = None,
        plant_state: Optional[PlantState] = None,
        tenant_id: str = "tenant-demo",
    ) -> None:
        self.session = session
        self.mission_client = mission_client
        self.event_bus = event_bus
        self.plant_state = plant_state
        self.tenant_id = tenant_id

    async def _all(self, stmt):
        return (await self.session.execute(stmt)).scalars().all()

    async def _first(self, stmt):
        return (await self.session.execute(stmt)).scalars().first()

    # ------------------------------------------------------------------ reads

    def _car_summary(self, car: Car) -> CarSummary:
        sheet = car.build_sheet
        return CarSummary(
            vin=car.vin,
            model=sheet.model if sheet else None,
            color=sheet.color if sheet else None,
            state=_DASHBOARD_STATE.get(car.state, DashboardState.EXCEPTION),
            location=car.location,
            mission=car.mission_id,
            truck_id=car.truck_id,
            slot=car.yard_slot,
            flags=list(car.flags or []),
            updated_at=car.updated_at,
        )

    def _truck_dto(self, truck: TruckRow, cars: list[Car]) -> Truck:
        live = self.plant_state.trucks.get(truck.code) if self.plant_state else None
        return Truck(
            truck_id=truck.code,
            scheduled_eta=live.scheduled if live else None,
            actual_eta=live.eta if live else None,
            delay_minutes=live.delay_min if live else None,
            assigned_vins=[c.vin for c in cars if c.truck_id == truck.id],
        )

    def _bay_dto(self, bay: ReworkBayRow) -> ReworkBay:
        return rework_bay_dto(bay, self.plant_state)

    async def _get_car(self, vin: str) -> Car:
        car = await self._first(
            select(Car).where(Car.tenant_id == self.tenant_id, Car.vin == vin)
        )
        if car is None:
            raise ApiError(404, "not_found", f"Vehicle {vin} not found")
        return car

    async def get_yard_snapshot(self) -> Yard:
        cars = await self._all(
            select(Car)
            .where(Car.tenant_id == self.tenant_id)
            .options(selectinload(Car.build_sheet))
        )
        trucks = await self._all(
            select(TruckRow).where(TruckRow.tenant_id == self.tenant_id)
        )
        zones = await self._all(select(ZoneRow).where(ZoneRow.tenant_id == self.tenant_id))
        bays = await self._all(
            select(ReworkBayRow).where(ReworkBayRow.tenant_id == self.tenant_id)
        )
        plant = await self._first(select(Plant).where(Plant.tenant_id == self.tenant_id))

        closed = self.plant_state.closed_zones if self.plant_state else set()
        return Yard(
            plant=plant.name if plant else "Plant 01",
            updated_at=utc_now(),
            summary=YardSummary(
                total=len(cars),
                exception=sum(1 for c in cars if c.state == CarState.EXCEPTION),
                held=sum(1 for c in cars if c.state == CarState.HELD),
                zones_closed=len(closed),
            ),
            cars=[self._car_summary(c) for c in cars],
            trucks=[self._truck_dto(t, cars) for t in trucks],
            zones=[
                Zone(zone=z.code, name=z.name, closed=z.code in closed) for z in zones
            ],
            bays=[self._bay_dto(b) for b in bays],
        )

    async def list_cars(
        self,
        q: str | None = None,
        state: str | None = None,
        location: str | None = None,
        truck: str | None = None,
        flags: list[str] | None = None,
        page: int = 1,
        limit: int = 20,
    ) -> Page[CarSummary]:
        stmt = (
            select(Car)
            .where(Car.tenant_id == self.tenant_id)
            .options(selectinload(Car.build_sheet))
        )
        if q:
            stmt = stmt.where(Car.vin.ilike(f"%{q}%"))
        if state:
            try:
                stmt = stmt.where(Car.state == CarState(state))
            except ValueError:
                raise ApiError(400, "invalid_state", f"Unknown car state '{state}'")
        if location:
            stmt = stmt.where(Car.location == location)
        if truck:
            stmt = stmt.where(Car.truck_id == truck)
        rows = list(await self._all(stmt.order_by(Car.vin)))
        if flags:
            wanted = set(flags)
            # shortcut: flag filter is in-process (JSON contains is not portable across SQLite/PG)
            rows = [c for c in rows if wanted & set(c.flags or [])]
        # shortcut: in-memory slicing after fetch; move to SQL offset/limit if the yard grows large
        total = len(rows)
        start = (page - 1) * limit
        items = [self._car_summary(c) for c in rows[start : start + limit]]
        return Page(
            items=items,
            pagination=Pagination(
                page=page, limit=limit, total=total, last_page=max(1, -(-total // limit))
            ),
        )

    async def get_car(self, vin: str) -> CarDetail:
        car = await self._first(
            select(Car)
            .where(Car.tenant_id == self.tenant_id, Car.vin == vin)
            .options(selectinload(Car.build_sheet))
        )
        if car is None:
            raise ApiError(404, "not_found", f"Vehicle {vin} not found")
        sheet = car.build_sheet
        build_sheet = (
            BuildSheet(
                model=sheet.model,
                trim=sheet.trim,
                color=sheet.color,
                options=list(sheet.options or []),
            )
            if sheet
            else BuildSheet(model="UNKNOWN", color="UNKNOWN")
        )
        return CarDetail(
            **self._car_summary(car).model_dump(),
            build_sheet=build_sheet,
            created_at=car.created_at,
        )

    async def get_map(self) -> YardMap:
        rows = await self._all(
            select(PlantMapPoint)
            .where(PlantMapPoint.tenant_id == self.tenant_id)
            .order_by(PlantMapPoint.code)
        )
        if rows:
            points = [self._map_point(p.code, p.name, p.kind, p.x, p.y) for p in rows]
        else:
            points = [
                MapPoint(id=name, name=name, kind=MapPointKind.POINT, x=p.x, y=p.y)
                for name, p in load_plant_map().points.items()
            ]
        return YardMap(points=points)

    @staticmethod
    def _map_point(point_id: str, name: str, kind: str, x: float, y: float) -> MapPoint:
        try:
            mapped = MapPointKind(kind)
        except ValueError:
            mapped = MapPointKind.POINT
        return MapPoint(id=point_id, name=name, kind=mapped, x=x, y=y)

    # -------------------------------------------------------------- mutations

    async def update_truck_eta(
        self, truck_id: str, eta_str: str, reason: str | None = None
    ) -> dict:
        eta = _parse_eta(eta_str)
        live = self.plant_state.trucks.get(truck_id) if self.plant_state else None
        scheduled = live.scheduled if live else eta
        delay = (eta - scheduled).total_seconds() / 60.0
        if self.plant_state is not None:
            self.plant_state.trucks[truck_id] = PlantTruck(
                truck_id=truck_id, scheduled=scheduled, eta=eta
            )
        if delay > 15 and self.event_bus is not None:
            await self.event_bus.publish(
                Topic.VEHICLE_STATUS,
                {"type": "late_truck", "truck_id": truck_id, "delay_min": delay, "reason": reason},
            )
        return {"ok": True, "truck_id": truck_id, "delay_min": delay}

    async def load_truck(self, truck_id: str) -> dict:
        truck = await self._first(
            select(TruckRow).where(
                TruckRow.tenant_id == self.tenant_id,
                (TruckRow.id == truck_id) | (TruckRow.code == truck_id),
            )
        )
        key = truck.id if truck else truck_id
        cars = await self._all(
            select(Car).where(Car.tenant_id == self.tenant_id, Car.truck_id == key)
        )
        not_ready = [c.vin for c in cars if c.state != CarState.YARD]
        if not_ready:
            raise ApiError(
                400, "truck_not_ready", f"Assigned vehicles not ready for shipping: {not_ready}"
            )
        shipped = [c.vin for c in cars]
        for car in cars:
            car.state = CarState.SHIPPED
            self.session.add(car)
        if truck is not None:
            truck.status = "departed"
            self.session.add(truck)
        await self.session.commit()
        return {"ok": True, "shipped_vins": shipped}

    async def set_zone_closure(
        self, zone_name: str, closed: bool, reason: str | None = None
    ) -> dict:
        if self.plant_state is not None:
            if closed:
                self.plant_state.closed_zones.add(zone_name)
            else:
                self.plant_state.closed_zones.discard(zone_name)
        if self.event_bus is not None:
            await self.event_bus.publish(
                Topic.VEHICLE_STATUS,
                {"type": "zone.closure", "zone": zone_name, "closed": closed, "reason": reason},
            )
        return {"ok": True, "zone": zone_name, "closed": closed}

    async def send_mission(self, vin: str, destination: str, reason: str | None = None) -> dict:
        car = await self._get_car(vin)
        if car.state == CarState.HELD or "held" in (car.flags or []) or car.do_not_ship:
            raise ApiError(400, "car_unavailable", f"Vehicle {vin} cannot be dispatched")
        mission = Mission(
            tenant_id=car.tenant_id,
            plant_id=car.plant_id,
            car_id=car.id,
            mission_type="go",
            destination=destination,
            status="in_progress",
        )
        self.session.add(mission)
        await self.session.flush()
        car.state = CarState.DRIVING
        car.mission_id = mission.id
        self.session.add(car)
        await self.session.commit()
        await self._dispatch(MissionGo(mission_id=mission.id, vin=vin, waypoints=[destination]))
        return {"ok": True, "mission_id": mission.id, "state": "driving"}

    async def hold_car(
        self, vin: str, reason: str, duration_minutes: int | None = None
    ) -> dict:
        car = await self._get_car(vin)
        car.state = CarState.HELD
        flags = list(car.flags or [])
        if "held" not in flags:
            flags.append("held")
        car.flags = flags
        self.session.add(car)
        await self.session.commit()
        await self._dispatch(
            MissionHold(
                vin=vin,
                duration_s=duration_minutes * 60 if duration_minutes else None,
                reason=reason,
            )
        )
        return {"ok": True, "vin": vin, "state": "held"}

    async def resume_car(self, vin: str) -> dict:
        car = await self._get_car(vin)
        car.state = CarState.WAITING_DISPATCH
        car.flags = [f for f in (car.flags or []) if f != "held"]
        self.session.add(car)
        await self.session.commit()
        await self._dispatch(MissionResume(vin=vin))
        return {"ok": True, "vin": vin, "state": "waiting_dispatch"}

    async def _dispatch(self, command) -> None:
        """Best-effort: a missing/simulating driving backend must never fail the request."""
        if self.mission_client is None:
            return
        try:
            await self.mission_client.send(command)
        except Exception:
            log.exception("driving dispatch failed: %s", command)
