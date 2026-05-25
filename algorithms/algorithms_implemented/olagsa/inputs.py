from pydantic import Field

from ...base import AlgorithmInput


class OlagsaInput(AlgorithmInput):
    # --- Required runtime inputs ---
    iterations: int = Field(
        ...,
        title="PARAMETERS.OLAGSA.ITERATIONS.TITLE",
        description="PARAMETERS.OLAGSA.ITERATIONS.DESCRIPTION",
        ge=1,
        json_schema_extra={"ui:section": "main"},
    )

    # --- Hyperparameters (with defaults) ---
    generations: int = Field(
        default=25,
        title="PARAMETERS.OLAGSA.GENERATIONS.TITLE",
        description="PARAMETERS.OLAGSA.GENERATIONS.DESCRIPTION",
        ge=1,
        json_schema_extra={"ui:section": "advanced"},
    )
    population_size: int = Field(
        default=50,
        title="PARAMETERS.OLAGSA.POPULATION_SIZE.TITLE",
        description="PARAMETERS.OLAGSA.POPULATION_SIZE.DESCRIPTION",
        ge=2,
        json_schema_extra={"ui:section": "advanced"},
    )
    crossover_rate: float = Field(
        default=0.1,
        title="PARAMETERS.OLAGSA.CROSSOVER_RATE.TITLE",
        description="PARAMETERS.OLAGSA.CROSSOVER_RATE.DESCRIPTION",
        ge=0.0,
        le=1.0,
        json_schema_extra={"ui:section": "advanced"},
    )
    inter_species_crossover_rate: float = Field(
        default=0.1,
        title="PARAMETERS.OLAGSA.INTER_SPECIES_CROSSOVER_RATE.TITLE",
        description="PARAMETERS.OLAGSA.INTER_SPECIES_CROSSOVER_RATE.DESCRIPTION",
        ge=0.0,
        le=1.0,
        json_schema_extra={"ui:section": "advanced"},
    )
    mutation_rate: float = Field(
        default=0.1,
        title="PARAMETERS.OLAGSA.MUTATION_RATE.TITLE",
        description="PARAMETERS.OLAGSA.MUTATION_RATE.DESCRIPTION",
        ge=0.0,
        le=1.0,
        json_schema_extra={"ui:section": "advanced"},
    )
    expected_species_count: int = Field(
        default=1,
        title="PARAMETERS.OLAGSA.EXPECTED_SPECIES_COUNT.TITLE",
        description="PARAMETERS.OLAGSA.EXPECTED_SPECIES_COUNT.DESCRIPTION",
        ge=1,
        json_schema_extra={"ui:section": "advanced"},
    )
    max_generations_without_improvement: int = Field(
        default=10,
        title="PARAMETERS.OLAGSA.MAX_GENERATIONS_WITHOUT_IMPROVEMENT.TITLE",
        description="PARAMETERS.OLAGSA.MAX_GENERATIONS_WITHOUT_IMPROVEMENT.DESCRIPTION",
        ge=1,
        json_schema_extra={"ui:section": "advanced"},
    )
