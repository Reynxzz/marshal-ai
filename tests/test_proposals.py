import pytest

from marshal_ai.config import Settings
from marshal_ai.records import Proposal, ProposalStatus
from marshal_ai.runtime import build_runtime

OFFLINE = Settings(bus="memory", store="memory", driving="fake", llm="offline")


def test_a_proposal_is_stored_where_the_dashboard_reads_it(app):
    p = app.rt.propose("replan", "Hold T-1's cars at the line end", {"kind": "truck_delayed"})

    stored = app.rt.store.get(Proposal, p.proposal_id)
    assert stored is not None
    assert stored.status == ProposalStatus.PENDING
    assert stored.payload == {"kind": "truck_delayed"}


def test_the_decision_is_stored_with_who_and_when(app):
    p = app.rt.propose("rework", "Rework for VIN-223: rework.electrical", {"vin": "VIN-223"})

    app.rt.decide(p.proposal_id, "rework", True, "supervisor-ana", what="rework proposal")

    stored = app.rt.store.get(Proposal, p.proposal_id)
    assert stored.status == ProposalStatus.APPROVED
    assert stored.decided_by == "supervisor-ana" and stored.decided_at is not None
    assert app.rt.pending("rework") == []


def test_only_a_pending_proposal_of_that_kind_can_be_decided(app):
    p = app.rt.propose("review", "Visual review for VIN-245", {"vin": "VIN-245"})

    with pytest.raises(ValueError, match="not a pending re-plan"):
        app.rt.decide(p.proposal_id, "replan", True, "supervisor-ana", what="re-plan")
    app.rt.decide(p.proposal_id, "review", False, "supervisor-ana", what="visual review")
    with pytest.raises(ValueError, match="not a pending visual review"):
        app.rt.decide(p.proposal_id, "review", True, "supervisor-ana", what="visual review")
    with pytest.raises(ValueError):
        app.rt.decide("P-999", "review", True, "supervisor-ana", what="visual review")


def test_a_later_process_does_not_reuse_an_earlier_proposals_id(app):
    """Each demo phase is its own process; a fresh counter would overwrite P-001 in Firestore."""
    first = app.rt.propose("replan", "first run's re-plan", {})
    later = build_runtime(OFFLINE)
    later.store = app.rt.store                       # the same Firestore, a new process

    second = later.propose("replan", "second run's re-plan", {})

    assert second.proposal_id != first.proposal_id
    assert later.store.get(Proposal, first.proposal_id).summary == "first run's re-plan"


def test_a_new_run_does_not_act_on_an_earlier_runs_open_proposal(app):
    app.rt.propose("rework", "left open by an earlier run", {"vin": "VIN-OLD"})
    later = build_runtime(OFFLINE)
    later.store = app.rt.store

    assert later.pending("rework") == []


async def test_a_flow_decision_reaches_the_store(plant):
    app = plant(["VIN-245"])
    await app.run_for(60)
    [review] = app.rt.pending("review")

    await app.visual.decide_review(review.proposal_id, confirm_defect=False, by="supervisor-ana")

    stored = app.rt.store.get(Proposal, review.proposal_id)
    assert stored.kind == "review" and stored.status == ProposalStatus.REJECTED
    assert stored.decided_by == "supervisor-ana"
