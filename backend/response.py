"""First-generation geospatial response planning, deliberately no removal physics."""

from datetime import timedelta
from typing import Literal
from pydantic import BaseModel, Field, ConfigDict, model_validator
from shapely.geometry import shape, Point
from .geo import distance
from .drift import timestamp

MODEL_VERSION = "geospatial-response-planner-1.0"


class ScenarioInput(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False, extra="forbid")
    run_id: str = Field(min_length=1)
    name: str = Field(min_length=1, max_length=100)
    kind: Literal["no_action", "boom", "dispatch", "interception", "delayed_response"]
    departure: tuple[float, float] | None = None
    target: tuple[float, float] | None = None
    target_horizon_h: Literal[6, 12, 24, 48] = 24
    speed_kn: float = Field(default=12, gt=0, le=60)
    speed_uncertainty_fraction: float = Field(default=0.2, ge=0, le=0.8)
    delay_h: float = Field(default=0, ge=0, le=96)
    setup_h: float = Field(default=1, ge=0, le=48)
    asset_name: str = Field(
        default="Operator-assumed response asset", min_length=1, max_length=100
    )
    asset_source: str = Field(
        default="Operator planning assumption; availability unverified",
        min_length=1,
        max_length=300,
    )

    @model_validator(mode="after")
    def coordinates(self):
        for point in (self.departure, self.target):
            if point is not None and not (
                -180 <= point[0] <= 180 and -90 <= point[1] <= 90
            ):
                raise ValueError("Coordinates must be [longitude, latitude] in WGS84")
        if self.kind != "no_action" and self.departure is None:
            raise ValueError("An operator-supplied departure location is required")
        return self


def evaluate(result, spec: ScenarioInput):
    if spec.run_id != result["run_id"]:
        raise ValueError("Scenario must reference the selected completed run")
    steps = result.get("forecast", {}).get("steps", [])
    if not steps:
        raise ValueError("Response planning requires a completed forecast")
    step = next((s for s in steps if s["hours"] == spec.target_horizon_h), None)
    if step is None:
        raise ValueError("Requested forecast horizon is unavailable")
    target = list(spec.target or step["center"])
    baseline = result["impact"]["receptors"]
    assumptions = [
        "All times are relative to the satellite observation, not a live dispatch clock.",
        "Route is a geodesic distance estimate; land, bathymetry, navigation restrictions and sea-state are not routed.",
        "Asset availability, constant speed, delay and setup duration are operator assumptions.",
        "Sampled forecast-envelope intersection is a planning opportunity, not guaranteed interception.",
        "Containment, removal efficiency, ecological damage reduction and pollutant mass are NOT modeled.",
    ]
    output = {
        "model_version": MODEL_VERSION,
        "input": spec.model_dump(mode="json"),
        "run_id": result["run_id"],
        "case_id": result["case_id"],
        "analysis_hash": result["analysis_hash"],
        "observation_time": result["observation_time"],
        "source_type": result["source_type"],
        "status": "PLANNING_ONLY",
        "target": target,
        "target_basis": (
            "Operator-selected point"
            if spec.target
            else "Selected forecast centroid; not an exact spill location"
        ),
        "candidate_interception_zone": step["geometry"],
        "assumptions": assumptions,
        "baseline": {
            "receptor_exposure": baseline,
            "label": "NO INTERVENTION — conditional forecast",
        },
        "intervention": {
            "receptor_exposure_after": None,
            "removal_efficiency": None,
            "avoided_exposure": None,
            "status": "NOT_MODELED",
        },
        "travel_km": None,
        "arrival_window_h": None,
        "arrival_window_utc": None,
        "ready_window_h": None,
        "opportunities": [],
        "route": None,
        "asset": None,
        "margin_to_target_h": None,
        "comparison": "Baseline exposure is retained. Post-intervention exposure cannot be calculated without a validated response model.",
    }
    if spec.kind == "no_action":
        output["status"] = "BASELINE"
        return output
    km = distance(spec.departure, target)
    fast = spec.speed_kn * (1 + spec.speed_uncertainty_fraction) * 1.852
    slow = spec.speed_kn * (1 - spec.speed_uncertainty_fraction) * 1.852
    arrival = [spec.delay_h + km / fast, spec.delay_h + km / slow]
    ready = [t + spec.setup_h for t in arrival]
    opportunities = []
    for snapshot in steps:
        intersects = shape(snapshot["geometry"]).covers(Point(*target))
        status = (
            (
                "ARRIVAL_BEFORE_SAMPLED_ENVELOPE"
                if ready[1] <= snapshot["hours"]
                else (
                    "ARRIVAL_UNCERTAIN"
                    if ready[0] <= snapshot["hours"]
                    else "ARRIVAL_AFTER_SAMPLED_ENVELOPE"
                )
            )
            if intersects
            else "TARGET_OUTSIDE_SAMPLED_ENVELOPE"
        )
        opportunities.append(
            {
                "hours": snapshot["hours"],
                "target_inside_envelope": intersects,
                "status": status,
                "conservative_lead_h": (
                    round(snapshot["hours"] - ready[1], 2) if intersects else None
                ),
            }
        )
    output.update(
        {
            "travel_km": round(km, 3),
            "arrival_window_h": [round(t, 3) for t in arrival],
            "arrival_window_utc": [
                (timestamp(result["observation_time"]) + timedelta(hours=t)).isoformat()
                for t in arrival
            ],
            "ready_window_h": [round(t, 3) for t in ready],
            "opportunities": opportunities,
            "margin_to_target_h": round(spec.target_horizon_h - ready[1], 3),
            "route": {
                "type": "LineString",
                "coordinates": [list(spec.departure), target],
            },
            "asset": {
                "name": spec.asset_name,
                "coordinates": list(spec.departure),
                "source": spec.asset_source,
                "evidence_kind": "ASSUMPTION",
                "availability": "UNVERIFIED",
            },
        }
    )
    return output
