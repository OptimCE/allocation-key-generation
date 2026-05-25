from pydantic import Field

from ...base import AlgorithmInput


class BruteForceInput(AlgorithmInput):
    iterations: int = Field(
        ...,
        title="PARAMETERS.BRUTE_FORCE.ITERATIONS.TITLE",
        description="PARAMETERS.BRUTE_FORCE.ITERATIONS.DESCRIPTION",
        ge=1,
        le=3,
        json_schema_extra={"ui:section": "main"},
    )
