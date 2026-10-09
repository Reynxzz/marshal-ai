"""Evaluation harness: score each flow's Gemini judgements against known answers, many times over.

Every result so far was one run read by eye. This runs each case of a dataset N times through the
flow's own code (fresh app, in-memory store, no CARLA) and reports the design's KPIs per flow, so a
prompt or model change can be measured instead of judged from a single run.

    python -m marshal_ai.eval eval/datasets/v1.json --runs 5
    python -m marshal_ai.eval eval/datasets/v1.json --flows gate,track --runs 3 --model gemini-2.5-pro
"""

from marshal_ai.eval.harness import evaluate

__all__ = ["evaluate"]
