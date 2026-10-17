from marshal_ai.batch.manifest import build_manifest
from marshal_api.services.batch_service import BatchService


def test_assistance_request_and_resolution_are_the_incident_ground_truth() -> None:
    manifest = build_manifest("BAT-INCIDENT", 3, seed=7)
    incident = next(car for car in manifest["cars"] if car["expected"]["kind"] == "incident")
    result = {
        "cars": {incident["vin"]: {"state": "shipped", "gate": "PASS", "mismatches": []}},
        "incident": {"vin": incident["vin"], "case_id": "AC-1", "resolved": True,
                     "test_requests": [{"text": "Box with a worker blocked the lane."}]},
    }

    labels = BatchService._label_outcomes(manifest, result)

    row = next(car for car in labels["cars"] if car["vin"] == incident["vin"])
    assert row["expected"] == "incident" and row["caught"] is True
    assert labels["missed"] == 2  # wrong colour and lamp outcomes are absent in this partial result
