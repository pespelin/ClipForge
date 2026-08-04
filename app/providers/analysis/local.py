import re
from collections import Counter

from app.schemas.video import VideoMetadata
from app.schemas.video_analysis import (
    ClipCandidate,
    HookCandidate,
    TopicResult,
    VideoAnalysisResult,
)

SUMMARY_MAX_LENGTH = 240
KEYWORD_LIMIT = 10
TOPIC_LIMIT = 3
WORDS_PER_SECOND = 2.5
SHORTS_MAX_DURATION = 60.0

TOKEN_PATTERN = re.compile(r"[a-zA-Z]+(?:'[a-zA-Z]+)?")
STOP_WORDS = frozenset(
    {
        "about",
        "after",
        "again",
        "all",
        "also",
        "and",
        "any",
        "are",
        "because",
        "been",
        "before",
        "being",
        "but",
        "can",
        "could",
        "did",
        "does",
        "doing",
        "down",
        "each",
        "few",
        "for",
        "from",
        "has",
        "had",
        "have",
        "her",
        "here",
        "hers",
        "him",
        "his",
        "how",
        "into",
        "its",
        "just",
        "more",
        "most",
        "not",
        "other",
        "our",
        "ours",
        "out",
        "over",
        "same",
        "she",
        "should",
        "some",
        "such",
        "that",
        "than",
        "the",
        "their",
        "then",
        "there",
        "they",
        "this",
        "too",
        "under",
        "until",
        "very",
        "was",
        "were",
        "what",
        "when",
        "where",
        "which",
        "while",
        "who",
        "why",
        "will",
        "with",
        "would",
        "you",
        "your",
    }
)
POSITIVE_WORDS = frozenset(
    {"amazing", "best", "excellent", "good", "great", "happy", "improve", "love", "success"}
)
NEGATIVE_WORDS = frozenset(
    {"awful", "bad", "fail", "failure", "hate", "poor", "problem", "sad", "terrible", "worst"}
)


class UnusableTranscriptError(ValueError):
    """Raised when local analysis cannot derive content from a transcript."""


class LocalVideoAnalyzer:
    """Deterministic offline analyzer intended only as a development fallback.

    Its keyword counts and small sentiment vocabularies are transparent heuristics,
    not production-grade natural-language understanding.
    """

    async def analyze(
        self, transcript: str, *, metadata: VideoMetadata | None
    ) -> VideoAnalysisResult:
        normalized = " ".join(transcript.split())
        tokens = [match.group().casefold() for match in TOKEN_PATTERN.finditer(normalized)]
        if not normalized or not tokens:
            raise UnusableTranscriptError("Transcript contains no analyzable words")

        keywords, counts = self._keywords(tokens)
        duration = self._usable_duration(metadata, len(tokens))
        summary = self._truncate_words(normalized, SUMMARY_MAX_LENGTH)
        if not summary:
            raise UnusableTranscriptError(
                "Transcript cannot be summarized without splitting a word"
            )
        return VideoAnalysisResult(
            summary=summary,
            topics=self._topics(keywords, counts),
            keywords=keywords,
            sentiment=self._sentiment(tokens),
            hook_candidates=[self._hook(normalized, duration)],
            clip_candidates=self._clips(keywords, duration),
        )

    @staticmethod
    def _keywords(tokens: list[str]) -> tuple[list[str], Counter[str]]:
        counts = Counter(token for token in tokens if len(token) >= 3 and token not in STOP_WORDS)
        keywords = sorted(counts, key=lambda token: (-counts[token], token))[:KEYWORD_LIMIT]
        return keywords, counts

    @staticmethod
    def _topics(keywords: list[str], counts: Counter[str]) -> list[TopicResult]:
        if not keywords:
            return [
                TopicResult(
                    name="General",
                    description="No distinctive topic keywords were found.",
                    relevance=0.0,
                )
            ]
        strongest_count = counts[keywords[0]]
        return [
            TopicResult(
                name=keyword.replace("'", "’").title(),
                description=f"Frequently occurring transcript keyword: {keyword}.",
                relevance=round(counts[keyword] / strongest_count, 3),
            )
            for keyword in keywords[:TOPIC_LIMIT]
        ]

    @staticmethod
    def _sentiment(tokens: list[str]) -> str:
        positive_count = sum(token in POSITIVE_WORDS for token in tokens)
        negative_count = sum(token in NEGATIVE_WORDS for token in tokens)
        if positive_count > negative_count:
            return "positive"
        if negative_count > positive_count:
            return "negative"
        return "neutral"

    @classmethod
    def _hook(cls, transcript: str, duration: float) -> HookCandidate:
        hook_text = cls._truncate_words(transcript, 160) or transcript.split()[0]
        return HookCandidate(
            text=hook_text,
            start_time=0.0,
            end_time=min(duration, 5.0),
            reason="Opening transcript segment suitable for an introductory hook.",
            score=0.6,
        )

    @staticmethod
    def _clips(keywords: list[str], duration: float) -> list[ClipCandidate]:
        clips = []
        start = 0.0
        clip_number = 1
        while start < duration and len(clips) < 3:
            end = min(start + SHORTS_MAX_DURATION, duration)
            label = (
                keywords[clip_number - 1].title() if clip_number <= len(keywords) else "Highlights"
            )
            clips.append(
                ClipCandidate(
                    title=f"{label} Clip {clip_number}",
                    start_time=start,
                    end_time=end,
                    reason="Deterministic Shorts-compatible transcript window.",
                    score=0.5,
                )
            )
            start = end
            clip_number += 1
        return clips

    @staticmethod
    def _usable_duration(metadata: VideoMetadata | None, word_count: int) -> float:
        if metadata is not None and metadata.duration is not None and metadata.duration > 0:
            return metadata.duration
        return max(1.0, word_count / WORDS_PER_SECOND)

    @staticmethod
    def _truncate_words(text: str, maximum_length: int) -> str:
        if len(text) <= maximum_length:
            return text
        words = text.split()
        selected: list[str] = []
        current_length = 0
        for word in words:
            next_length = current_length + len(word) + bool(selected)
            if next_length > maximum_length:
                break
            selected.append(word)
            current_length = next_length
        return " ".join(selected)
