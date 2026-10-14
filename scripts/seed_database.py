"""Seed script for Marshal Control Tower database.

Populates the database with realistic demo data (Tenant, Plant, Trucks, Zones,
Rework Bays, Map Points, Cars, BuildSheets, Assistance Cases, Proposals,
Inspection Results, and Scenarios) matching the Control Tower UI fixtures.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from sqlmodel import select

import marshal_api.models  # noqa: F401
from marshal_api.db.session import async_session_maker, engine
from marshal_api.models import (
    AssistanceCase,
    AssistanceOption,
    BuildSheet,
    Car,
    CarReport,
    CarState,
    InspectionResult,
    Plant,
    PlantMapPoint,
    Proposal,
    Release,
    ReworkBay,
    Scenario,
    Tenant,
    TestRequest,
    Truck,
    Verdict,
    VisualFinding,
    Zone,
)
from marshal_api.models.assistance import AssistanceStatus, RiskLevel
from marshal_api.models.lab import ScenarioStatus

TENANT_ID = "tenant-demo"
PLANT_ID = "plant-01"


async def seed() -> None:
    now = datetime.now(timezone.utc)

    async with async_session_maker() as session:
        # Check if already seeded
        existing_tenant = await session.get(Tenant, TENANT_ID)
        if existing_tenant:
            print("Database already contains demo tenant. Skipping seed.")
            return

        print("Seeding database with demo data...")

        # 1. Tenant & Plant
        tenant = Tenant(id=TENANT_ID, name="Marshal Operations", slug="marshal-ops")
        plant = Plant(id=PLANT_ID, tenant_id=TENANT_ID, code="P1", name="Plant 01")
        session.add(tenant)
        session.add(plant)
        await session.flush()

        # 2. Trucks
        t1 = Truck(
            id="trk-01",
            tenant_id=TENANT_ID,
            plant_id=PLANT_ID,
            code="TRK-01",
            capacity=8,
            status="loading",
            eta=now + timedelta(minutes=25),
        )
        t2 = Truck(
            id="trk-02",
            tenant_id=TENANT_ID,
            plant_id=PLANT_ID,
            code="TRK-02",
            capacity=8,
            status="en_route",
            eta=now + timedelta(minutes=50),
        )
        session.add_all([t1, t2])
        await session.flush()

        # 3. Zones
        zones = [
            Zone(id="z-1", tenant_id=TENANT_ID, plant_id=PLANT_ID, code="ZONE-A", name="Staging A"),
            Zone(id="z-2", tenant_id=TENANT_ID, plant_id=PLANT_ID, code="ZONE-B", name="Staging B"),
            Zone(id="z-3", tenant_id=TENANT_ID, plant_id=PLANT_ID, code="ZONE-C", name="Holding C"),
            Zone(id="z-4", tenant_id=TENANT_ID, plant_id=PLANT_ID, code="GATE-IN", name="Gate Masuk"),
            Zone(id="z-5", tenant_id=TENANT_ID, plant_id=PLANT_ID, code="GATE-OUT", name="Gate Keluar"),
        ]
        session.add_all(zones)
        await session.flush()

        # 4. Rework Bays
        bays = [
            ReworkBay(id="bay-1", tenant_id=TENANT_ID, plant_id=PLANT_ID, code="BAY-PAINT", name="Paint Touch-up Bay (20 min)", defect_type="paint", capacity=3),
            ReworkBay(id="bay-2", tenant_id=TENANT_ID, plant_id=PLANT_ID, code="BAY-BODY", name="Body Fit Bay (30 min)", defect_type="body", capacity=3),
            ReworkBay(id="bay-3", tenant_id=TENANT_ID, plant_id=PLANT_ID, code="BAY-ELEC", name="Electrical Bay (25 min)", defect_type="electrical", capacity=2),
            ReworkBay(id="bay-4", tenant_id=TENANT_ID, plant_id=PLANT_ID, code="BAY-MECH", name="Mechanical Bay (45 min)", defect_type="mechanical", capacity=2),
            ReworkBay(id="bay-5", tenant_id=TENANT_ID, plant_id=PLANT_ID, code="BAY-HOLD", name="Production Control Hold (Manual)", defect_type="hold", capacity=5),
        ]
        session.add_all(bays)
        await session.flush()

        # 5. Plant Map Points
        map_points = [
            PlantMapPoint(id="mp-1", tenant_id=TENANT_ID, plant_id=PLANT_ID, code="GATE-IN", name="Gate Masuk", kind="gate", x=8.0, y=50.0),
            PlantMapPoint(id="mp-2", tenant_id=TENANT_ID, plant_id=PLANT_ID, code="INSPECT", name="Inspection Line", kind="lane", x=24.0, y=50.0),
            PlantMapPoint(id="mp-3", tenant_id=TENANT_ID, plant_id=PLANT_ID, code="STAGING", name="Yard Staging", kind="zone", x=50.0, y=35.0),
            PlantMapPoint(id="mp-4", tenant_id=TENANT_ID, plant_id=PLANT_ID, code="REWORK", name="Rework Bays", kind="bay", x=50.0, y=65.0),
            PlantMapPoint(id="mp-5", tenant_id=TENANT_ID, plant_id=PLANT_ID, code="GATE-OUT", name="Gate Keluar / Loading", kind="gate", x=85.0, y=50.0),
        ]
        session.add_all(map_points)
        await session.flush()

        # 6. Cars & Build Sheets
        c1 = Car(id="c-214", tenant_id=TENANT_ID, plant_id=PLANT_ID, vin="VIN-214", state=CarState.YARD, location="yard.slot-01", truck_id="trk-01")
        c2 = Car(id="c-223", tenant_id=TENANT_ID, plant_id=PLANT_ID, vin="VIN-223", state=CarState.REWORK, location="bay.BAY-ELEC", truck_id="trk-01", flags=["defect_electrical"])
        c3 = Car(id="c-231", tenant_id=TENANT_ID, plant_id=PLANT_ID, vin="VIN-231", state=CarState.REVIEW, location="inspection.gate-01", truck_id="trk-02", flags=["build_mismatch"])
        c4 = Car(id="c-240", tenant_id=TENANT_ID, plant_id=PLANT_ID, vin="VIN-240", state=CarState.HELD, location="lane.finishing-02", truck_id="trk-02", flags=["assist_required", "held"])
        c5 = Car(id="c-245", tenant_id=TENANT_ID, plant_id=PLANT_ID, vin="VIN-245", state=CarState.REVIEW, location="bay.BAY-PAINT", truck_id="trk-01", flags=["scratch_inspection"])
        session.add_all([c1, c2, c3, c4, c5])
        await session.flush()

        bs1 = BuildSheet(id="bs-214", car_id="c-214", model="Sedan Alpha", color="blue", options=["rear_badge", "sunroof"])
        bs2 = BuildSheet(id="bs-223", car_id="c-223", model="Sedan Alpha", color="blue", options=["rear_badge", "led_package"])
        bs3 = BuildSheet(id="bs-231", car_id="c-231", model="Sedan Alpha", color="white", options=["rear_badge", "spoiler"])
        bs4 = BuildSheet(id="bs-240", car_id="c-240", model="Sedan Alpha", color="grey", options=["rear_badge", "comfort_pack"])
        bs5 = BuildSheet(id="bs-245", car_id="c-245", model="Sedan Alpha", color="blue", options=["rear_badge", "sport_wheels"])
        session.add_all([bs1, bs2, bs3, bs4, bs5])

        # 7. Assistance Case & Options (for VIN-240 stuck at box in lane)
        case1 = AssistanceCase(
            id="ac-01",
            tenant_id=TENANT_ID,
            plant_id=PLANT_ID,
            case_id="AC-1",
            car_id="c-240",
            location="Lane Finishing - Waypoint 4",
            situation="Cardboard box obstacle in lane near finishing; worker detected 3m away",
            status=AssistanceStatus.OPEN,
            moving_people_or_equipment=True,
            recommended_option_id="opt-wait",
            confidence=0.88,
        )
        session.add(case1)
        await session.flush()

        opt1 = AssistanceOption(
            id="opt-1",
            case_id="ac-01",
            option_id="opt-wait",
            label="Wait for worker to clear obstacle (recommended)",
            risk=RiskLevel.LOW,
            reason="Worker is within 3 meters and actively moving boxes; safest protocol.",
            rank=1,
        )
        opt2 = AssistanceOption(
            id="opt-2",
            case_id="ac-01",
            option_id="opt-reroute",
            label="Reroute via Lane C (+2 min)",
            risk=RiskLevel.LOW,
            reason="Lane C is clear with no detected human activity.",
            rank=2,
        )
        opt3 = AssistanceOption(
            id="opt-3",
            case_id="ac-01",
            option_id="opt-creep",
            label="Creep pass left at 3 km/h",
            risk=RiskLevel.MEDIUM,
            reason="Sufficient clearance to bypass box on the left, but worker is nearby.",
            rank=3,
        )
        session.add_all([opt1, opt2, opt3])

        # 8. Proposal for supervisor review
        prop1 = Proposal(
            id="pr-01",
            tenant_id=TENANT_ID,
            plant_id=PLANT_ID,
            proposal_id="PROP-01",
            kind="rework",
            category="Quality Rework",
            car_id="c-245",
            summary="Approve Paint Touch-up for 6cm scratch on rear left door before yard dispatch",
            status="pending",
            allowed_actions=["approve", "reject", "defer"],
            payload={"car_vin": "VIN-245", "defect": "scratch", "size_cm": 6.0, "bay": "BAY-PAINT"},
            effects={"approve": "Route VIN-245 to BAY-PAINT for touch-up", "reject": "Keep VIN-245 on hold"},
        )
        session.add(prop1)

        # 9. Inspection Results & Findings
        insp1 = InspectionResult(
            id="ir-01",
            tenant_id=TENANT_ID,
            plant_id=PLANT_ID,
            result_id="INSP-223-FUNC",
            car_id="c-223",
            kind="functional",
            verdict=Verdict.FAIL,
            review_status="DONE",
            reviewed_by="inspector-demo",
            review_decision="SEND_TO_REWORK",
        )
        insp2 = InspectionResult(
            id="ir-02",
            tenant_id=TENANT_ID,
            plant_id=PLANT_ID,
            result_id="INSP-245-VIS",
            car_id="c-245",
            kind="visual",
            verdict=Verdict.REVIEW,
            review_status="PENDING",
            proposal_id="pr-01",
        )
        session.add_all([insp1, insp2])
        await session.flush()

        vf1 = VisualFinding(
            id="vf-01",
            inspection_result_id="ir-02",
            zone="rear_left_door",
            defect_type="scratch",
            size_cm=6.0,
            confidence=0.92,
            observation="Surface scratch through clear coat, approx 6 cm length.",
        )
        session.add(vf1)

        # 9.5 Car Reports
        rep1 = CarReport(
            id="rep-223",
            tenant_id=TENANT_ID,
            plant_id=PLANT_ID,
            car_id="c-223",
            verdict=Verdict.FAIL,
            summary="Functional inspection failed (mirror fold test). Rework required.",
            reinspection_status="waiting",
        )
        rep2 = CarReport(
            id="rep-245",
            tenant_id=TENANT_ID,
            plant_id=PLANT_ID,
            car_id="c-245",
            verdict=Verdict.REVIEW,
            summary="Visual inspection identified scratch on rear left door. Under supervisor review.",
            reinspection_status="not_required",
        )
        session.add_all([rep1, rep2])
        await session.flush()

        # 10. Lab Test Requests, Scenarios & Release
        scen1 = Scenario(
            id="sc-01",
            tenant_id=TENANT_ID,
            plant_id=PLANT_ID,
            scenario_id="SCEN-BOX-LANE",
            source="incident_AC-1",
            status=ScenarioStatus.VALID,
            tags=["obstacle", "human_worker", "lane_navigation"],
        )
        session.add(scen1)
        await session.flush()

        tr1 = TestRequest(
            id="tr-01",
            tenant_id=TENANT_ID,
            plant_id=PLANT_ID,
            request_id="REQ-01",
            source="incident_AC-1",
            text="Generate regression tests for static cardboard obstacle with nearby pedestrian",
            status="converted",
            scenario_id="sc-01",
        )
        session.add(tr1)

        rel1 = Release(
            id="rel-01",
            tenant_id=TENANT_ID,
            release_id="REL-1.2.0",
            name="Release v1.2.0-rc1",
            gate="passed",
            evaluation_status="complete",
            passed_count=42,
            failed_count=0,
        )
        session.add(rel1)

        await session.commit()
        print("Database seeded successfully with demo data!")


if __name__ == "__main__":
    asyncio.run(seed())
