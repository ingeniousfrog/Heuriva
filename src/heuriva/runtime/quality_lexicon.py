"""Shared narrow quality-word lexicon for deterministic matching (v0.7).

Table-driven CN/EN equivalents for completion (and aligned relevance) checks.
This is not a semantic engine: expand only with evidence-backed rows and tests.
"""

from __future__ import annotations

import re

# Tokens skipped when extracting English/ASCII content terms from criteria text.
COMPLETION_STOP_WORDS = frozenset(
    {
        "about",
        "answer",
        "describe",
        "explain",
        "include",
        "mention",
        "must",
        "need",
        "needs",
        "provide",
        "show",
        "summarize",
        "the",
        "this",
        "with",
    }
)

# Broader English stop list for free-text quality-term extraction (goal/query/etc.).
RELEVANCE_STOP_WORDS = frozenset(
    {
        "about",
        "after",
        "again",
        "also",
        "answer",
        "could",
        "details",
        "evidence",
        "explain",
        "external",
        "find",
        "from",
        "have",
        "into",
        "need",
        "needed",
        "release",
        "search",
        "signal",
        "source",
        "support",
        "supporting",
        "that",
        "the",
        "this",
        "version",
        "what",
        "with",
    }
)

# Evidence-backed quality families (Post-v0.5 / Post-v0.6 reverse-conflict class).
TERM_EQUIVALENTS: dict[str, tuple[str, ...]] = {
    "safety": (
        "safety",
        "safe",
        "risk",
        "risks",
        "安全",
        "风险",
        "伤害",
        "未成年",
        "合规",
        "红线",
    ),
    "tradeoff": (
        "tradeoff",
        "tradeoffs",
        "trade-off",
        "trade-offs",
        "权衡",
        "取舍",
        "代价",
        "成本",
        "损失",
        "摩擦",
    ),
    "tradeoffs": (
        "tradeoffs",
        "tradeoff",
        "trade-offs",
        "trade-off",
        "权衡",
        "取舍",
        "代价",
        "成本",
        "损失",
        "摩擦",
    ),
}

_ASCII_TERM = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_-]{2,}")


def expand_criterion_terms(criterion: str) -> tuple[str, ...]:
    """Extract ASCII content terms from a criterion string and expand via lexicon."""
    return _expand_ascii_terms(criterion, stop_words=COMPLETION_STOP_WORDS, apply_equivalents=True)


def expand_text_terms(text: str, *, stop_words: frozenset[str] | None = None) -> tuple[str, ...]:
    """Extract and expand ASCII terms from free text (goal, query, evidence need)."""
    return _expand_ascii_terms(
        text,
        stop_words=stop_words if stop_words is not None else RELEVANCE_STOP_WORDS,
        apply_equivalents=True,
    )


def _expand_ascii_terms(
    text: str,
    *,
    stop_words: frozenset[str],
    apply_equivalents: bool,
) -> tuple[str, ...]:
    terms: list[str] = []
    for term in _ASCII_TERM.findall(text.lower()):
        if term in stop_words:
            continue
        expansions = TERM_EQUIVALENTS.get(term, (term,)) if apply_equivalents else (term,)
        for expanded in expansions:
            if expanded not in terms:
                terms.append(expanded)
    return tuple(terms)
