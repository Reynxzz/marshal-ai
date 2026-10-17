from marshal_api.services.batch_service import BatchService, default_flow_counts


def test_lab_events_update_scenario_and_adversarial_counts() -> None:
    counts = default_flow_counts()
    batch: dict = {"lab": {}}

    BatchService._handle_lab_event(counts, batch, {
        "type": "lab.generated", "scenario_id": "SC-1", "status": "dry_run_ok",
    })
    BatchService._handle_lab_event(counts, batch, {
        "type": "lab.adversarial.done", "runs": 4, "robust": False,
    })

    assert batch["lab"]["scenario_id"] == "SC-1"
    assert counts["flow6"]["generated"] == 1
    assert counts["flow7"] == {"runs": 4, "failures": 1, "status": "done"}
