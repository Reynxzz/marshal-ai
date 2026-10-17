"""Gate findings are checked against the latest car that passed the same camera (dataset v6-gate)."""

from marshal_ai.llm import Media
from marshal_ai.records import BuildSheet, Car, CarState, Verdict

SHEEN = {"zone": "front_left_door", "type": "paint_defect", "confidence": 0.9,
         "observation": "a pixelated pattern across the door"}


def _car(app, vin):
    app.rt.store.put(Car(vin=vin, build_sheet=BuildSheet(model="sedan", colour="blue"),
                         state=CarState.INSPECTION_GATE, location="inspection_gate"))


def _views(tag):
    return [Media(mime="image/jpeg", data=tag.encode(), label=f"{v} camera") for v in ("front", "rear", "left", "right")]


def _gemini(app, left_findings, real):
    calls = []

    def defects(call):
        left = call.media[0].label.startswith("left")
        return {"findings": left_findings if left else [], "clear_confidence": 0.95}

    def verify(call):
        calls.append(call)
        return {"checks": [{"index": 0, "real": real, "reason": "r"}]}

    def screen(call):                                # the decision agrees with the full answer
        left = call.media[0].label.startswith("left")
        seen = bool(left_findings) and left
        return {"choice": "defect visible" if seen else "no defect visible",
                "probs": {"defect visible": 0.95 if seen else 0.05, "no defect visible": 0.05 if seen else 0.95}}

    app.rt.llm.on("visual.defects", defects).on("visual.verify", verify).on("visual.screen", screen)
    app.rt.llm.on("visual.variant", lambda c: {"observed_colour": "blue", "mismatches": [], "confidence": 0.95})
    return calls


async def test_a_passing_car_becomes_the_reference_and_is_never_its_own(app):
    calls = _gemini(app, [], real=True)
    _car(app, "VIN-A")
    result = await app.visual.inspect("VIN-A", images=_views("a"))
    assert result.verdict == Verdict.PASS and app.visual.references["left"][0] == "VIN-A" and not calls


async def test_a_finding_the_passed_car_shows_too_is_dropped(app):
    _gemini(app, [], real=True)
    _car(app, "VIN-A")
    await app.visual.inspect("VIN-A", images=_views("a"))
    calls = _gemini(app, [SHEEN], real=False)
    _car(app, "VIN-B")
    result = await app.visual.inspect("VIN-B", images=_views("b"))
    assert result.verdict == Verdict.PASS and result.findings == []
    assert any("not confirmed" in o for o in result.observations)
    [call] = calls                                   # only the view with a finding is checked
    assert [m.data for m in call.media] == [b"a", b"b"] and call.media[0].label.startswith("reference")


async def test_a_finding_on_this_car_only_stands(app):
    _gemini(app, [], real=True)
    _car(app, "VIN-A")
    await app.visual.inspect("VIN-A", images=_views("a"))
    _gemini(app, [dict(SHEEN, type="scratch", confidence=0.95)], real=True)
    _car(app, "VIN-B")
    result = await app.visual.inspect("VIN-B", images=_views("b"))
    assert result.verdict == Verdict.FAIL and result.findings[0].type == "scratch"


async def test_without_a_reference_findings_stand(app):
    calls = _gemini(app, [SHEEN], real=False)
    _car(app, "VIN-B")
    result = await app.visual.inspect("VIN-B", images=_views("b"))
    assert result.verdict == Verdict.REVIEW and result.findings and not calls


async def test_a_car_a_supervisor_clears_becomes_the_reference(app):
    """The first car has no reference, so its false finding goes to a person; once cleared, the next
    car with the same sheen is compared with it and passes."""
    _gemini(app, [SHEEN], real=False)
    _car(app, "VIN-A")
    first = await app.visual.inspect("VIN-A", images=_views("a"))
    assert first.verdict == Verdict.REVIEW and not app.visual.references
    [card] = app.rt.pending("review")
    await app.visual.decide_review(card.proposal_id, confirm_defect=False, by="supervisor-ana")
    assert app.visual.references["left"][0] == "VIN-A"
    _car(app, "VIN-B")
    second = await app.visual.inspect("VIN-B", images=_views("b"))
    assert second.verdict == Verdict.PASS


async def test_a_car_a_supervisor_confirms_is_not_a_reference(app):
    _gemini(app, [SHEEN], real=False)
    _car(app, "VIN-A")
    await app.visual.inspect("VIN-A", images=_views("a"))
    [card] = app.rt.pending("review")
    await app.visual.decide_review(card.proposal_id, confirm_defect=True, by="supervisor-ana")
    assert not app.visual.references and "VIN-A" not in app.visual.awaiting_review
