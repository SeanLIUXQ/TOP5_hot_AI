AI_TOPICS = [
    "artificial-intelligence",
    "machine-learning",
    "deep-learning",
    "llm",
    "large-language-models",
    "generative-ai",
    "rag",
    "retrieval-augmented-generation",
    "ai-agent",
    "agents",
    "computer-vision",
    "nlp",
    "inference",
    "vector-database",
]

AI_KEYWORDS = [
    "ai",
    "artificial intelligence",
    "machine learning",
    "deep learning",
    "llm",
    "large language model",
    "rag",
    "retrieval augmented generation",
    "agent",
    "generative ai",
    "diffusion",
    "transformer",
    "inference",
    "embedding",
    "fine-tuning",
    "fine tuning",
    "nlp",
    "computer vision",
]

LIST_REPOSITORY_KEYWORDS = [
    "awesome",
    "curated-list",
    "papers",
    "paper-list",
    "course",
    "tutorial",
    "roadmap",
    "collection",
]


def build_search_queries(min_stars: int) -> list[str]:
    base = f"stars:>={min_stars} fork:false archived:false"
    topic_queries = [f"topic:{topic} {base}" for topic in AI_TOPICS[:10]]
    keyword_queries = [
        f'"large language model" in:description {base}',
        f'"retrieval augmented generation" in:description {base}',
        f'"AI agent" in:description {base}',
        f'"LLM" in:name,description {base}',
        f'"inference" "transformer" in:description {base}',
    ]
    return topic_queries + keyword_queries

