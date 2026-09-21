import time
from threading import Event


class SearchTimer:
    def __init__(self, time_limit_ms, stop_event, check_interval=64):
        self.started_ns = time.perf_counter_ns()
        self.deadline_ns = self.started_ns + max(1, time_limit_ms) * 1_000_000
        self.stop_event = stop_event
        self.check_interval = max(1, check_interval)

    def should_stop(self, work_units, force=False):
        if self.stop_event.is_set():
            return True

        if force or work_units % self.check_interval == 0:
            if time.perf_counter_ns() >= self.deadline_ns:
                self.stop_event.set()
                return True

        return False

    @property
    def elapsed_ms(self):
        return int((time.perf_counter_ns() - self.started_ns) / 1_000_000)
