"""§14 OPTIONAL text-to-speech — Sarvam Bulbul, off by default.

Scope is deliberately tiny: it speaks the CONFIRMATION STRING the bridge
already produced, and nothing else. It is never in the reasoning path, never
sees a transcript, and never decides anything — the same category as STT (§2,
DESIGN.md §7.5), and the reason a free cloud service is allowed here at all.

DEFAULT IS `none`, and that is a credit decision, not an aesthetic one: a demo
that speaks every confirmation spends a call on every command, including the
ones nobody is listening to. Turn it on with `--tts sarvam` when someone is
actually going to hear it.

FAIL SILENT, NOT FAIL LOUD. `speak()` returns None on a missing key, a
transport error, or a bad reply. A confirmation that does not get spoken is a
cosmetic loss; an exception here would take down a command that already
applied to the road.
"""

from __future__ import annotations

import os
import time

from backend.voice.stt import SARVAM_API_KEY_ENV, SARVAM_TIMEOUT_S

PROVIDER_NONE = "none"
PROVIDER_SARVAM = "sarvam"
TTS_PROVIDERS = (PROVIDER_NONE, PROVIDER_SARVAM)
DEFAULT_TTS_PROVIDER = PROVIDER_NONE

SARVAM_TTS_URL = "https://api.sarvam.ai/text-to-speech"
SARVAM_TTS_MODEL = "bulbul:v2"
SARVAM_TTS_SPEAKER = "anushka"
SARVAM_TTS_LANGUAGE = "en-IN"

#: Bulbul rejects long inputs, and a confirmation is one sentence. Truncating
#: rather than erroring keeps a slightly long echo audible.
MAX_TTS_CHARS = 500


class NullTTS:
    """The default. Speaks nothing, costs nothing, and is not an error."""

    provider = PROVIDER_NONE

    def available(self) -> bool:
        return False

    def status(self) -> dict:
        return {"provider": self.provider, "available": False, "calls": 0}

    def speak(self, text: str) -> None:
        return None


class SarvamTTS:
    """Sarvam Bulbul. Confirmation strings only — see the module docstring.

    THE KEY comes from `SARVAM_API_KEY` and is never returned, never logged and
    never put on a frame. A missing key makes the provider unavailable and
    `speak()` a no-op that touches no network — documented behaviour, not an
    error to surface.
    """

    provider = PROVIDER_SARVAM

    def __init__(self, *, api_key: str | None = None,
                 model: str = SARVAM_TTS_MODEL,
                 speaker: str = SARVAM_TTS_SPEAKER,
                 language: str = SARVAM_TTS_LANGUAGE,
                 timeout_s: float = SARVAM_TIMEOUT_S,
                 session=None) -> None:
        self._key = api_key if api_key is not None else os.environ.get(
            SARVAM_API_KEY_ENV) or None
        self.model = model
        self.speaker = speaker
        self.language = language
        self.timeout_s = float(timeout_s)
        self._session = session
        self.calls = 0                       # credit counter (§11c manual run)
        self.last_error: str | None = None

    def available(self) -> bool:
        return bool(self._key)

    def status(self) -> dict:
        return {"provider": self.provider, "available": self.available(),
                "key_env": SARVAM_API_KEY_ENV, "model": self.model,
                "speaker": self.speaker, "calls": self.calls,
                "last_error": self.last_error}

    def speak(self, text: str) -> dict | None:
        started = time.perf_counter()
        if not self.available():
            self.last_error = f"{SARVAM_API_KEY_ENV} is not set"
            return None
        if not isinstance(text, str) or not text.strip():
            return None
        self.calls += 1
        try:
            if self._session is None:
                import requests
                self._session = requests.Session()
            reply = self._session.post(
                SARVAM_TTS_URL,
                headers={"api-subscription-key": self._key,
                         "Content-Type": "application/json"},
                json={"text": text[:MAX_TTS_CHARS],
                      "target_language_code": self.language,
                      "speaker": self.speaker,
                      "model": self.model},
                timeout=self.timeout_s,
            )
            if reply.status_code != 200:
                # Status only — the body can echo request detail, and a key
                # must never reach a log through an error path.
                self.last_error = f"sarvam HTTP {reply.status_code}"
                return None
            audios = (reply.json() or {}).get("audios") or []
        except Exception as exc:
            self.last_error = type(exc).__name__
            return None
        if not audios or not isinstance(audios[0], str):
            self.last_error = "no audio in the reply"
            return None
        return {"audio_b64": audios[0], "mime": "audio/wav",
                "provider": self.provider,
                "latency_ms": int((time.perf_counter() - started) * 1000)}


def get_tts(provider: str = DEFAULT_TTS_PROVIDER, **kwargs):
    """Provider name -> a TTS object. Raises on an unknown name (a `--tts`
    typo is a startup mistake and must be loud)."""
    if provider not in TTS_PROVIDERS:
        raise ValueError(
            f"unknown TTS provider {provider!r}; choose one of {TTS_PROVIDERS}")
    if provider == PROVIDER_NONE:
        return NullTTS()
    return SarvamTTS(**kwargs)
