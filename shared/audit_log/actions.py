"""Audit log action codes.

Action codes follow the ``domain.entity.verb`` convention used by
``crm-backend`` (e.g. ``crm.allocation_key.created``). They are stored as
``VARCHAR(128)`` and the ``AuditAction`` type stays open-ended so call sites
can introduce new codes without round-tripping this module.
"""

from typing import Final

AuditAction = str


class AuditActions:
    """Known action codes emitted by ``allocation-key-generation``."""

    GENERATION_CREATED: Final[AuditAction] = "allocation_key_generation.generation.created"
    GENERATION_QUEUE_FAILED: Final[AuditAction] = (
        "allocation_key_generation.generation.queue_failed"
    )
    GENERATION_SUCCEEDED: Final[AuditAction] = "allocation_key_generation.generation.succeeded"
    GENERATION_FAILED: Final[AuditAction] = "allocation_key_generation.generation.failed"
    GENERATION_DELETED: Final[AuditAction] = "allocation_key_generation.generation.deleted"
    ALLOCATION_KEY_SAVED: Final[AuditAction] = "allocation_key_generation.allocation_key.saved"
    ALLOCATION_KEY_GENERATED_DELETED: Final[AuditAction] = (
        "allocation_key_generation.allocation_key_generated.deleted"
    )
