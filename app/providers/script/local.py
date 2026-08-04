import math
import re

from app.models.script import ScriptTone
from app.schemas.script import (
    ScriptGenerationResult,
    ScriptGeneratorInput,
    ScriptSection,
)
from app.schemas.video_analysis import ClipCandidate, HookCandidate, TopicResult

WORDS_PER_MINUTE = 150
WORDS_PER_SECOND = WORDS_PER_MINUTE / 60
MAX_HOOK_WORDS = 14
MAX_TITLE_CHARACTERS = 60
MEANINGFUL_TEXT_PATTERN = re.compile(r"[A-Za-z0-9]")

TONE_BODY_TEMPLATES = {
    ScriptTone.ENGAGING: "{summary} Here is the key: focus on {topic}.",
    ScriptTone.EDUCATIONAL: "The key lesson is {topic}: {summary}",
    ScriptTone.DRAMATIC: "The turning point is {topic}. {summary}",
    ScriptTone.HUMOROUS: "Here is the surprisingly simple part about {topic}: {summary}",
    ScriptTone.INSPIRATIONAL: (
        "{summary} The takeaway is that progress in {topic} starts with one clear step."
    ),
    ScriptTone.NEUTRAL: "{summary} The main topic is {topic}.",
}

TONE_CALLS_TO_ACTION = {
    ScriptTone.ENGAGING: "Follow for more practical ideas.",
    ScriptTone.EDUCATIONAL: "Follow for more clear lessons.",
    ScriptTone.DRAMATIC: "Follow to see what happens next.",
    ScriptTone.HUMOROUS: "Follow for more useful surprises.",
    ScriptTone.INSPIRATIONAL: "Follow for your next step forward.",
    ScriptTone.NEUTRAL: "Follow for more concise updates.",
}


class UnsupportedScriptLanguageError(ValueError):
    """Raised when the local adapter is asked to generate non-English output."""


class UnusableScriptInputError(ValueError):
    """Raised when no meaningful source text can be derived from the input."""


class LocalScriptGenerator:
    """Deterministic English-only generator for development and testing.

    Tone changes use fixed, transparent templates. They are heuristic phrasing
    adjustments rather than production-grade language generation.
    """

    async def generate(self, generation_input: ScriptGeneratorInput) -> ScriptGenerationResult:
        self._validate_language(generation_input.options.language)
        self._validate_usable_input(generation_input)

        hook_candidate = generation_input.selected_hook_candidate or self._strongest_hook(
            generation_input.analysis.hook_candidates
        )
        clip_candidate = generation_input.selected_clip_candidate or self._strongest_clip(
            generation_input.analysis.clip_candidates
        )
        target_words = self._target_word_budget(generation_input)
        call_to_action = self._call_to_action(generation_input, target_words)
        call_to_action_words = self._word_count(call_to_action)

        hook_source = self._hook_source(generation_input, hook_candidate)
        hook_budget = min(MAX_HOOK_WORDS, max(1, target_words // 4))
        hook = self._finish_sentence(self._truncate_words(hook_source, hook_budget))

        body_budget = max(1, target_words - self._word_count(hook) - call_to_action_words)
        body_source = self._body_source(generation_input, clip_candidate)
        body = self._finish_sentence(self._truncate_words(body_source, body_budget))

        title = self._title(generation_input)
        parts = [hook, body]
        if call_to_action is not None:
            parts.append(call_to_action)
        full_script = self._normalize(" ".join(parts))

        duration = self._usable_video_duration(generation_input)
        hook_range = self._source_range(hook_candidate, duration)
        if hook_range is None:
            hook_range = self._fallback_range(duration, 3.0)
        body_range = self._source_range(clip_candidate, duration)
        if body_range is None:
            body_range = self._fallback_range(
                duration, generation_input.options.target_duration_seconds
            )

        sections = [self._section(0, "hook", hook, hook_range)]
        sections.append(self._section(1, "body", body, body_range))
        if call_to_action is not None:
            sections.append(self._section(2, "call_to_action", call_to_action, None))

        return ScriptGenerationResult(
            title=title,
            hook=hook,
            body=body,
            call_to_action=call_to_action,
            full_script=full_script,
            estimated_duration_seconds=self._estimate_duration(full_script),
            sections=sections,
        )

    @staticmethod
    def _validate_language(language: str) -> None:
        if language.split("-", maxsplit=1)[0] != "en":
            raise UnsupportedScriptLanguageError(
                f"LocalScriptGenerator supports English only, not {language}"
            )

    @classmethod
    def _validate_usable_input(cls, generation_input: ScriptGeneratorInput) -> None:
        analysis = generation_input.analysis
        source_values = [generation_input.transcript, analysis.summary]
        source_values.extend(topic.name for topic in analysis.topics)
        source_values.extend(analysis.keywords)
        source_values.extend(candidate.text for candidate in analysis.hook_candidates)
        source_values.extend(candidate.title for candidate in analysis.clip_candidates)
        if not any(cls._is_meaningful(value) for value in source_values):
            raise UnusableScriptInputError("Script input contains no meaningful source text")

    @classmethod
    def _hook_source(
        cls, generation_input: ScriptGeneratorInput, candidate: HookCandidate | None
    ) -> str:
        if candidate is not None and cls._is_meaningful(candidate.text):
            return candidate.text
        if cls._is_meaningful(generation_input.analysis.summary):
            return generation_input.analysis.summary
        if cls._is_meaningful(generation_input.transcript):
            return generation_input.transcript
        raise UnusableScriptInputError("No usable hook source is available")

    @classmethod
    def _body_source(
        cls, generation_input: ScriptGeneratorInput, clip: ClipCandidate | None
    ) -> str:
        analysis = generation_input.analysis
        summary = cls._normalize(analysis.summary)
        if not cls._is_meaningful(summary):
            summary = cls._normalize(generation_input.transcript)
        topic = cls._topic_name(analysis.topics, analysis.keywords)
        body = TONE_BODY_TEMPLATES[generation_input.options.tone].format(
            summary=summary,
            topic=topic,
        )
        if clip is not None and cls._is_meaningful(clip.title):
            body = f"{body} The {cls._normalize(clip.title)} segment is the clearest example."
        elif analysis.keywords:
            keywords = ", ".join(analysis.keywords[:3])
            body = f"{body} Key ideas include {keywords}."
        return cls._normalize(body)

    @classmethod
    def _title(cls, generation_input: ScriptGeneratorInput) -> str:
        analysis = generation_input.analysis
        topic = cls._strongest_topic(analysis.topics)
        if topic is not None and cls._is_meaningful(topic.name):
            base = topic.name
        elif analysis.keywords and cls._is_meaningful(analysis.keywords[0]):
            base = analysis.keywords[0]
        elif cls._is_meaningful(analysis.summary):
            base = cls._truncate_words(analysis.summary, 6)
        else:
            base = cls._truncate_words(generation_input.transcript, 6)
        title = cls._truncate_characters(f"{cls._normalize(base).title()}: Key Takeaways")
        return title or "Shorts Script"

    @classmethod
    def _call_to_action(
        cls, generation_input: ScriptGeneratorInput, target_words: int
    ) -> str | None:
        if not generation_input.options.include_call_to_action:
            return None
        source = TONE_CALLS_TO_ACTION[generation_input.options.tone]
        budget = max(1, min(cls._word_count(source), target_words // 6))
        return cls._finish_sentence(cls._truncate_words(source, budget))

    @staticmethod
    def _target_word_budget(generation_input: ScriptGeneratorInput) -> int:
        minimum = 3 if generation_input.options.include_call_to_action else 2
        calculated = math.floor(generation_input.options.target_duration_seconds * WORDS_PER_SECOND)
        return max(minimum, calculated)

    @classmethod
    def _strongest_hook(cls, candidates: list[HookCandidate]) -> HookCandidate | None:
        usable = [candidate for candidate in candidates if cls._is_meaningful(candidate.text)]
        return cls._strongest_candidate(usable)

    @classmethod
    def _strongest_clip(cls, candidates: list[ClipCandidate]) -> ClipCandidate | None:
        usable = [candidate for candidate in candidates if cls._is_meaningful(candidate.title)]
        return cls._strongest_candidate(usable)

    @staticmethod
    def _strongest_candidate[CandidateT: (HookCandidate, ClipCandidate)](
        candidates: list[CandidateT],
    ) -> CandidateT | None:
        if not candidates:
            return None
        return max(
            enumerate(candidates),
            key=lambda item: (
                item[1].score if item[1].score is not None else -1.0,
                -item[0],
            ),
        )[1]

    @staticmethod
    def _strongest_topic(topics: list[TopicResult]) -> TopicResult | None:
        if not topics:
            return None
        return max(
            enumerate(topics),
            key=lambda item: (
                item[1].relevance if item[1].relevance is not None else -1.0,
                -item[0],
            ),
        )[1]

    @classmethod
    def _topic_name(cls, topics: list[TopicResult], keywords: list[str]) -> str:
        topic = cls._strongest_topic(topics)
        if topic is not None and cls._is_meaningful(topic.name):
            return cls._normalize(topic.name)
        if keywords and cls._is_meaningful(keywords[0]):
            return cls._normalize(keywords[0])
        return "the main idea"

    @classmethod
    def _section(
        cls,
        order: int,
        section_type: str,
        text: str,
        source_range: tuple[float, float] | None,
    ) -> ScriptSection:
        start, end = source_range if source_range is not None else (None, None)
        return ScriptSection(
            order=order,
            type=section_type,
            text=text,
            estimated_duration_seconds=cls._estimate_duration(text),
            source_start_time=start,
            source_end_time=end,
        )

    @staticmethod
    def _source_range(
        candidate: HookCandidate | ClipCandidate | None,
        duration: float | None,
    ) -> tuple[float, float] | None:
        if candidate is None:
            return None
        start = candidate.start_time
        end = candidate.end_time
        if duration is not None:
            start = min(start, duration)
            end = min(end, duration)
            if end <= start:
                start = 0.0
                end = duration
        return (start, end) if end > start else None

    @staticmethod
    def _fallback_range(
        duration: float | None, preferred_length: float
    ) -> tuple[float, float] | None:
        if duration is None:
            return None
        end = min(duration, max(preferred_length, 0.1))
        return (0.0, end) if end > 0 else None

    @staticmethod
    def _usable_video_duration(generation_input: ScriptGeneratorInput) -> float | None:
        metadata = generation_input.video_metadata
        if metadata is None or metadata.duration is None or metadata.duration <= 0:
            return None
        return metadata.duration

    @classmethod
    def _estimate_duration(cls, text: str) -> float:
        return round(cls._word_count(text) / WORDS_PER_SECOND, 2)

    @staticmethod
    def _word_count(text: str | None) -> int:
        return len(text.split()) if text else 0

    @classmethod
    def _truncate_characters(cls, text: str) -> str:
        normalized = cls._normalize(text)
        if len(normalized) <= MAX_TITLE_CHARACTERS:
            return normalized
        selected: list[str] = []
        length = 0
        for word in normalized.split():
            next_length = length + len(word) + bool(selected)
            if next_length > MAX_TITLE_CHARACTERS:
                break
            selected.append(word)
            length = next_length
        return " ".join(selected)

    @classmethod
    def _truncate_words(cls, text: str, maximum_words: int) -> str:
        return " ".join(cls._normalize(text).split()[:maximum_words])

    @staticmethod
    def _finish_sentence(text: str) -> str:
        normalized = " ".join(text.split())
        if not normalized:
            raise UnusableScriptInputError("Generated script section is empty")
        return normalized if normalized.endswith((".", "!", "?")) else f"{normalized}."

    @staticmethod
    def _normalize(text: str) -> str:
        return " ".join(text.split())

    @staticmethod
    def _is_meaningful(text: str) -> bool:
        return bool(MEANINGFUL_TEXT_PATTERN.search(text))
