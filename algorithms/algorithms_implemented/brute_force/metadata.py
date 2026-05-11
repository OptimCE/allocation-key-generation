# algorithms_implemented/brute_force/metadata.py
from ...base import AlgorithmMetadata
from .inputs import BruteForceInput

BRUTE_FORCE_METADATA = AlgorithmMetadata(
    name="brute_force",
    description="Énumération exhaustive de clés d'allocation par combinaison d'algorithmes de répartition",
    version="1.0",
    queue="optimce.allocation.brute_force",
    input_schema=BruteForceInput,
    tags=["enumeration", "exhaustive"],
)
