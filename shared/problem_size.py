"""How large an input a generation may take: participants x timesteps.

One limit, checked in three places so that a run which cannot fit is refused
before it is queued whenever possible:

* the CRM pre-flight (``shared.crm_preflight.evaluate``), which feeds the
  preview, ``POST /from-crm`` and the worker's re-read of the period;
* ``POST /`` (file upload), from a cheap count of the file's rows and columns
  (``api.generation.upload_shape``);
* the worker, on the exact parsed matrix, as the backstop for an upload whose
  shape could not be counted up front.

Framework-free and numpy-free: the API imports it.
"""

# Sized for OLAGSA, the default algorithm. The peak memory of its solver process
# is linear in participants x timesteps, about 145 MB + 2 KB per value (measured
# 2026-10-08 with cvxpy 1.6.3 in the worker image, one CPU): 0.5 GB for 20
# meters over 92 days of quarter-hours (176,640 values), 1.5 GB for 40 meters
# over 184 days (706,560 values). At this limit one solver process peaks near
# 0.95 GB, inside the 2 GB the compose files give a worker running one solver
# process. The README's "Resource sizing" section has the table; raise this only
# together with the worker's mem_limit.
MAX_PROBLEM_CELLS = 400_000


def too_large(participants: int, timesteps: int) -> str | None:
    """Return the detail to report when the input exceeds the limit, else ``None``."""
    cells = participants * timesteps
    if cells <= MAX_PROBLEM_CELLS:
        return None
    return (
        f"{participants} participants x {timesteps} timesteps = {cells} values exceed "
        f"the {MAX_PROBLEM_CELLS} limit; shorten the period or reduce the participants"
    )
