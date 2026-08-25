from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlparse

from heuriva.config import QualityConfig, QualityMode
from heuriva.core.decision import Decision, SearchParams
from heuriva.core.evaluation import CandidateAssessment, RelevanceVerdict
from heuriva.core.observation import SourceRef
from heuriva.core.state import CognitiveState, EvidenceItem
from heuriva.runtime.quality_lexicon import (
    RELEVANCE_STOP_WORDS,
    expand_criterion_terms,
    expand_text_terms,
)


@dataclass(frozen=True)
class EvidenceRelevanceResult:
    accepted_sources: tuple[SourceRef, ...]
    accepted_evidence: tuple[EvidenceItem, ...]
    assessments: tuple[CandidateAssessment, ...]
    raw_candidate_count: int
    accepted_evidence_count: int
    rejected_candidate_count: int


def assess_search_candidates(
    *,
    state: CognitiveState,
    decision: Decision,
    sources: tuple[SourceRef, ...],
    quality: QualityConfig,
) -> EvidenceRelevanceResult:
    if not isinstance(decision.params, SearchParams):
        return EvidenceRelevanceResult((), (), (), len(sources), 0, len(sources))
    existing_urls = {item.source_ref.strip().lower() for item in state.evidence}
    accepted_sources: list[SourceRef] = []
    accepted_evidence: list[EvidenceItem] = []
    assessments: list[CandidateAssessment] = []
    for source in sources:
        assessment = _assess_source(
            state=state,
            params=decision.params,
            source=source,
            existing_urls=existing_urls,
            mode=quality.evidence_relevance_mode,
        )
        assessments.append(assessment)
        if _accepts(assessment, mode=quality.evidence_relevance_mode):
            accepted_sources.append(source)
            accepted_evidence.append(
                EvidenceItem(
                    content=f"{source.title}: {source.snippet}".strip(),
                    source_type="search",
                    source_ref=source.url,
                    retrieved_at=source.retrieved_at,
                    query=decision.params.query,
                    relevance_verdict=assessment.verdict.value,
                    supports_criteria=assessment.supports_criteria,
                    assessment_origin=assessment.assessment_origin,
                )
            )
            existing_urls.add(source.url.strip().lower())
    return EvidenceRelevanceResult(
        accepted_sources=tuple(accepted_sources),
        accepted_evidence=tuple(accepted_evidence),
        assessments=tuple(assessments),
        raw_candidate_count=len(sources),
        accepted_evidence_count=len(accepted_evidence),
        rejected_candidate_count=len(sources) - len(accepted_evidence),
    )


def _assess_source(
    *,
    state: CognitiveState,
    params: SearchParams,
    source: SourceRef,
    existing_urls: set[str],
    mode: QualityMode,
) -> CandidateAssessment:
    if mode is QualityMode.OFF:
        return CandidateAssessment(
            rank=source.rank or 1,
            verdict=RelevanceVerdict.RELEVANT,
            supports_criteria=tuple(item.display() for item in state.task_contract.criteria),
            reason="relevance assessment is disabled",
            assessment_origin="off",
        )
    url = source.url.strip()
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return CandidateAssessment(
            rank=source.rank or 1,
            verdict=RelevanceVerdict.IRRELEVANT,
            reason="candidate URL is not an http(s) URL",
        )
    if url.lower() in existing_urls:
        return CandidateAssessment(
            rank=source.rank or 1,
            verdict=RelevanceVerdict.IRRELEVANT,
            reason="candidate URL was already accepted",
        )
    if not source.title.strip() or not source.snippet.strip():
        return CandidateAssessment(
            rank=source.rank or 1,
            verdict=RelevanceVerdict.IRRELEVANT,
            reason="candidate is missing title or snippet",
        )
    haystack = f"{source.title} {source.snippet} {source.url}".lower()
    terms = _quality_terms(state, params)
    matches = tuple(term for term in terms if term in haystack)
    supported = tuple(
        criterion.display()
        for criterion in state.task_contract.criteria
        if _criterion_has_match(criterion.value, haystack)
    )
    if len(matches) >= 2 or supported:
        verdict = RelevanceVerdict.RELEVANT
        reason = "candidate overlaps with task, criteria, or expected signal"
    elif len(matches) == 1:
        verdict = RelevanceVerdict.PARTIAL
        reason = "candidate has partial overlap with the search intent"
    else:
        verdict = RelevanceVerdict.IRRELEVANT
        reason = "candidate does not overlap with the task evidence need"
    return CandidateAssessment(
        rank=source.rank or 1,
        verdict=verdict,
        supports_criteria=supported,
        reason=reason,
    )


def _accepts(assessment: CandidateAssessment, *, mode: QualityMode) -> bool:
    if mode is not QualityMode.ENFORCE:
        return (
            assessment.verdict is not RelevanceVerdict.IRRELEVANT
            or assessment.assessment_origin == "off"
        )
    return assessment.verdict in {RelevanceVerdict.RELEVANT, RelevanceVerdict.PARTIAL}


def _quality_terms(state: CognitiveState, params: SearchParams) -> tuple[str, ...]:
    text = " ".join(
        (
            state.goal,
            " ".join(item.value for item in state.task_contract.criteria),
            params.query,
            params.evidence_need,
            params.expected_signal,
        )
    )
    return expand_text_terms(text, stop_words=RELEVANCE_STOP_WORDS)


def _criterion_has_match(criterion: str, haystack: str) -> bool:
    return any(term in haystack for term in expand_criterion_terms(criterion))
