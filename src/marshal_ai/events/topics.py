from enum import Enum


class Topic(str, Enum):
    """Pub/Sub topics. Publisher -> subscribers, as in the design doc."""

    VEHICLE_STATUS = "vehicle-status"          # driving system -> Yard Traffic, dashboard
    ASSIST_REQUESTS = "assist-requests"        # driving system -> Remote Assistance Desk
    GATE_IMAGES = "gate-images"                # gate cameras -> Visual Inspector
    TRACK_STEPS = "track-steps"                # driving system + track cameras -> Functional Checker
    INSPECTION_RESULTS = "inspection-results"  # inspectors -> Report and Rework Router
    TEST_REQUESTS = "test-requests"            # Assistance Desk, supervisor, engineers -> Scenario Generator
    TEST_RESULTS = "test-results"              # CARLA runners -> Adversarial Tester, dashboard
