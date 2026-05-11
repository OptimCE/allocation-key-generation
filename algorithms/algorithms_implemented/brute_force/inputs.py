from pydantic import Field

from ...base import AlgorithmInput


class BruteForceInput(AlgorithmInput):
    iterations: int = Field(
        ...,
        title="Nombre d'itérations",
        description=(
            "Nombre d'itérations imbriquées de l'énumération brute-force. "
            "Doit être 1, 2 ou 3."
        ),
        ge=1,
        le=3,
        json_schema_extra={"ui:section": "main"},
    )
