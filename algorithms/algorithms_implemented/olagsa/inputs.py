from pydantic import Field

from ...base import AlgorithmInput


class OlagsaInput(AlgorithmInput):
    # --- Required runtime inputs ---
    iterations: int = Field(
        ...,
        title="Nombre d'itérations",
        description="Nombre d'itérations de l'algorithme génétique.",
        ge=1,
        json_schema_extra={"ui:section": "main"},
    )

    # --- Hyperparameters (with defaults) ---
    generations: int = Field(
        default=25,
        title="Nombre de générations",
        description="Nombre de générations par itération.",
        ge=1,
        json_schema_extra={"ui:section": "advanced"},
    )
    population_size: int = Field(
        default=50,
        title="Taille de la population",
        description="Nombre d'individus dans la population initiale.",
        ge=2,
        json_schema_extra={"ui:section": "advanced"},
    )
    crossover_rate: float = Field(
        default=0.1,
        title="Taux de crossover",
        description="Taux de croisement entre deux individus d'une même espèce (entre 0 et 1).",
        ge=0.0,
        le=1.0,
        json_schema_extra={"ui:section": "advanced"},
    )
    inter_species_crossover_rate: float = Field(
        default=0.1,
        title="Taux de crossover inter-espèce",
        description="Taux de croisement entre deux individus d'espèces différentes (entre 0 et 1).",
        ge=0.0,
        le=1.0,
        json_schema_extra={"ui:section": "advanced"},
    )
    mutation_rate: float = Field(
        default=0.1,
        title="Taux de mutation",
        description="Chance qu'un nouvel individu mute (entre 0 et 1).",
        ge=0.0,
        le=1.0,
        json_schema_extra={"ui:section": "advanced"},
    )
    expected_species_count: int = Field(
        default=1,
        title="Nombre d'espèces attendues",
        description=(
            "Nombre d'espèces attendues. Si iterations = 3, le nombre d'espèces "
            "sera de 4^x, où x est la valeur rentrée en paramètre."
        ),
        ge=1,
        json_schema_extra={"ui:section": "advanced"},
    )
    max_generations_without_improvement: int = Field(
        default=10,
        title="Nombre de générations maximum sans amélioration",
        description=(
            "Nombre de générations maximum sans amélioration avant d'arrêter le processus."
        ),
        ge=1,
        json_schema_extra={"ui:section": "advanced"},
    )
