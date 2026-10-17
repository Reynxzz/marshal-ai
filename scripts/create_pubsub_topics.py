"""Create the seven Pub/Sub topics and one subscription per subscriber. Safe to re-run.

    python scripts/create_pubsub_topics.py --project my-project
"""

from __future__ import annotations

import argparse

from google.api_core.exceptions import AlreadyExists
from google.cloud import pubsub_v1

from marshal_ai.events import Topic

# "api" is the Control Tower SSE stream (marshal_api.bridge uses subscriber_name="api").
SUBSCRIBERS = {
    Topic.VEHICLE_STATUS: ["yard-traffic", "dashboard", "api"],
    Topic.ASSIST_REQUESTS: ["remote-assistance", "api"],
    Topic.GATE_IMAGES: ["visual-inspector", "api"],
    Topic.TRACK_STEPS: ["functional-checker", "api"],
    Topic.INSPECTION_RESULTS: ["report-router", "api"],
    Topic.TEST_REQUESTS: ["scenario-generator", "api"],
    Topic.TEST_RESULTS: ["adversarial-tester", "dashboard", "api"],
}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", required=True)
    args = parser.parse_args()

    publisher = pubsub_v1.PublisherClient()
    subscriber = pubsub_v1.SubscriberClient()
    for topic, subs in SUBSCRIBERS.items():
        topic_path = publisher.topic_path(args.project, topic.value)
        try:
            publisher.create_topic(name=topic_path)
            print(f"created topic {topic.value}")
        except AlreadyExists:
            print(f"topic {topic.value} exists")
        for sub in subs:
            sub_path = subscriber.subscription_path(args.project, f"{topic.value}--{sub}")
            try:
                subscriber.create_subscription(name=sub_path, topic=topic_path)
                print(f"  created subscription {topic.value}--{sub}")
            except AlreadyExists:
                print(f"  subscription {topic.value}--{sub} exists")


if __name__ == "__main__":
    main()
