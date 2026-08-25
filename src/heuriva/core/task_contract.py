from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

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


class CriterionKind(StrEnum):
    MUST_INCLUDE = "must_include"
    MUST_NOT_INCLUDE = "must_not_include"
    EXACT_ANSWER = "exact_answer"


_EXACT_NORMALIZE_DEFAULT = ("trim", "collapse_whitespace")
_ALLOWED_NORMALIZE = frozenset(_EXACT_NORMALIZE_DEFAULT)


class Criterion(BaseModel):
    """Structured task-level completion criterion (v0.6).

    Legacy bare strings coerce to ``must_include`` with the original text as
    ``value``, preserving v0.5 deterministic term matching.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: CriterionKind
    value: str
    normalize: tuple[str, ...] = ()

    @field_validator("value")
    @classmethod
    def _non_empty_value(cls, value: str) -> str:
        return non_empty(value.strip(), "criterion value")

    @field_validator("normalize")
    @classmethod
    def _clean_normalize(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        cleaned: list[str] = []
        for item in values:
            token = str(item).strip().lower()
            if not token:
                continue
            if token not in _ALLOWED_NORMALIZE:
                raise ValueError(
                    "criterion normalize entries must be 'trim' or 'collapse_whitespace'"
                )
            if token not in cleaned:
                cleaned.append(token)
        return tuple(cleaned)

    @model_validator(mode="before")
    @classmethod
    def _default_exact_normalize(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        kind = data.get("kind")
        kind_value = kind.value if isinstance(kind, CriterionKind) else str(kind or "")
        if kind_value == CriterionKind.EXACT_ANSWER.value:
            current = data.get("normalize")
            if current in (None, (), []):
                return {**data, "normalize": list(_EXACT_NORMALIZE_DEFAULT)}
            return data
        if data.get("normalize"):
            raise ValueError("normalize is only allowed for exact_answer criteria")
        return data

    def display(self) -> str:
        return f"{self.kind.value}:{self.value}"

    @classmethod
    def parse(cls, raw: CriterionInput) -> Criterion:
        if isinstance(raw, Criterion):
            return raw
        if isinstance(raw, dict):
            return cls.model_validate(raw)
        text = str(raw).strip()
        if not text:
            raise ValueError("criterion must not be empty")
        lowered = text.lower()
        for kind in CriterionKind:
            prefix = f"{kind.value}:"
            if lowered.startswith(prefix):
                value = text[len(prefix) :].strip()
                if not value:
                    raise ValueError(f"{kind.value} criterion value must not be empty")
                return cls(kind=kind, value=value)
        return cls(kind=CriterionKind.MUST_INCLUDE, value=text)


CriterionInput = str | dict[str, Any] | Criterion


def parse_criteria(values: Any) -> tuple[Criterion, ...]:
    if values is None:
        return ()
    if isinstance(values, (str, dict, Criterion)):
        values = (values,)
    if not isinstance(values, (list, tuple)):
        raise ValueError("criteria must be a list or tuple")
    cleaned: list[Criterion] = []
    seen: set[str] = set()
    for raw in values:
        if isinstance(raw, str) and not raw.strip():
            continue
        criterion = Criterion.parse(raw)
        key = criterion.display()
        if key not in seen:
            cleaned.append(criterion)
            seen.add(key)
    return tuple(cleaned)


class TaskContract(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    criteria: tuple[Criterion, ...] = ()
    search_policy: SearchPolicy = SearchPolicy.AUTO
    evidence_requirement: EvidenceRequirement = EvidenceRequirement.OPTIONAL
    origin: str = "default"

    @field_validator("criteria", mode="before")
    @classmethod
    def _coerce_criteria(cls, values: Any) -> tuple[Criterion, ...]:
        return parse_criteria(values)

    @field_validator("origin")
    @classmethod
    def _non_empty_origin(cls, value: str) -> str:
        return non_empty(value, "task contract origin")

    @classmethod
    def from_user(
        cls,
        *,
        criteria: tuple[CriterionInput, ...] = (),
        search_policy: SearchPolicy | str = SearchPolicy.AUTO,
        evidence_requirement: EvidenceRequirement | str = EvidenceRequirement.OPTIONAL,
    ) -> TaskContract:
        policy = SearchPolicy(search_policy)
        requirement = EvidenceRequirement(evidence_requirement)
        parsed = parse_criteria(criteria)
        origin = (
            "user"
            if parsed
            or policy is not SearchPolicy.AUTO
            or requirement is EvidenceRequirement.REQUIRED
            else "default"
        )
        return cls(
            criteria=parsed,
            search_policy=policy,
            evidence_requirement=requirement,
            origin=origin,
        )
