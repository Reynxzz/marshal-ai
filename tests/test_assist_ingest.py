"""Backend ingest: a CARLA clip in Cloud Storage becomes an assistance case."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlmodel import SQLModel, select

import marshal_api.models  # noqa: F401  (registers tables)
from marshal_ai.events import InMemoryBus
from marshal_ai.flows.assistance import AssistAssessment
from marshal_ai.llm import FakeLLM, Media

from marshal_api.bridge import get_event_bus, get_mission_client, get_plant_state
from marshal_api.core import deps, gcs
from marshal_api.core.deps import get_current_tenant, get_session
from marshal_api.core.errors import ApiError
from marshal_api.main import app
from marshal_api.models import (
    AssistanceCase as AssistanceCaseRow,
    EventLog,
    MediaArtifact,
    Plant,
    Tenant,
)

TENANT = "t1"
CLIP = "gs://marshal-artifacts/assist/AC-1.jpg"


class _Recorder:
    async def send(self, command) -> None:
        pass


def _assessment() -> AssistAssessment:
    return AssistAssessment(
        situation="A cardboard box is in the lane 2.3 m ahead; nobody in view.",
        people_or_moving_equipment=False,
        ranking=[
            {"option": "pass_left_3kmh", "reason": "lane beside the box is clear", "risk": "medium"},
            {"option": "wait_for_removal", "reason": "safe but delays the car", "risk": "low"},
        ],
        recommended="wait_for_removal",
        confidence=0.7,
    )


@pytest.fixture
async def api(tmp_path, monkeypatch):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'ingest.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with maker() as session:
        session.add(Tenant(id=TENANT, name="Alpha", slug="alpha"))
        await session.flush()
        session.add(Plant(id="p1", tenant_id=TENANT, code="P1", name="Plant Alpha"))
        await session.commit()

    async def _session():
        async with maker() as session:
            yield session

    monkeypatch.setattr(
        gcs,
        "load_gcs_media",
        lambda uri, client=None: Media(mime="image/jpeg", data=b"jpeg", uri=uri, label="evidence clip"),
    )
    monkeypatch.setattr(gcs, "download_bytes", lambda uri, client=None: b"jpeg")
    # Deterministic fallback: pretend no signer is available, whatever the environment has.
    monkeypatch.setattr(
        "marshal_api.services.media_service._signed_gcs_url",
        lambda uri, ttl_minutes=60: None,
    )
    llm = FakeLLM().on("assist.assess", lambda call: _assessment())

    app.dependency_overrides[get_session] = _session
    app.dependency_overrides[get_current_tenant] = lambda: TENANT
    app.dependency_overrides[get_event_bus] = lambda: InMemoryBus()
    app.dependency_overrides[get_mission_client] = lambda: _Recorder()
    app.dependency_overrides[get_plant_state] = lambda: None
    app.dependency_overrides[deps.get_llm] = lambda: llm

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client, maker

    app.dependency_overrides.clear()
    await engine.dispose()


async def test_ingest_clip_opens_case_with_gemini_recommendation(api) -> None:
    client, maker = api
    response = await client.post(
        "/api/v1/assistance/cases",
        json={
            "clip_uri": CLIP,
            "vin": "VIN-OK",
            "location": "test_track",
            "options": ["wait_for_removal", "pass_left_3kmh", "reroute_lane_c"],
        },
        headers={"X-Actor-Role": "supervisor", "X-Tenant-ID": TENANT},
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["case_id"].startswith("AC-")
    assert body["status"] == "OPEN" and body["vin"] == "VIN-OK"
    assert body["recommendation"]["option_id"] == "wait_for_removal"
    assert [o["id"] for o in body["options"]] == [
        "pass_left_3kmh",
        "wait_for_removal",
        "reroute_lane_c",
    ]
    assert len(body["media_ids"]) == 1

    async with maker() as session:
        case = (
            await session.execute(
                select(AssistanceCaseRow).where(AssistanceCaseRow.case_id == body["case_id"])
            )
        ).scalars().one()
        assert case.clip_media_id is not None
        artifact = (
            await session.execute(select(MediaArtifact).where(MediaArtifact.storage_uri == CLIP))
        ).scalars().one()
        assert artifact.kind == "photo" and artifact.content_type == "image/jpeg"
        event = (await session.execute(select(EventLog))).scalars().one()
        assert event.seq == 1 and event.flow_number == 2

    listing = (
        await client.get("/api/v1/assistance/cases", headers={"X-Tenant-ID": TENANT})
    ).json()
    assert listing["pagination"]["total"] == 1

    media = (
        await client.get(f"/api/v1/media/{body['media_ids'][0]}", headers={"X-Tenant-ID": TENANT})
    ).json()
    assert media["available"] is True and media["kind"] == "image"
    assert media["url"] == f"/api/v1/media/{body['media_ids'][0]}/file"

    file_response = await client.get(media["url"], headers={"X-Tenant-ID": TENANT})
    assert file_response.status_code == 200
    assert file_response.headers["content-type"] == "image/jpeg"
    assert file_response.content == b"jpeg"


async def test_ingest_requires_an_option(api) -> None:
    client, _ = api
    response = await client.post(
        "/api/v1/assistance/cases",
        json={"clip_uri": CLIP, "vin": "VIN-OK", "options": []},
        headers={"X-Actor-Role": "supervisor", "X-Tenant-ID": TENANT},
    )
    assert response.status_code == 422 and response.json()["code"] == "validation_error"


def test_only_gs_uris_are_accepted() -> None:
    with pytest.raises(ApiError) as exc:
        gcs.load_gcs_media("https://example.com/not-a-clip.jpg")
    assert exc.value.status_code == 422 and exc.value.code == "invalid_media_uri"
