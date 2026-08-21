"""Thread-local progress reporting for Stocks background jobs (see
utils/background_jobs.py). A job's own worker thread calls report(current,
total) periodically from inside whatever loop it's running (syncing
symbols, matching instruments, etc.); background_jobs.py binds a DB-backed
reporter to that thread right before the job body runs, so nothing
downstream needs to know a background job is even involved -- outside of
one (a cron run, a direct function call in a test), report() is simply a
no-op.

Uses threading.local rather than a plain module-level variable because two
different background jobs (different job_name) can run concurrently, each
on its own thread -- see start_background_job's per-job_name dedup, which
only prevents the SAME job_name from overlapping itself."""
import threading
import time

_local = threading.local()

# Minimum real time between DB writes for the same job, regardless of how
# fast the caller's loop iterates -- reporting on every single symbol would
# add back the kind of per-item DB round trip this session already had to
# fix once (see stock_shortlist.py's previous-snapshot batching). The final
# report (current >= total) always writes through immediately.
MIN_REPORT_INTERVAL_SECONDS = 2

# Sentinel distinguishing "no label passed" (keep whatever label is
# currently sticky, e.g. Super Sync's "step N of 9: ...") from an explicit
# label=None (which would otherwise be indistinguishable from "unset" and
# clear a step label a nested call didn't mean to touch).
_LABEL_UNSET = object()


def bind(report_fn):
    """Call once, from the job's own thread, before running the job body."""
    _local.report_fn = report_fn
    _local.last_reported_at = 0.0
    _local.last_label = None


def clear():
    _local.report_fn = None


def report(current, total, label=_LABEL_UNSET):
    """current/total: e.g. (340, 1067). label optionally describes what's
    being counted right now (e.g. a Super Sync step name) -- omitting it
    keeps whichever label a caller further up the call stack last set,
    rather than blanking it out. Silently does nothing if no job is bound
    to this thread, so callers never need to check "am I running as a
    background job" themselves.

    A call that passes an explicit label always writes through immediately,
    bypassing the throttle below -- it marks a phase change (e.g. Super
    Sync moving to its next step), not just another tick of the same loop,
    and those are both rare enough and important enough to never get
    dropped."""
    fn = getattr(_local, 'report_fn', None)
    if not fn or not total:
        return

    has_explicit_label = label is not _LABEL_UNSET
    if has_explicit_label:
        _local.last_label = label
    else:
        label = getattr(_local, 'last_label', None)

    now = time.monotonic()
    last = getattr(_local, 'last_reported_at', 0.0)
    is_final = current >= total
    if not is_final and not has_explicit_label and (now - last) < MIN_REPORT_INTERVAL_SECONDS:
        return

    _local.last_reported_at = now
    fn(current, total, label)
