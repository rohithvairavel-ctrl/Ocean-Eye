"""Classifier contract. No trained model is installed or implied by this interface."""

from typing import Literal, Protocol, Any
from pydantic import BaseModel, Field, model_validator

CANDIDATE_LABEL = "OIL CANDIDATE — CLASSIFICATION PENDING"


class ClassificationResult(BaseModel):
    status: Literal["UNAVAILABLE", "SCREENING", "VALIDATED"] = "UNAVAILABLE"
    model: str | None = None
    version: str | None = None
    predicted_class: str = "UNKNOWN"
    calibrated_confidence: float | None = Field(default=None, ge=0, le=1)
    probabilities: dict[str, float] | None = None
    calibration_reference: str | None = None
    preprocessing: list[str] = Field(default_factory=list)
    dataset: dict[str, Any] | None = None
    metrics: dict[str, Any] | None = None
    provenance: list[dict[str, Any]] = Field(default_factory=list)
    explanation: str = (
        "No validated oil/look-alike classifier is installed. Dark-region segmentation is a screening fallback."
    )

    @model_validator(mode="after")
    def validate_claims(self):
        if self.status == "UNAVAILABLE" and (
            self.predicted_class != "UNKNOWN"
            or self.metrics
            or self.probabilities
            or self.calibrated_confidence is not None
        ):
            raise ValueError(
                "Unavailable classifiers cannot supply predictions, metrics or confidence"
            )
        if self.status == "VALIDATED" and not all(
            [self.model, self.version, self.dataset, self.provenance]
        ):
            raise ValueError(
                "Validated status requires model, version, dataset and provenance"
            )
        if self.calibrated_confidence is not None or self.probabilities is not None:
            if self.status != "VALIDATED" or not all(
                [
                    self.model,
                    self.version,
                    self.calibration_reference,
                    self.dataset,
                    self.provenance,
                ]
            ):
                raise ValueError(
                    "Probability claims require a validated model, version, dataset, calibration reference and provenance"
                )
        if self.probabilities is not None:
            import math

            values = list(self.probabilities.values())
            if (
                not values
                or any(not math.isfinite(v) or not 0 <= v <= 1 for v in values)
                or abs(sum(values) - 1) > 1e-6
            ):
                raise ValueError("Class probabilities must be finite and sum to one")
        return self


class ClassifierAdapter(Protocol):
    def predict(self, scene: Any, metadata: dict) -> ClassificationResult: ...


def pending_classification():
    return ClassificationResult(
        preprocessing=[
            "Calibrated Sigma0 input",
            "Median filter",
            "Adaptive dark-region screening",
        ]
    ).model_dump()


INCIDENT_TYPES = [
    {
        "id": "oil",
        "label": "Oil candidate",
        "supported_workflow": True,
        "required_evidence": [
            "SAR candidate geometry",
            "Independent oil/look-alike confirmation",
        ],
        "sensor_limit": "SAR damping alone does not confirm oil.",
    },
    {
        "id": "chemical",
        "label": "Chemical discharge",
        "supported_workflow": False,
        "required_evidence": [
            "Chemical-specific sampling or validated spectroscopy",
            "Substance-specific transport model",
        ],
        "sensor_limit": "Cannot identify chemical composition from this SAR input.",
    },
    {
        "id": "debris",
        "label": "Floating waste / debris",
        "supported_workflow": False,
        "required_evidence": [
            "Optical or field confirmation",
            "Validated debris detector and transport parameters",
        ],
        "sensor_limit": "Current SAR screening cannot identify floating waste.",
    },
    {
        "id": "unknown",
        "label": "Unknown marine pollution",
        "supported_workflow": False,
        "required_evidence": ["Independent characterization of the observed anomaly"],
        "sensor_limit": "No pollutant classification established.",
    },
]
