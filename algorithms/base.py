# algorithms/base.py
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict, Field, field_serializer


class AlgorithmInput(BaseModel):
    """Base class for all algorithm input schemas."""

    model_config = ConfigDict(extra="forbid")


# ---- Raw data (loaded once by the worker before invoking the algorithm) ----
@dataclass
class AlgorithmRawData:
    """Pre-parsed input data shared by all allocation-key algorithms.

    Produced by the worker (from ``GenerationModel.file_storage_key`` +
    ``GenerationModel.injection_name``) and passed to ``Algorithm.run``.
    The algorithm is a pure function of ``(inputs, raw_data)``.

    ``C`` / ``VA`` are 2D numpy arrays. Typed as ``Any`` here to keep
    ``algorithms.base`` free of heavy dependencies.
    """

    C: Any  # consumption matrix, shape (num_consumers, T)
    VA: Any  # production matrix, shape (num_consumers, T)
    consumer_names: list[str]


# ---- Result schemas (pure — no DB concerns) --------------------------------
class ConsumerResult(BaseModel):
    name: str
    energy_allocated_percentage: float


class IterationResult(BaseModel):
    number: int
    energy_allocated_percentage: float
    surplus_total: float
    consumers: list[ConsumerResult]


class AllocationKeyResult(BaseModel):
    name: str
    description: str
    iterations: list[IterationResult]


class AlgorithmResult(BaseModel):
    """Pure result returned by ``Algorithm.run``.

    The worker is responsible for persisting this into the Local DB
    (``AllocationKeyGeneratedModel`` / ``IterationGeneratedModel`` /
    ``ConsumerGeneratedModel``) after the algorithm completes.
    """

    keys: list[AllocationKeyResult] = Field(default_factory=list)


# ---- Metadata --------------------------------------------------------------
class AlgorithmMetadata(BaseModel):
    """Lightweight, serializable description of an algorithm.

    Registered by both the API service (for schema exposure and dispatch)
    and the worker service (for routing to the correct implementation).
    Contains no executable logic or heavy dependencies.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    name: str = Field(
        ...,
        description="Unique identifier, used in URLs and NATS subjects. "
        "Must be lowercase, alphanumeric with underscores.",
        pattern=r"^[a-z][a-z0-9_]*$",
    )
    description: str = Field(
        ...,
        description=(
            "i18n key (dot-notation, e.g. 'ALGORITHMS.OLAGSA.DESCRIPTION') "
            "resolved to a localized string at the API response boundary."
        ),
    )
    version: str = Field(
        default="1.0",
        description="Semantic version of the algorithm's input/output contract.",
    )
    queue: str = Field(
        ...,
        description="NATS subject the worker subscribes to for this algorithm.",
    )
    input_schema: type[AlgorithmInput] = Field(
        ...,
        description="Pydantic model class describing required and optional inputs.",
    )
    tags: list[str] = Field(default_factory=list)
    timeout_seconds: int | None = Field(default=None)

    @field_serializer("input_schema")
    def _serialize_input_schema(self, input_schema: type[AlgorithmInput]) -> dict:
        """Expose the input schema as its JSON schema dict.

        The raw ``Type[AlgorithmInput]`` value is a Pydantic model class
        (a ``ModelMetaclass``), which Pydantic cannot serialize on its own.
        The API contract wants a plain JSON Schema so the frontend can
        render a dynamic form — so we emit that here.
        """
        return input_schema.model_json_schema()


# ---- Algorithm base --------------------------------------------------------
class Algorithm[InputT: AlgorithmInput](ABC):
    """Abstract base for all algorithm implementations.

    Only imported by the worker service — subclass modules may pull in
    heavy dependencies like cvxpy, numpy, etc.

    Subclasses parameterize ``InputT`` with their concrete input schema so
    ``run()`` can declare the specific type without violating LSP.
    """

    metadata: ClassVar[AlgorithmMetadata]

    @abstractmethod
    async def run(
        self,
        inputs: InputT,
        raw_data: AlgorithmRawData,
    ) -> AlgorithmResult:
        """Execute the algorithm against validated inputs and pre-loaded data.

        Must be a pure function: no DB access, no network I/O. The worker
        owns data loading and result persistence.
        """
        ...
