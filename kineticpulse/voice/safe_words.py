"""Classify the subject's verbal response after a fall prompt.

Three possible verdicts:

* ``SAFE``     - subject explicitly indicated they are fine.
* ``DISTRESS`` - subject called for help or expressed pain / panic.
* ``UNKNOWN``  - no clear keyword detected (caller may treat this as
  silence after the verify timeout).

Only ``SAFE`` cancels an alert; ``DISTRESS`` and ``UNKNOWN`` both escalate.
That asymmetry decides how matching works here:

* **Distress** phrases match anywhere in the reply. A false positive costs an
  unnecessary "are you okay?" escalation; missing a real one leaves someone on
  the floor.
* **Safe** phrases match anywhere *only when they are more than one word*
  ("i am fine", "alles in ordnung"). A single-word acknowledgement ("ok",
  "okay") counts only when it is the entire reply, because inside a longer
  sentence it is filler, not an answer - "okay so what happened" used to
  cancel the alert of someone confused after a fall.

Inflections are enumerated in the word lists rather than stemmed: matching is
whole-word, so ``hurt`` does not cover ``hurts``. Both are in
:class:`~kineticpulse.config.VoiceConfig` defaults and in
``config.example.yaml``, and ``tests/test_safe_words.py`` asserts the shipped
lists satisfy the documented behaviour - a trimmed list is a silent safety
regression, which is exactly how the deployed config lost ``hurts`` and every
German word once before.
"""

from __future__ import annotations

import re
import unicodedata
from enum import Enum
from typing import Iterable, Optional, Tuple

# Whisper often emits curly apostrophes; German umlauts must survive the
# ASCII-only strip that used to live here, otherwise "Hilfe" is fine but
# "geht's" / "Ordnung" / "I'm fine" with a typographic quote all miss.
_APOS = str.maketrans({"‘": "'", "’": "'", "‛": "'", "ʼ": "'", "`": "'"})
_FOLD = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss"})


class VoiceVerdict(str, Enum):
    SAFE = "safe"
    DISTRESS = "distress"
    UNKNOWN = "unknown"


def _normalise(text: str) -> str:
    text = unicodedata.normalize("NFC", text).lower()
    text = text.translate(_APOS).translate(_FOLD)
    text = re.sub(r"[^a-z0-9' ]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _contains_any(
    haystack: str,
    phrases: Iterable[str],
    *,
    single_word_must_be_whole_reply: bool = False,
) -> Optional[str]:
    """Find the first matching phrase, or ``None``.

    ``single_word_must_be_whole_reply`` makes one-word phrases match only when
    they are the complete reply. Used for safe words: see the module docstring
    for why the two lists are matched differently.
    """
    for phrase in phrases:
        if not phrase:
            continue
        needle = _normalise(phrase)
        if not needle:
            continue
        if single_word_must_be_whole_reply and " " not in needle:
            if haystack == needle:
                return phrase
            continue
        pat = r"(^|\s)" + re.escape(needle) + r"(\s|$)"
        if re.search(pat, haystack):
            return phrase
    return None


def classify_response(
    text: Optional[str],
    safe_words: Iterable[str],
    distress_words: Iterable[str],
) -> Tuple[VoiceVerdict, Optional[str]]:
    """Return ``(verdict, matched_phrase_or_None)``.

    Distress words are checked first and take priority, so ``"I'm fine, but it
    hurts"`` is distress rather than safe - provided ``hurts`` is in the list
    (see the module docstring on inflections).

    Safe words only match as described in the module docstring: multi-word
    phrases anywhere, single words only as the whole reply.
    """
    if not text or not text.strip():
        return VoiceVerdict.UNKNOWN, None
    normalised = _normalise(text)
    distress = _contains_any(normalised, distress_words)
    if distress:
        return VoiceVerdict.DISTRESS, distress
    safe = _contains_any(
        normalised, safe_words, single_word_must_be_whole_reply=True
    )
    if safe:
        return VoiceVerdict.SAFE, safe
    return VoiceVerdict.UNKNOWN, None
