"""Pipeline module — closure-collection + ordinary call patterns.

Used by the recall regression harness to test:
  - closure-collection synthesis (A1a channel): stage callbacks appended + invoked
  - ordinary direct call edges
  - seam_trace between pipeline symbols
"""
from collections.abc import Callable


class DataPipeline:
    """Executes a sequence of processing stages (closure-collection pattern)."""

    def __init__(self) -> None:
        # Collection of callable stages — the CC synthesis channel looks for
        # iteration+invocation of this field.
        self.stages: list[Callable] = []

    def run(self, data: dict) -> dict:
        """Run all registered stages on the data in order."""
        for stage in self.stages:
            data = stage(data)
        return data

    def add_stage(self, stage: Callable) -> None:
        """Register a new processing stage."""
        self.stages.append(stage)


def normalize_stage(data: dict) -> dict:
    """Normalize data keys to lowercase."""
    return {k.lower(): v for k, v in data.items()}


def enrich_stage(data: dict) -> dict:
    """Enrich data with a timestamp."""
    data["enriched"] = True
    return data


def build_pipeline() -> DataPipeline:
    """Factory that assembles a DataPipeline with standard stages."""
    pipeline = DataPipeline()
    pipeline.stages.append(normalize_stage)
    pipeline.stages.append(enrich_stage)
    return pipeline


def run_pipeline(data: dict) -> dict:
    """Convenience function that builds + runs the pipeline."""
    pipeline = build_pipeline()
    return pipeline.run(data)
