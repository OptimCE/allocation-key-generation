from typing import Any

from pydantic import BaseModel, Field

from shared.const import GenerationStatus


class Generation(BaseModel):
    id: int = Field(..., description="Unique ID for generation.")
    name: str = Field(..., description="Name of the generation.")
    status: GenerationStatus = Field(..., description="Status of the generation.")


class ConsumerGenerated(BaseModel):
    id: int = Field(..., description="Unique ID for consumer.")
    name: str = Field(..., description="Name of the consumer.")
    energy_allocated_percentage: float = Field(
        ..., description="Energy allocated percentage of the consumer."
    )


class IterationGenerated(BaseModel):
    id: int = Field(..., description="Unique ID for iteration.")
    number: int = Field(..., description="Number of the iterations")
    energy_allocated_percentage: float = Field(
        ..., description="Energy allocated percentage of the iteration."
    )
    consumers: list[ConsumerGenerated] = Field(
        ..., description="Consumers of the iteration."
    )
    surplus_total: float = Field(..., description="Total surplus of the iteration.")


class PartialAllocationKeyGenerated(BaseModel):
    id: int = Field(..., description="Unique ID for generation.")
    name: str = Field(..., description="Name of the generation.")
    description: str = Field(..., description="Description of the generation.")
    surplus_total: float = Field(..., description="Total surplus of the generation.")


class AllocationKeyGenerated(PartialAllocationKeyGenerated):
    iterations: list[IterationGenerated] = Field(
        ..., description="Iterations of the generation."
    )


class SaveKey(BaseModel):
    id_key: int = Field(..., description="Unique ID for save.")


class GenerateRequest(BaseModel):
    """Body of POST /generation/.

    Triggers a new generation. The ``inputs`` field is validated dynamically
    against the algorithm's own input schema (``registry.metadata(algorithm_name).input_schema``)
    in the service layer — we cannot statically express it as a discriminated
    union because algorithms are pluggable.

    ``id_community`` and ``algorithm_version`` are intentionally absent:
    - community comes from the auth context (``require_community``);
    - version is snapshotted from the metadata at creation time.
    """

    name: str = Field(..., description="User-facing label for the generation.")
    file_url: str = Field(
        ..., description="URL of the externally hosted source data file."
    )
    file_name: str = Field(
        ..., description="Original file name (used to pick CSV vs XLSX parser)."
    )
    injection_name: str = Field(
        ...,
        description="Name of the injection (production) column inside the source file.",
    )
    algorithm_name: str = Field(
        ..., description="Algorithm registry key, e.g. 'olagsa'."
    )
    inputs: dict[str, Any] = Field(
        ...,
        description="Algorithm-specific input parameters; validated against the algorithm's input schema.",
    )


class GenerateResponse(BaseModel):
    """Returned by POST /generation/ once the generation row is created and queued."""

    id: int = Field(..., description="ID of the freshly created generation row.")
    status: GenerationStatus = Field(
        ..., description="Initial status (always PENDING on success)."
    )
