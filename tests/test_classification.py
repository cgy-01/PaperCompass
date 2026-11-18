"""Classification regressions: research evidence, rather than keyword counts."""

from pathlib import Path

import pytest
import yaml

from paper_firehose.classification import TopicClassifier


@pytest.fixture
def catalog():
    root = Path(__file__).resolve().parents[1] / 'src/paper_firehose/system/config/topics'
    return [dict(key=path.stem, name=config['name'], **config['filter'])
            for path in sorted(root.glob('*.yaml'))
            if path.stem not in {'demo_topic', 'ccf_a_all'}
            for config in [yaml.safe_load(path.read_text(encoding='utf-8'))]]


@pytest.mark.parametrize('key,title,abstract', [
    ('llm', 'Efficient large language models', 'We propose a new pretraining architecture.'),
    ('agents', 'Marathoner: Ultra-Long-Horizon Autonomous Intelligence',
     'In this paper, we propose Marathoner, an autonomous agentic model possessing '
     'the ability of ultra-long-horizon execution. Specifically, we propose a '
     'comprehensive post-training pipeline to instill this capability into base model.'),
    ('ai_infra', 'Efficient LLM serving', 'We propose a KV cache scheduler for GPU inference.'),
    ('reasoning_post_training', 'Preference optimization with DPO', 'We improve model reasoning.'),
    ('multimodal_generative', 'Vision-language models for video generation', 'A new architecture.'),
    ('ai_safety_alignment', 'Preventing prompt injection in language models', 'We propose a guardrail.'),
    ('data_evaluation', 'A new resource for vision',
     'We introduce a new dataset for object detection and release the annotations.'),
    ('embodied_robotics', 'Robot manipulation in the wild', 'We propose imitation learning.'),
    ('reinforcement_learning', 'Offline reinforcement learning', 'We propose policy optimization.'),
    ('ai_for_science', 'Neural operators for scientific discovery', 'We develop a neural model.'),
    ('vision_graphics', 'Fast 3D reconstruction', 'We propose neural rendering.'),
    ('ml_foundations', 'Generalization bounds in learning theory', 'We study statistical learning.'),
    ('language_speech_retrieval', 'Efficient dense retrieval', 'We propose a document retrieval model.'),
    ('systems_architecture', 'Fault tolerance in distributed systems', 'We design a consensus protocol.'),
])
def test_each_curated_direction_has_positive_evidence(catalog, key, title, abstract):
    results = TopicClassifier(catalog).classify(title, abstract)
    assert key in {result.topic['key'] for result in results}
    assert all(result.evidence for result in results)


@pytest.mark.parametrize('key,title,abstract', [
    ('llm', 'A segmentation method', 'We use an LLM to label images. We compare with another LLM.'),
    ('llm', 'Image restoration', 'Existing baselines include GPT-4, Llama, Qwen, Claude and Gemini.'),
    ('llm', 'An empirical study', 'An LLM is mentioned. An LLM is mentioned. An LLM is mentioned.'),
    ('ai_infra', 'Low latency image processing', 'We propose a new image processing algorithm.'),
    ('ai_infra', 'GPU throughput measurements', 'Existing GPU baselines have high throughput.'),
    ('ai_safety_alignment', 'Alignment of images', 'We propose image alignment with a neural model.'),
    ('ai_safety_alignment', 'Protein sequence alignment', 'We introduce a neural model for sequence alignment.'),
    ('data_evaluation', 'A better classifier', 'We evaluate our method on ten datasets and three benchmarks.'),
    ('data_evaluation', 'A benchmark comparison', 'Existing benchmark suites are used for evaluation.'),
    ('ml_foundations', 'A robotic system', 'We use deep learning for classification and regression.'),
])
def test_incidental_ambiguous_or_repeated_words_do_not_qualify(catalog, key, title, abstract):
    assert key not in {result.topic['key'] for result in TopicClassifier(catalog).classify(title, abstract)}


def test_primary_topic_uses_evidence_not_catalog_order(catalog):
    title = 'Distributed training and KV cache for LLM serving'
    abstract = 'We propose a new machine learning system for model inference.'
    forward = TopicClassifier(catalog).classify(title, abstract)
    reverse = TopicClassifier(list(reversed(catalog))).classify(title, abstract)
    assert forward[0].topic['key'] == 'ai_infra'
    assert [item.topic['key'] for item in forward] == [item.topic['key'] for item in reverse]
    assert 'llm' in {item.topic['key'] for item in forward}


@pytest.mark.parametrize('key,title,abstract', [
    ('vision_graphics', 'Sapiens2',
     'We present Sapiens2, a model family of high-resolution transformers for human-centric vision.'),
    ('agents', 'PExA: Parallel Exploration Agent for Complex Text-to-SQL',
     'LLM-based agents for text-to-SQL struggle with latency. We reformulate query generation.'),
    ('ai_safety_alignment', 'Defense Against Knowledge Poisoning Attack on GraphRAG',
     'GraphRAG augments large language models with structured knowledge graphs. We propose a defense.'),
    ('ai_safety_alignment', 'On the Rejection Criterion for Proxy-based Test-time Alignment',
     'An aligned model guides generation. We propose a new rejection criterion.'),
    ('language_speech_retrieval', 'De-biasing Listwise Rerankers with Probability Calibration',
     'We propose a training-free framework for retrieval.'),
    ('language_speech_retrieval', 'Reviving Iterative Refinement in Diffusion-based NER',
     'We propose an initializer-restorer approach for named entity recognition.'),
    ('language_speech_retrieval', 'Enhancing Linguistic Competence of Language Models',
     'We propose a pre-training framework integrating language learning tasks.'),
])
def test_real_catalog_cases_preserve_recall(catalog, key, title, abstract):
    assert key in {item.topic['key'] for item in TopicClassifier(catalog).classify(title, abstract)}


def test_specific_llm_phrase_wins_over_language_model_substring(catalog):
    results = TopicClassifier(catalog).classify('Efficient large language models', '')
    assert results[0].topic['key'] == 'llm'


def test_duplicate_abstract_or_repeated_concept_cannot_increase_weight(catalog):
    classifier = TopicClassifier(catalog)
    sentence = 'We propose an autonomous agentic model.'
    first = classifier.classify('Marathoner', sentence)
    duplicated = classifier.classify('Marathoner', sentence + ' ' + sentence)
    assert [(item.topic['key'], item.score) for item in first] == [
        (item.topic['key'], item.score) for item in duplicated]


def test_unicode_hyphens_and_html_are_normalized(catalog):
    assert 'reasoning_post_training' in {item.topic['key'] for item in TopicClassifier(catalog).classify(
        'Post‑training language models', '<p>We propose a model improvement.</p>')}


def test_custom_pattern_remains_effective_and_distinct_matches_are_required():
    classifier = TopicClassifier([{'key': 'llm', 'pattern': r'\b(custom|special)\b', 'min_matches': 2}])
    assert classifier.classify('Custom methods')
    assert classifier.classify('A method', 'Custom and special properties.')
    assert not classifier.classify('LLM research', 'LLM LLM LLM')
    assert not classifier.classify('A method', 'Custom custom custom.')


@pytest.mark.parametrize('config', [
    {'key': 'broken', 'pattern': '['},
    {'key': 'broken', 'pattern': 'test', 'classification': {'anchors': '['}},
    {'key': 'broken', 'pattern': 'test', 'classification': 'not a mapping'},
])
def test_invalid_rules_fail_explicitly(config):
    with pytest.raises(ValueError, match='broken'):
        TopicClassifier([config])


def test_empty_metadata_is_not_a_topic(catalog):
    assert TopicClassifier(catalog).classify('', '') == []


def test_uncertain_papers_are_candidates_without_becoming_confirmed_topics(catalog):
    classifier = TopicClassifier(catalog)
    title, abstract = 'An empirical study', 'We use an LLM as a baseline.'
    assert classifier.classify(title, abstract) == []
    assert classifier.is_candidate(title, abstract)
    assert not classifier.is_candidate('A medieval history essay', 'An analysis of ancient manuscripts.')


def test_explanation_reports_decisive_evidence_instead_of_baseline_mentions(catalog):
    results = TopicClassifier(catalog).classify('A model architecture',
        'Baselines include GPT-4, Llama, Qwen, Claude and Gemini. '
        'We propose a new large language model architecture.')
    match = next(item for item in results if item.topic['key'] == 'llm')
    assert match.evidence[0].startswith('large language model | abstract: We propose')
    assert not any('Baselines' in evidence for evidence in match.evidence)
