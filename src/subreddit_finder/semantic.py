from __future__ import annotations

from collections.abc import Iterable

CURATED_SYNONYMS = {
    "housing": [
        "rent",
        "rental",
        "real estate",
        "tenant",
        "landlord",
        "mortgage",
        "apartment",
        "home",
        "affordable housing",
        "property",
        "urban planning",
    ],
    "jobs": ["employment", "career", "hiring", "recruitment", "work", "occupation"],
    "fitness": ["exercise", "workout", "training", "wellness", "health", "gym"],
}


def _wordnet_terms(keyword: str, max_terms: int) -> list[str]:
    try:
        from nltk.corpus import wordnet as wn  # type: ignore
    except Exception:
        return []

    terms: set[str] = set()
    for synset in wn.synsets(keyword):
        for lemma in synset.lemmas():
            value = lemma.name().replace("_", " ").strip().lower()
            if value and value != keyword.lower():
                terms.add(value)
        if len(terms) >= max_terms:
            break
    return sorted(terms)[:max_terms]


def expand_keyword(keyword: str, min_terms: int = 20, max_terms: int = 50) -> list[str]:
    """Generate related query terms from curated and WordNet sources."""
    root = keyword.strip().lower()
    expanded: list[str] = [root]

    for item in CURATED_SYNONYMS.get(root, []):
        if item not in expanded:
            expanded.append(item)

    for item in _wordnet_terms(root, max_terms=max_terms):
        if item not in expanded:
            expanded.append(item)

    if len(expanded) < min_terms:
        fallback = [
            f"{root} advice",
            f"{root} community",
            f"{root} help",
            f"{root} discussion",
            f"{root} guide",
            f"{root} tips",
            f"{root} support",
            f"{root} news",
            f"{root} resources",
            f"{root} questions",
        ]
        for item in fallback:
            if item not in expanded:
                expanded.append(item)
            if len(expanded) >= min_terms:
                break

    return expanded[:max_terms]


def keyword_frequency_score(text: str, terms: Iterable[str]) -> float:
    lowered = text.lower()
    score = 0.0
    for term in terms:
        token = term.lower()
        score += lowered.count(token)
    return score
