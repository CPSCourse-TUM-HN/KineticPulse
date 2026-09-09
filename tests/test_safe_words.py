"""Keyword classifier must accept English and German replies.

The speaker language is not known in advance, so a German ``Hilfe`` or
``mir geht's gut`` has to classify the same way as the English equivalents.

These tests run twice over: once against :class:`VoiceConfig` defaults and
once against the lists in ``config.example.yaml``, which is what a real
deployment actually starts from. Testing only the defaults is how the
deployed config silently lost ``hurts`` and every German word - the classifier
was fine, the lexicon it was given was not.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from kineticpulse.config import VoiceConfig
from kineticpulse.voice.safe_words import VoiceVerdict, classify_response
from kineticpulse.voice.stt import pick_stt_language

_CFG = VoiceConfig()

_REPO = Path(__file__).resolve().parent.parent


def _shipped_voice_lists():
    """The lexicon a real deployment gets from config.example.yaml."""
    raw = yaml.safe_load((_REPO / "config.example.yaml").read_text(encoding="utf-8"))
    voice = raw["voice"]
    return voice["safe_words"], voice["distress_words"]


#: Both lexicons every behavioural assertion has to hold for.
LEXICONS = [
    pytest.param((_CFG.safe_words, _CFG.distress_words), id="defaults"),
    pytest.param(_shipped_voice_lists(), id="config.example.yaml"),
]


def _classify(text: str):
    return classify_response(text, _CFG.safe_words, _CFG.distress_words)


def test_english_safe_and_distress_still_match():
    assert _classify("I am fine")[0] == VoiceVerdict.SAFE
    assert _classify("help")[0] == VoiceVerdict.DISTRESS


def test_curly_apostrophe_does_not_drop_im_fine():
    """Whisper often emits a typographic quote; that used to become 'i m fine'."""
    verdict, matched = _classify("I’m fine")
    assert verdict == VoiceVerdict.SAFE
    assert matched is not None


def test_german_safe_replies():
    assert _classify("Mir geht es gut")[0] == VoiceVerdict.SAFE
    assert _classify("Mir geht's gut")[0] == VoiceVerdict.SAFE
    assert _classify("Alles in Ordnung")[0] == VoiceVerdict.SAFE


def test_german_distress_replies():
    assert _classify("Hilfe")[0] == VoiceVerdict.DISTRESS
    assert _classify("Notfall")[0] == VoiceVerdict.DISTRESS
    assert _classify("Das tut weh")[0] == VoiceVerdict.DISTRESS
    assert _classify("Schmerzen")[0] == VoiceVerdict.DISTRESS


def test_umlaut_folding_matches_listed_ascii_phrase():
    """Whisper emits 'Fürchte'; the lexicon stores the folded ASCII form."""
    verdict, matched = classify_response(
        "Fürchte mich",
        safe_words=[],
        distress_words=["fuerchte mich"],
    )
    assert verdict == VoiceVerdict.DISTRESS
    assert matched == "fuerchte mich"


def test_distress_still_wins_over_safe_in_either_language():
    assert _classify("I'm fine, but it hurts")[0] == VoiceVerdict.DISTRESS
    assert _classify("Mir geht es gut, aber Hilfe")[0] == VoiceVerdict.DISTRESS


def test_pick_stt_language_clamps_to_en_or_de():
    """A short 'Hilfe' often scores as Dutch; we must still transcribe as German."""
    probs = [("nl", 0.55), ("de", 0.30), ("en", 0.10), ("af", 0.05)]
    assert pick_stt_language(probs, ["en", "de"]) == "de"
    assert pick_stt_language(probs, ["en"]) == "en"
    assert pick_stt_language(probs, []) is None


def test_pick_stt_language_prefers_english_when_it_wins():
    probs = [("en", 0.8), ("de", 0.15), ("fr", 0.05)]
    assert pick_stt_language(probs, ["en", "de"]) == "en"


# --------------------------------------------------------------------------- #
# The lexicon a deployment actually uses must satisfy the documented behaviour
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("lexicon", LEXICONS)
def test_distress_covers_the_inflections_people_actually_say(lexicon) -> None:
    """Matching is whole-word, so every inflection must be listed.

    ``hurt`` does not cover ``hurts``. The deployed config once listed only
    ``hurt``, which classified "I'm fine, but it hurts" as SAFE and cancelled
    the alert - the single worst failure this module can produce.
    """
    safe, distress = lexicon
    for reply in (
        "it hurts",
        "my leg hurts",
        "I'm fine, but it hurts",
        "i am hurt",
        "in pain",
        "help",
        "please help me",
    ):
        verdict, _ = classify_response(reply, safe, distress)
        assert verdict == VoiceVerdict.DISTRESS, reply


@pytest.mark.parametrize("lexicon", LEXICONS)
def test_german_replies_classify_in_the_shipped_lexicon_too(lexicon) -> None:
    safe, distress = lexicon
    for reply in ("Hilfe", "Notfall", "Das tut weh", "Schmerzen", "Aua"):
        assert classify_response(reply, safe, distress)[0] == VoiceVerdict.DISTRESS
    for reply in ("Mir geht es gut", "Alles in Ordnung", "Ich bin okay"):
        assert classify_response(reply, safe, distress)[0] == VoiceVerdict.SAFE


@pytest.mark.parametrize("lexicon", LEXICONS)
def test_full_affirmations_still_cancel_the_alert(lexicon) -> None:
    safe, distress = lexicon
    for reply in ("I am fine", "I'm fine", "all good", "okay i am fine"):
        assert classify_response(reply, safe, distress)[0] == VoiceVerdict.SAFE, reply


# --------------------------------------------------------------------------- #
# A one-word acknowledgement is an answer only when it IS the answer
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("lexicon", LEXICONS)
def test_bare_acknowledgement_alone_is_safe(lexicon) -> None:
    safe, distress = lexicon
    for reply in ("okay", "ok", "Okay.", "  OK  "):
        assert classify_response(reply, safe, distress)[0] == VoiceVerdict.SAFE, reply


@pytest.mark.parametrize("lexicon", LEXICONS)
def test_embedded_filler_does_not_cancel_the_alert(lexicon) -> None:
    """"okay so what happened" is someone confused after a fall, not someone
    reporting they are fine. It used to classify SAFE and cancel the alert."""
    safe, distress = lexicon
    for reply in (
        "okay so what happened",
        "yeah okay",
        "ok what",
        "okay okay what is going on",
    ):
        verdict, matched = classify_response(reply, safe, distress)
        assert verdict == VoiceVerdict.UNKNOWN, f"{reply!r} -> {verdict} ({matched})"


@pytest.mark.parametrize("lexicon", LEXICONS)
def test_multi_word_safe_phrases_still_match_inside_a_sentence(lexicon) -> None:
    """The whole-reply rule applies only to one-word phrases."""
    safe, distress = lexicon
    verdict, matched = classify_response(
        "no no i am fine really", safe, distress
    )
    assert verdict == VoiceVerdict.SAFE
    assert matched == "i am fine"


@pytest.mark.parametrize("lexicon", LEXICONS)
def test_distress_beats_a_bare_acknowledgement(lexicon) -> None:
    safe, distress = lexicon
    assert classify_response("ok help", safe, distress)[0] == VoiceVerdict.DISTRESS


def test_silence_and_off_script_replies_escalate() -> None:
    for reply in ("", "   ", "what time is it", "I am not fine"):
        assert _classify(reply)[0] == VoiceVerdict.UNKNOWN, reply
