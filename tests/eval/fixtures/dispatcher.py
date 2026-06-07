"""Dispatcher module — event-emitter + ordinary call patterns.

Used by the recall regression harness to test:
  - seam_query/search can LOCATE these symbols
  - seam_context / seam_impact can find callers via synthesized event-emitter edges
  - seam_trace can find paths via synthesized edges
"""


class EventBus:
    """Central event bus using observer/subscriber pattern."""

    def __init__(self) -> None:
        self._handlers: dict[str, list] = {}

    def subscribe(self, event: str, handler: object) -> None:
        """Register a handler for an event."""
        if event not in self._handlers:
            self._handlers[event] = []
        self._handlers[event].append(handler)

    def emit(self, event: str) -> None:
        """Fire all registered handlers for an event."""
        for h in self._handlers.get(event, []):
            h()

    def process_data(self, data: dict) -> None:
        """Process incoming data — calls validate_data then emit."""
        validate_data(data)
        self.emit("data_received")


def validate_data(data: dict) -> bool:
    """Validate that data has required keys."""
    return bool(data)


def on_data_received_handler() -> None:
    """Handler invoked when the data_received event fires."""
    pass


def on_error_handler() -> None:
    """Handler invoked when an error event fires."""
    pass


# Registration sites — the recall harness checks these are linked to the dispatcher
bus = EventBus()
bus.subscribe("data_received", on_data_received_handler)
bus.subscribe("error", on_error_handler)
