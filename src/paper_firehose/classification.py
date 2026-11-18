"""Bounded, explainable topic classification shared by ingestion and publication.

This is an evidence scorer, not a semantic model. Built-in topics distinguish
specific concepts from ambiguous terms and background mentions. Custom filters
remain supported without silently substituting the project's built-in taxonomy.
"""

from __future__ import annotations

import html
import logging
import re
import unicodedata
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import yaml

LOGGER = logging.getLogger(__name__)
CLASSIFIER_VERSION = 'evidence-v1'
PENDING_TOPIC = '_classification_pending'
_CONTRIBUTION = re.compile(
    r'\b(?:we|this (?:paper|work|study)|our (?:work|method|approach|framework))\s+'
    r'(?:(?:here|also|further|first)\s+)?(?:propos\w*|introduc\w*|present\w*|develop\w*|'
    r'design\w*|releas\w*|construct\w*|creat\w*|curat\w*|investigat\w*|analy[sz]\w*|'
    r'stud\w*|improv\w*|optimi[sz]\w*|address\w*|reformulat\w*|demonstrat\w*|'
    r'show\w*|deriv\w*|prov\w*|establish\w*|evaluat\w*)\b', re.I)
_BACKGROUND = re.compile(r'\b(?:previous|prior|existing|recent works?|related work|baselines?|for example|mention\w*)\b', re.I)
_INCIDENTAL = re.compile(
    r'\b(?:using|use|uses|used|based on|powered by|compared (?:with|to)|such as|e\.g\.)'
    r'[^.;!?]{0,65}$', re.I)
_SENTENCES = re.compile(r'(?<=[.!?;。！？；])\s+|\n+')


def _normalize(value: str) -> str:
    value = html.unescape(re.sub(r'<[^>]+>', ' ', value or ''))
    value = unicodedata.normalize('NFKC', value)
    return re.sub(r'[‐‑‒–—−]', '-', value)


def _concept(value: str) -> str:
    return re.sub(r'[-\s]+', ' ', value.casefold()).strip()


@lru_cache(maxsize=1)
def _defaults():
    root = Path(__file__).parent / 'system' / 'config'
    profiles = yaml.safe_load((root / 'topic_classification.yaml').read_text(encoding='utf-8'))
    filters = {}
    for key in profiles:
        config = yaml.safe_load((root / 'topics' / (key + '.yaml')).read_text(encoding='utf-8'))
        filters[key] = config['filter']['pattern']
    return profiles, filters


@dataclass(frozen=True)
class TopicMatch:
    topic: dict
    score: float
    evidence: tuple[str, ...]
    specificity: int = 0


class TopicClassifier:
    """Compile once per collection/build; return topics in evidence order.

    Scores are internal ordering weights, never probabilities or paper quality.
    One repeated concept counts once; title and contribution evidence outweigh
    incidental mentions. Invalid rules fail before any collection/publication.
    """

    def __init__(self, catalog):
        profiles, filters = _defaults()
        self.rules = []
        for topic in catalog:
            pattern = topic.get('pattern', '')
            if not pattern:
                continue
            key = topic['key']
            # Explicit custom patterns retain their meaning. Advanced guards can
            # be opted into through the topic's optional classification mapping.
            profile = topic.get('classification')
            if profile is None:
                profile = profiles.get(key, {}) if pattern == filters.get(key) else {}
            if not isinstance(profile, dict):
                raise ValueError('Invalid classification configuration for topic: ' + key)
            try:
                broad = re.compile(pattern, re.I)
                anchors = re.compile(profile.get('anchors') or pattern, re.I)
                context = re.compile(profile['context'], re.I) if profile.get('context') else None
                ambiguous = re.compile(profile['ambiguous'], re.I) if profile.get('ambiguous') else None
                exclude = re.compile(profile['exclude'], re.I) if profile.get('exclude') else None
            except (re.error, TypeError) as exc:
                raise ValueError('Invalid classification rule for topic: ' + key) from exc
            self.rules.append((topic, broad, anchors, context, ambiguous, exclude,
                               bool(profile), bool(profile.get('contribution_required'))))

    def is_candidate(self, title: str, abstract: str = '') -> bool:
        """Keep plausible papers searchable without assigning unsupported labels.

        Broad recall is deliberately more permissive than confirmed membership.
        Curated alias expansion still needs its topic's domain context.
        """
        text = _normalize(title) + '\n' + _normalize(abstract)
        return any(broad.search(text) or (anchors.search(text) and (not context or context.search(text)))
                   for _, broad, anchors, context, *_ in self.rules)

    def classify(self, title: str, abstract: str = '') -> list[TopicMatch]:
        title = _normalize(title)
        abstract = _normalize(abstract)
        sentences = [part.strip() for part in _SENTENCES.split(abstract) if part.strip()]
        combined = title + '\n' + abstract
        matches = []
        for topic, broad, anchors, context, ambiguous, exclude, guarded, contribution_required in self.rules:
            # Include curated anchors for spelling/phrase variants that the old
            # recall filter did not enumerate; custom profiles are explicit.
            if not broad.search(combined) and not anchors.search(combined):
                continue
            if context and not context.search(combined):
                continue
            evidence = {}
            for field, sentence in [('title', title), *(('abstract', part) for part in sentences)]:
                contribution = bool(_CONTRIBUTION.search(sentence))
                if exclude and exclude.search(sentence):
                    continue
                for hit in anchors.finditer(sentence):
                    incidental = field != 'title' and bool(_INCIDENTAL.search(sentence[:hit.start()]))
                    background = field != 'title' and bool(_BACKGROUND.search(sentence)) and not contribution
                    weak = bool(ambiguous and ambiguous.fullmatch(hit.group()))
                    # Broad terms require an actual contribution, not just an
                    # AI term elsewhere in the abstract or a generic title.
                    if weak and (not contribution or incidental or background):
                        continue
                    if contribution_required and field != 'title' and not contribution:
                        continue
                    if field == 'title':
                        weight = 6.0
                    elif incidental or background:
                        weight = 0.5
                    elif contribution:
                        weight = 4.0
                    else:
                        weight = 2.0
                    concept = _concept(hit.group())
                    if concept not in evidence or evidence[concept][0] < weight:
                        evidence[concept] = (weight, field + ': ' + sentence[:300])
            if not evidence:
                continue
            # Incidental mentions alone must not qualify, regardless of how
            # many baseline model names or repeated sentences occur.
            decisive = sorted((value[0] for value in evidence.values() if value[0] >= 2), reverse=True)
            score = sum(decisive[:4])
            minimum = max(1, int(topic.get('min_matches', 1)))
            threshold = max(3 if guarded else 2, minimum + 1)
            if score < threshold:
                continue
            specificity = max(len(concept) for concept, value in evidence.items() if value[0] >= 2)
            decisive_evidence = sorted(
                ((concept, value) for concept, value in evidence.items() if value[0] >= 2),
                key=lambda item: (-item[1][0], item[0]))[:4]
            result = TopicMatch(topic, score, tuple(concept + ' | ' + value[1]
                                                   for concept, value in decisive_evidence), specificity)
            matches.append(result)
            LOGGER.debug('Topic evidence key=%s weight=%.1f evidence=%s', topic['key'], score, result.evidence)
        # Prefer the more specific concept on equal weights ("large language
        # models" over its "language models" substring), then a stable key.
        return sorted(matches, key=lambda match: (-match.score, -match.specificity, match.topic['key']))
