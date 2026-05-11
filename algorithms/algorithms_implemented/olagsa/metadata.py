# algorithms_implemented/olagsa/metadata.py
from ...base import AlgorithmMetadata
from .inputs import OlagsaInput

OLAGSA_METADATA = AlgorithmMetadata(
    name="olagsa",
    description="Optimal Linear Allocation with Guaranteed Stable Shares",
    version="1.0",
    queue="optimce.allocation.olagsa",
    input_schema=OlagsaInput,
    tags=["genetic", "optimization"],
)
