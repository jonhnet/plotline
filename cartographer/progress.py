"""Real operation counts plus a monotone estimate of whole-pipeline progress."""
import threading
import time
from .storage import atomic_json


# Eight pipeline steps. Drawing gets the largest budget; these are estimates
# of work, not elapsed-time predictions. The browser owns the final 1%.
STAGES = {
    'queued': (0, 0, 1), 'starting': (0, 0, 1), 'planning': (0, 1, 1),
    'elevation': (1, 6, 1), 'tiles': (6, 18, 1),
    'geometry': (18, 28, 2), 'sketch': (28, 30, 2),
    'visibility': (30, 40, 3),
    'drawing': (40, 85, 4), 'labels': (40, 85, 4),
    'pdf': (85, 90, 5), 'preview': (90, 94, 6),
    'checks': (94, 97, 7), 'bundle': (97, 99, 7),
    'complete': (99, 99, 8), 'image': (99, 100, 8),
}


def estimate(stage, fraction=0):
    start, end, step = STAGES.get(stage, (0, 0, 1))
    return dict(overall_percent=round(start+(end-start)*max(0, min(1, fraction)), 2),
                overall_step=step, overall_steps=8)


def noop(*args, **kwargs):
    pass


class Progress:
    """Publish phases immediately and coalesce inner work to four updates/second.

    A trailing publisher exposes the latest operation even while the composing
    thread is inside a long native call. Use as a context manager to flush and
    stop it on both successful completion and errors.
    """
    def __init__(self, path, interval=.25):
        self.path = path
        self.lock = threading.RLock()
        self.current = None
        self.history = []
        self.last_write = 0
        self.fractions = {}
        self.percent = 0
        self.step = 1
        self.interval = interval
        self.pending = False
        self.closed = False
        self.error = None
        self.wake = threading.Condition(self.lock)
        self.publisher = threading.Thread(target=self._run, name='map-progress', daemon=True)
        self.publisher.start()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        try:
            self.close()
        except Exception:
            if exc_type is None:
                raise

    def _publish(self):
        atomic_json(self.path, self.current, durable=False)
        self.last_write = time.monotonic()
        self.pending = False

    def _run(self):
        with self.wake:
            while not self.closed:
                if not self.pending:
                    self.wake.wait()
                    continue
                delay = self.interval - (time.monotonic() - self.last_write)
                if delay > 0:
                    self.wake.wait(delay)
                    continue
                try:
                    self._publish()
                except Exception as error:
                    self.error = error
                    self.closed = True

    def close(self):
        with self.wake:
            self.closed = True
            try:
                if self.pending and self.error is None:
                    self._publish()
            except Exception as error:
                self.error = error
            self.wake.notify()
        self.publisher.join()
        if self.error is not None:
            raise self.error

    def advance(self, stage, fraction):
        """Set a parent fraction; nested operation counts cannot consume it."""
        with self.lock:
            step = STAGES[stage][2]
            self.fractions[step] = max(self.fractions.get(step, 0), min(1, max(0, fraction)))

    def __call__(self, stage, detail, completed=None, total=None, unit=None):
        with self.lock:
            if self.error is not None:
                raise self.error
            if self.closed:
                raise RuntimeError('Progress reporter is closed')
            now = time.time()
            changed = self.current is None or self.current['stage'] != stage
            if changed and self.current:
                self.history.append(dict(stage=self.current['stage'], seconds=round(now-self.current['started_at'], 2)))
            started = now if changed else self.current['started_at']
            # Drawing includes multiple regions and repeated feature operations.
            # Its parent fraction comes from the composer, never an inner hatch
            # or projection count that might finish long before the map does.
            fraction = self.fractions.get(STAGES.get(stage, (0, 0, 1))[2],
                completed/total if total and completed is not None and stage not in ('drawing', 'labels') else 0)
            overall = estimate(stage, fraction)
            self.percent = max(self.percent, overall['overall_percent'])
            self.step = max(self.step, overall['overall_step'])
            overall.update(overall_percent=self.percent, overall_step=self.step)
            self.current = dict(stage=stage, detail=detail, completed=completed, total=total,
                                unit=unit, started_at=started, updated_at=now, history=self.history[:], **overall)
            # Tiny operations and their completions share the same throttle.
            # Phase changes (including final completion) are always immediate.
            if changed or time.monotonic()-self.last_write >= self.interval:
                self._publish()
            elif not self.pending:
                self.pending = True
                self.wake.notify()
