"""Lightweight context object passed through service calls within a run."""
from dataclasses import dataclass, field
import threading


@dataclass
class RunContext:
    run_id: int
    task_id: str = ""
    _stop_event: threading.Event = field(default_factory=threading.Event, repr=False)
    _llm_calls: int = field(default=0, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def request_stop(self) -> None:
        self._stop_event.set()

    @property
    def should_stop(self) -> bool:
        return self._stop_event.is_set()

    def increment_llm_calls(self) -> int:
        with self._lock:
            self._llm_calls += 1
            return self._llm_calls

    @property
    def llm_calls(self) -> int:
        return self._llm_calls
