"""
FRIS NLP Service — offline entity extraction from unstructured text (PDFs,
emails), the unstructured-to-structured wedge a16z's 2026 thesis names as
the generational opportunity: turning "PDFs, screenshots, videos, logs,
emails, and semi-structured sludge" into fused, structured entities the
graph can reconcile like any other source.

Mirrors embedding_service.py's offline-first contract: no external API call
by default, so air-gapped deployments work unchanged. Two backends:

  heuristic - regex/pattern-based extraction (organizations, person names,
              emails, monetary amounts). Zero dependencies beyond the
              standard library, always available, moderate precision.
  spacy     - if spaCy and a model are installed (an operator's choice —
              not a hard dependency of this codebase, same optional-import
              pattern connectors/files.py already uses for chardet),
              named-entity recognition gives materially better precision.
              Falls back to heuristic automatically if unavailable.

Extraction confidence feeds the same fusion_engine.py pipeline every
structured connector uses — a low-confidence extraction lands in the
Review Queue exactly like a low-confidence structured-source match, rather
than being silently trusted.
"""

import re
from typing import Any, Dict, List

ORG_SUFFIXES = [
    "Inc", "Inc.", "Incorporated", "Corp", "Corp.", "Corporation", "Company", "Co", "Co.",
    "LLC", "L.L.C.", "Ltd", "Ltd.", "Limited", "PLC", "GmbH", "Pvt", "Pty", "LLP", "LP",
]
_ORG_SUFFIX_PATTERN = "|".join(re.escape(s) for s in ORG_SUFFIXES)

# Words that precede a Title Case span often enough (email/document headers,
# salutations) to produce false-positive "person" matches if included —
# e.g. "Contact Maria Rodriguez" or "Subject: Contract Renewal". Stripped
# from the front of a match rather than excluded from the character class,
# so "Contact Maria Rodriguez" still yields "Maria Rodriguez".
_PERSON_LEADING_STOPWORDS = {
    "Subject", "From", "To", "Cc", "Bcc", "Dear", "Regards", "Sincerely",
    "Contact", "Attn", "Re", "Fwd", "Hi", "Hello",
}

# [ \t]+ (not \s+) between words so a match can never span a newline —
# otherwise two unrelated header lines like "...Renewal\nFrom Acme..."
# collapse into one bogus multi-word match.
_ORG_RE = re.compile(
    r"\b([A-Z][\w&.,'-]*(?:[ \t]+[A-Z][\w&.,'-]*){0,4}[ \t]+(?:" + _ORG_SUFFIX_PATTERN + r"))\b"
)
# Two or three consecutive Title Case words, e.g. "John Smith" / "Maria De La Cruz"
# (capped at 3 words to avoid swallowing headline-style sentence fragments).
_PERSON_RE = re.compile(r"\b([A-Z][a-z]+(?:[ \t]+[A-Z][a-z]+){1,2})\b")
_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_MONEY_RE = re.compile(r"(?:USD|SAR|AED|\$|PKR)\s?[\d,]+(?:\.\d{1,2})?", re.IGNORECASE)


class NLPService:
    """Loads once at startup (mirrors EmbeddingService's lifecycle) and is shared across all extraction calls."""

    def __init__(self, backend: str = "auto"):
        self._spacy_nlp = None
        if backend in ("auto", "spacy"):
            self._try_load_spacy()

    def _try_load_spacy(self):
        try:
            import spacy
            self._spacy_nlp = spacy.load("en_core_web_sm")
        except Exception:
            # Not installed, or model not downloaded — heuristic backend
            # covers this case fully offline, no error surfaced to the caller.
            self._spacy_nlp = None

    @property
    def backend(self) -> str:
        return "spacy" if self._spacy_nlp is not None else "heuristic"

    def extract_entities(self, text: str, max_entities: int = 200) -> List[Dict[str, Any]]:
        """
        Returns a list of {name, entity_type, confidence, email?, amount?,
        source_snippet} dicts — one per distinct entity mention found in
        text. Deduplicated by (entity_type, name) within a single document;
        cross-document dedup is fusion_engine.py's job, same as any
        structured source.
        """
        if not text or not text.strip():
            return []
        if self._spacy_nlp is not None:
            return self._extract_spacy(text)[:max_entities]
        return self._extract_heuristic(text)[:max_entities]

    def _extract_spacy(self, text: str) -> List[Dict[str, Any]]:
        doc = self._spacy_nlp(text[:100_000])  # cap input for pathologically large documents
        seen = set()
        results = []
        label_map = {"ORG": "Organization", "PERSON": "Person", "GPE": "Location", "MONEY": "Transaction"}
        for ent in doc.ents:
            entity_type = label_map.get(ent.label_)
            if not entity_type:
                continue
            key = (entity_type, ent.text.strip().lower())
            if key in seen:
                continue
            seen.add(key)
            results.append({
                "name": ent.text.strip(),
                "entity_type": entity_type,
                "confidence": 0.8,  # spaCy's small model doesn't expose per-entity scores; fixed baseline above heuristic
                "source_snippet": text[max(0, ent.start_char - 40):ent.end_char + 40].strip(),
            })
        return results

    def _extract_heuristic(self, text: str) -> List[Dict[str, Any]]:
        seen = set()
        results = []

        for match in _ORG_RE.finditer(text):
            name = match.group(1).strip()
            key = ("Organization", name.lower())
            if key in seen:
                continue
            seen.add(key)
            results.append({
                "name": name, "entity_type": "Organization", "confidence": 0.6,
                "source_snippet": text[max(0, match.start() - 40):match.end() + 40].strip(),
            })

        for match in _EMAIL_RE.finditer(text):
            email = match.group(0).strip()
            key = ("Person", email.lower())
            if key in seen:
                continue
            seen.add(key)
            # An email is a decisive identifier (EntityResolver.DECISIVE_IDENTIFIER_FIELDS),
            # so this extraction is high-confidence even without a name attached.
            results.append({
                "name": email, "entity_type": "Person", "email": email, "confidence": 0.85,
                "source_snippet": text[max(0, match.start() - 40):match.end() + 40].strip(),
            })

        for match in _PERSON_RE.finditer(text):
            name = match.group(1).strip()
            words = name.split()
            # Strip a leading header/salutation word (e.g. "Contact Maria
            # Rodriguez" -> "Maria Rodriguez") rather than discarding the
            # whole match, then require at least 2 words to remain.
            if words[0] in _PERSON_LEADING_STOPWORDS:
                words = words[1:]
                if len(words) < 2:
                    continue
                name = " ".join(words)
            # Skip if this span is actually the start of an already-captured
            # organization name (e.g. "Acme Global" inside "Acme Global Corp").
            if any(name in r["name"] or r["name"] in name for r in results if r["entity_type"] == "Organization"):
                continue
            key = ("Person", name.lower())
            if key in seen:
                continue
            seen.add(key)
            results.append({
                "name": name, "entity_type": "Person", "confidence": 0.5,
                "source_snippet": text[max(0, match.start() - 40):match.end() + 40].strip(),
            })

        for match in _MONEY_RE.finditer(text):
            amount = match.group(0).strip()
            key = ("Transaction", amount.lower())
            if key in seen:
                continue
            seen.add(key)
            results.append({
                "name": amount, "entity_type": "Transaction", "amount": amount, "confidence": 0.55,
                "source_snippet": text[max(0, match.start() - 40):match.end() + 40].strip(),
            })

        return results
