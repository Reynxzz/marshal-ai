from __future__ import annotations

from marshal_ai.batch.feedback import (
    BatchFeedback,
    build_digest,
    build_prompt,
    normalize_feedback,
    offline_feedback,
)
from marshal_ai.batch.report import build_report
from marshal_ai.llm import FakeLLM


def test_feedback_digest_is_capped_and_offline_feedback_covers_seven_flows() -> None:
    events = [{"seq": i, "flow": i % 7 + 1, "source": "plant", "title": f"event {i}",
               "detail": "", "level": "bad" if i == 0 else "info"} for i in range(300)]
    digest = build_digest({"batch_id": "BAT-1", "requested_cars": 3, "created_cars": 3,
                           "finished_cars": 3, "counts": {}, "flow_counts": {}},
                          [], events, {}, {})
    assert len(digest["logs"]) <= 200
    prompt = build_prompt(digest)
    assert "BAT-1" in prompt and "Logs (oldest first" in prompt
    feedback = normalize_feedback(offline_feedback(digest), 3)
    assert feedback.batch_size == 3 and [item.flow for item in feedback.per_flow] == list(range(1, 8))


def test_fake_llm_and_fallback_report_are_valid() -> None:
    llm = FakeLLM().on("batch.feedback", lambda call: {
        "batch_size": 99, "summary": "batch facts", "per_flow": [], "risk": "unknown"
    })
    response = llm.generate("batch.feedback", "Batch: BAT-1", BatchFeedback)
    normalized = normalize_feedback(response, 2)
    assert normalized.batch_size == 2 and normalized.risk == "medium"
    assert len(normalized.per_flow) == 7

    report = build_report({"batch_id": "BAT-1", "requested_cars": 2, "created_cars": 2,
                           "finished_cars": 2, "counts": {}, "flow_counts": {}},
                          [], [], {}, {"status": "not_triggered"}, normalized.model_dump())
    assert report["batch_size"] == 2
    assert len(report["gemini"]["per_flow"]) == 7
