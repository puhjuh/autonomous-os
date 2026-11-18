"""Unit tests for the strict Deepgram interim wake-word matcher."""

import threading

from hal.drivers.voice._internal.speaker_decorate import (
    SpeakerDecorator,
    merge_stt_hypothesis,
    merge_wake_words,
)


def _decorator(words: list[str]) -> SpeakerDecorator:
    decorator = object.__new__(SpeakerDecorator)
    decorator._wake_words = words
    decorator._wake_words_lock = threading.Lock()
    return decorator


def test_wake_word_match_is_case_and_punctuation_insensitive():
    decorator = _decorator(["hello luna", "hey luna", "hi luna", "alo luna", "okay luna", "ok luna", "wake up luna"])

    assert decorator.starts_with_wake_word("Hey Luna, thời tiết hôm nay?")
    assert decorator.starts_with_wake_word("Hello Luna! kể chuyện đi")
    assert decorator.starts_with_wake_word("Alo Luna ơi, kể chuyện đi")
    assert decorator.starts_with_wake_word("wake up Luna xem giúp mình")


def test_wake_word_match_requires_a_prefix_and_word_boundary():
    decorator = _decorator(["hey luna", "wake up luna"])

    assert not decorator.starts_with_wake_word("Mình vừa gặp Luna ngoài đường")
    assert not decorator.starts_with_wake_word("lunar calendar")
    assert not decorator.starts_with_wake_word("luna, nghe mình nói này")
    assert decorator.starts_with_wake_word("wake up luna, nghe mình nói này")


def test_lampy_call_accepts_bare_name_without_matching_mid_sentence():
    from hal.app_state import _build_wake_words

    words = _build_wake_words("Lampy")
    assert "lampy" in words
    assert "luna" not in _build_wake_words("Luna")
    decorator = _decorator(words)
    assert decorator.starts_with_wake_word("Lampy, show me a video")
    assert decorator.starts_with_wake_word("Hey Lampy, show me a video")
    assert decorator.starts_with_wake_word("Lampy")
    assert not decorator.starts_with_wake_word("I saw Lampy at the shop")


def test_celery_call_is_exact_and_limited_to_lampy():
    from hal.app_state import _build_wake_words

    lampy = _decorator(_build_wake_words("Lampy"))
    other = _decorator(_build_wake_words("Luna"))
    for utterance in ("Load up celery man.", "Load up celery, ma’am.", "load up celery ma'am"):
        assert lampy.classify_wake_word(utterance) == (utterance, "voice_command")
        assert not other.starts_with_wake_word(utterance)
    for utterance in (
        "Do not load up celery man",
        "Load up celery man tomorrow",
        "I said load up celery man",
        "We talked about it. Load up celery man.",
        "What is a celery man?",
    ):
        assert not lampy.starts_with_wake_word(utterance)


def test_wake_word_command_keeps_the_original_transcript():
    decorator = _decorator(["hey lamp"])

    assert decorator.classify_wake_word("Hey lamp, how are you?") == (
        "Hey lamp, how are you?",
        "voice_command",
    )
    assert decorator.classify_wake_word("I said hey lamp earlier") == (
        "I said hey lamp earlier",
        "voice",
    )


def test_partial_hypothesis_reassembles_cumulative_and_delta_updates():
    decorator = _decorator(["hello luna"])

    cumulative = merge_stt_hypothesis("hello", "hello luna")
    delta = merge_stt_hypothesis("hello", "luna")
    overlapping = merge_stt_hypothesis("hello luna", "luna what time is it")

    assert decorator.starts_with_wake_word(cumulative)
    assert decorator.starts_with_wake_word(delta)
    assert overlapping == "hello luna what time is it"


def test_device_type_alias_is_retained_alongside_agent_name():
    words = merge_wake_words(
        ["hello autonomous", "hey autonomous", "hi lamp", "wake up lamp"],
        ["hey luna", "wake up luna"],
    )

    assert words == [
        "hello autonomous",
        "hey autonomous",
        "hi lamp",
        "wake up lamp",
        "hey luna",
        "wake up luna",
    ]


def test_wake_word_matches_a_later_sentence():
    """A mic session is one stretch of speech, not one sentence.

    Device-observed 18/08/2026: the whole turn was dropped because the wake
    phrase opened the SECOND sentence.
    """
    decorator = _decorator(["hi lamp", "hey lamp"])

    assert decorator.starts_with_wake_word(
        "What was the score of the Vietnam versus Malaysia match? Hi lamp, can you hear me?"
    )
    assert decorator.starts_with_wake_word("Okay. Hey lamp, turn the light off.")


def test_wake_word_matches_a_trailing_vocative():
    decorator = _decorator(["hi lamp", "hey lamp"])

    assert decorator.starts_with_wake_word("What time is it, hey lamp?")
    assert decorator.starts_with_wake_word("Turn the light off. Hi lamp")


def test_wake_word_still_rejects_mid_sentence_mentions():
    decorator = _decorator(["hi lamp", "hey lamp"])

    assert not decorator.starts_with_wake_word("I said hey lamp earlier and nothing happened")
    assert not decorator.starts_with_wake_word("this hi lamp thing is broken")
    assert not decorator.starts_with_wake_word("Nice weather. I bought a lamp yesterday.")
