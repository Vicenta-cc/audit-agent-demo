"""Wait only for proven, pre-collection route capacity shortages."""

from contextlib import ExitStack
import math
import time

from .crawler_route import RouteCapacityBusy


def acquire_with_capacity_wait(acquire, *, check, on_wait, max_wait,
                               clock=None, sleep=None):
    """Return an owned bundle; never retry caller body or bundle cleanup.

    Each unsuccessful acquisition has already released its temporary handles.
    The caller's execution/account leases and heartbeat remain its responsibility.
    """
    if not math.isfinite(max_wait) or max_wait < 0:
        raise ValueError('finite nonnegative route capacity wait required')
    clock = clock or time.monotonic
    sleep = sleep or time.sleep
    deadline = clock() + max_wait
    waiting = None
    try:
        while True:
            check()
            if waiting is not None and clock() >= deadline:
                waiting.wait_timed_out = True
                raise waiting
            try:
                bundle = acquire()
            except RouteCapacityBusy as exc:
                if waiting is None or waiting.reason != exc.reason:
                    on_wait(exc.reason)
                waiting = exc
                check()
                remaining = deadline - clock()
                if remaining <= 0:
                    exc.wait_timed_out = True
                    raise
                sleep(min(.25, remaining))
                continue
            with ExitStack() as cleanup:
                cleanup.callback(bundle.close)
                # Cancellation/revocation can arrive while acquisition is busy.
                check()
                if waiting is not None:
                    if clock() >= deadline:
                        waiting.wait_timed_out = True
                        raise waiting
                    on_wait('')
                    waiting = None
                cleanup.pop_all()
                return bundle
    except BaseException:
        if waiting is not None:
            on_wait('')
        raise
