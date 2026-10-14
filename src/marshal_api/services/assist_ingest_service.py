"""Flow 2, offline: turn a CARLA clip already in Cloud Storage into an assistance case.

The CARLA host records evidence for every verdict (``marshal_ai.artifacts``) and the
driving system raises ``assist.request`` live. The same case can be built from the
stored clip alone — for a replay, or when the car is not on the server any more:
Gemini reads it and ranks the options the driving system would have offered, and the
desk's rules are enforced in code. A supervisor then decides on the dashboard,
exactly as with a live stop.
"""

from __future__ import annotations

import asyncio
import logging

from sqlalchemy import func
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from marshal_ai.events import EventBus, Topic
from marshal_ai.flows.assistance import SYSTEM, AssistAssessment, enforce_rules
from marshal_ai.llm import LLM

from marshal_api.core import gcs
from marshal_api.core.errors import ApiError
from marshal_api.models import (
    AssistanceCase as AssistanceCaseRow,
    AssistanceOption as AssistanceOptionRow,
    AssistanceStatus,
    Car,
    CarState,
    EventLog,
    MediaArtifact,
    Plant,
)
from marshal_api.models.assistance import RiskLevel
from marshal_api.models.base import generate_ulid

log = logging.getLogger("marshal.api.assist_ingest")


class AssistIngestService:
    def __init__(
        self,
        session: AsyncSession,
        llm: LLM,
        event_bus: EventBus | None = None,
        tenant_id: str = "tenant-demo",
    ) -> None:
        self.session = session
        self.llm = llm
        self.event_bus = event_bus
        self.tenant_id = tenant_id

    async def ingest(
        self,
        *,
        clip_uri: str,
        vin: str,
        location: str,
        options: list[str],
        situation: str | None = None,
    ) -> str:
        """Assess the stored clip and open an assistance case. Returns its case_id."""
        options = [o for o in options if o]
        if not options:
            raise ApiError(422, "validation_error", "at least one option is required")

        plant_id = await self._plant_id()
        car = await self._car(vin, location, plant_id)
        media = await asyncio.to_thread(gcs.load_gcs_media, clip_uri)
        artifact = await self._artifact(plant_id, clip_uri, media)

        prompt = (
            f"Car {vin} is stopped at map point {location}.\n"
            f"Offered options: {options}\n"
            "Read the evidence clip and rank those options."
        )
        try:
            assessment = await asyncio.to_thread(
                self.llm.generate, "assist.assess", prompt, AssistAssessment, [media], SYSTEM
            )
        except ApiError:
            raise
        except Exception as exc:
            raise ApiError(
                502, "assessment_failed", f"Gemini could not assess the clip: {exc}"
            ) from exc

        assessment, _notes = enforce_rules(assessment, options, frames=1)

        case = AssistanceCaseRow(
            tenant_id=self.tenant_id,
            plant_id=plant_id,
            case_id=f"AC-{generate_ulid()[:6]}",
            car_id=car.id,
            location=location,
            situation=situation or assessment.situation or "unknown object in lane",
            status=AssistanceStatus.OPEN,
            moving_people_or_equipment=assessment.people_or_moving_equipment,
            recommended_option_id=assessment.recommended,
            confidence=assessment.confidence,
            clip_media_id=artifact.id,
        )
        car.state = CarState.EXCEPTION
        self.session.add(car)
        self.session.add(case)
        await self.session.flush()
        for rank, ranked in enumerate(assessment.ranking, start=1):
            self.session.add(
                AssistanceOptionRow(
                    case_id=case.id,
                    option_id=ranked.option,
                    label=ranked.option.replace("_", " "),
                    risk=RiskLevel(ranked.risk),
                    reason=ranked.reason,
                    rank=rank,
                )
            )

        await self._log_event(plant_id, case.case_id, assessment)
        await self.session.commit()
        log.info("assist case %s opened from %s", case.case_id, clip_uri)
        return case.case_id

    async def _plant_id(self) -> str:
        plant = (
            await self.session.execute(select(Plant).where(Plant.tenant_id == self.tenant_id))
        ).scalars().first()
        if plant is None:
            raise ApiError(400, "no_plant", "No plant configured for this tenant")
        return plant.id

    async def _car(self, vin: str, location: str, plant_id: str) -> Car:
        car = (
            await self.session.execute(
                select(Car).where(Car.tenant_id == self.tenant_id, Car.vin == vin)
            )
        ).scalars().first()
        if car is None:
            car = Car(
                tenant_id=self.tenant_id,
                plant_id=plant_id,
                vin=vin,
                state=CarState.EXCEPTION,
                location=location,
            )
            self.session.add(car)
            await self.session.flush()
        return car

    async def _artifact(self, plant_id: str, uri: str, media) -> MediaArtifact:
        artifact = MediaArtifact(
            tenant_id=self.tenant_id,
            plant_id=plant_id,
            media_id=f"MED-{generate_ulid()[:8]}",
            kind="clip" if media.mime.startswith("video/") else "photo",
            storage_uri=uri,
            content_type=media.mime,
            file_size_bytes=len(media.data or b""),
            available=True,
        )
        self.session.add(artifact)
        await self.session.flush()
        return artifact

    async def _log_event(self, plant_id: str, case_id: str, assessment: AssistAssessment) -> None:
        """One row for the dashboard's feed, and the same message on the live bus."""
        seq = (
            await self.session.execute(
                select(func.max(EventLog.seq)).where(EventLog.tenant_id == self.tenant_id)
            )
        ).scalar() or 0
        level = "warn" if assessment.people_or_moving_equipment else "info"
        title = f"Assist case {case_id}: {assessment.situation[:120]}"
        detail = (
            f"recommended {assessment.recommended}"
            if assessment.recommended
            else "no safe option; a human decides"
        )
        self.session.add(
            EventLog(
                tenant_id=self.tenant_id,
                plant_id=plant_id,
                seq=seq + 1,
                flow_number=2,
                source="assistance",
                title=title,
                detail=detail,
                level=level,
                payload={"case_id": case_id},
            )
        )
        if self.event_bus is not None:
            await self.event_bus.publish(
                Topic.ASSIST_REQUESTS,
                {
                    "type": "assist.request",
                    "flow": 2,
                    "source": "assistance",
                    "title": title,
                    "detail": detail,
                    "level": level,
                    "case_id": case_id,
                },
            )
