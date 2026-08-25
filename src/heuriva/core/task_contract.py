from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, field_validator

from heuriva.core.common import non_empty


class SearchPolicy(StrEnum):
    AUTO = "auto"
    REQUIRED = "required"
    FORBIDDEN = "forbidden"


class EvidenceRequirement(StrEnum):
    OPTIONAL = "optional"
    REQUIRED = "required"


class SourceScope(StrEnum):
    WEB = "web"
    LOCAL = "local"
    PROVIDED = "provided"


class TaskContract(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    criteria: tuple[str, ...] = ()
    search_policy: SearchPolicy = SearchPolicy.AUTO
    evidence_requirement: EvidenceRequirement = EvidenceRequirement.OPTIONAL
    origin: str = "default"

    @field_validator("criteria")
    @classmethod
    def _clean_criteria(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        cleaned: list[str] = []
        for value in values:
            stripped = value.strip()
            if stripped and stripped not in cleaned:
                cleaned.append(stripped)
        return tuple(cleaned)

    @field_validator("origin")
    @classmethod
    def _non_empty_origin(cls, value: str) -> str:
        return non_empty(value, "task contract origin")

    @classmethod
    def from_user(
        cls,
        *,
        criteria: tuple[str, ...] = (),
        search_policy: SearchPolicy | str = SearchPolicy.AUTO,
        evidence_requirement: EvidenceRequirement | str = EvidenceRequirement.OPTIONAL,
    ) -> TaskContract:
        policy = SearchPolicy(search_policy)
        requirement = EvidenceRequirement(evidence_requirement)
        origin = (
            "user"
            if criteria
            or policy is not SearchPolicy.AUTO
            or requirement is EvidenceRequirement.REQUIRED
            else "default"
        )
        return cls(
            criteria=criteria,
            search_policy=policy,
            evidence_requirement=requirement,
            origin=origin,
        )
