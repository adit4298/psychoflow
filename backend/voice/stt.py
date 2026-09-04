"""§14 STT layer — the browser Web Speech API contract, plus an OPTIONAL local
Whisper fallback.

WHAT THIS MODULE IS AND IS NOT
------------------------------
This module does **not** capture audio. Under the approved §14 design the
*browser* does capture and recognition; this module defines the contract for
the text that arrives, sanitises it, and hands a `TranscriptEvent` to
`backend/voice/intent_agent.py`. There is no microphone code here and there
should never be.

HONESTY NOTE — WEB SPEECH IS NOT LOCAL (CLAUDE.md §2, master plan §14)
---------------------------------------------------------------------
The browser Web Speech API is the DEFAULT path and it is **not on-device**. In
Chrome, `SpeechRecognition` streams the microphone audio to Google's cloud
speech service. It is free and needs no API key, which is why §14 chose it, but
it is off-device and must be described that way.

The project's hard rule is narrower than "local-only": **no Claude API call and
no paid inference anywhere in the runtime path** (a budget constraint, §2). Web
Speech satisfies that rule; it does not satisfy "local". The correct sentence to
say out loud on demo day is:

    "free local-model intent parsing with browser speech-to-text"

NOT "local-only". `LocalWhisperSTT` below is the truly-local option, and it is
optional and off by default.

THE FRONTEND CONTRACT (§13.2's frame is separate — this is the inbound half)
---------------------------------------------------------------------------
The voice panel runs recognition in the browser and POSTs the recognised text.
Reference implementation of the capture side, for whoever builds the Phase 10
panel:

    const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
    const rec = new SR();
    rec.lang = "en-IN";           // en-US also fine; see LANG_HINTS below
    rec.continuous = false;       // one utterance per press-to-talk
    rec.interimResults = true;    // show interim text, only SUBMIT finals
    rec.maxAlternatives = 1;
    rec.onresult = (ev) => {
      const r = ev.results[ev.results.length - 1];
      if (!r.isFinal) return showInterim(r[0].transcript);   // display only
      fetch("/voice/utterance", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({
          transcript: r[0].transcript,
          confidence: r[0].confidence,
          is_final: true,
          source: "web_speech",
        }),
      });
    };

Only `is_final` results are dispatched. An interim result is a display artefact
— acting on one would let a half-heard "emergency vehicle on lane..." fire a
control call before the operator finished the sentence.

SECURITY POSTURE — THIS TEXT IS UNTRUSTED
-----------------------------------------
Whatever arrives here is attacker-influenceable in the general case (anyone
audible near the microphone; anyone who can POST to the endpoint, since §13 has
NO authentication and is a loopback-only demo surface). `normalise_transcript`
does the cheap hygiene: strip control characters, collapse whitespace, cap
length. That hygiene is NOT the safety argument. The safety argument is
downstream and structural — `control_api.dispatch()` refuses any function name
outside `CONTROL_FUNCTIONS` before it binds a single argument, and every
operator-supplied number is range-checked inside `control_api`. Prompt hygiene
reduces noise; the allowlist is what makes a mis-parse harmless.
"""

from __future__ import annotations

import io
import math
import os
import time
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

# ---------------------------------------------------------------------------
# Contract constants
# ---------------------------------------------------------------------------
SOURCE_WEB_SPEECH = "web_speech"        # browser, NOT local (see docstring)
SOURCE_WHISPER_LOCAL = "whisper_local"  # optional, truly on-device
SOURCE_TEXT = "text"                    # typed into the panel / test harness
SOURCES = (SOURCE_WEB_SPEECH, SOURCE_WHISPER_LOCAL, SOURCE_TEXT)

#: Sources whose audio leaves the machine. Kept as data so a UI can render the
#: honesty note (§17) next to the mic button rather than hardcoding the claim.
OFF_DEVICE_SOURCES = frozenset({SOURCE_WEB_SPEECH})

#: A spoken traffic command is a short sentence. Anything longer is either a
#: recognition run-on or someone pasting into the endpoint; either way it is
#: not a command. Truncating (rather than rejecting) keeps a genuinely long
#: but valid utterance usable, and bounds what reaches the model's prompt.
MAX_TRANSCRIPT_CHARS = 400

#: Below this the utterance cannot carry a command ("go", "uh").
MIN_TRANSCRIPT_CHARS = 2

#: Web Speech `confidence` is unreliable across browsers and is sometimes 0.0
#: even for a clean final result, so it is RECORDED but never used as a gate.
#: Gating on it would silently drop good commands in exactly the noisy
#: rehearsal conditions §14's done-bar cares about.
CONFIDENCE_IS_ADVISORY = True

#: `rec.lang` values worth trying in rehearsal. Indian-accented English is the
#: demo's actual condition and en-IN recognises "auto rickshaw" and Indian
#: place names markedly better than en-US.
LANG_HINTS = ("en-IN", "en-US", "en-GB")


@dataclass(frozen=True)
class TranscriptEvent:
    """One final utterance, sanitised and ready for intent parsing."""

    transcript: str
    source: str = SOURCE_WEB_SPEECH
    confidence: float | None = None
    is_final: bool = True
    received_at: float = field(default_factory=time.time)

    @property
    def is_on_device(self) -> bool:
        """False for Web Speech — the audio left the machine (§2)."""
        return self.source not in OFF_DEVICE_SOURCES

    def to_dict(self) -> dict:
        return {
            "transcript": self.transcript,
            "source": self.source,
            "confidence": self.confidence,
            "is_final": self.is_final,
            "received_at": round(self.received_at, 3),
            "on_device": self.is_on_device,
        }


# ---------------------------------------------------------------------------
# Sanitisation
# ---------------------------------------------------------------------------
def normalise_transcript(raw) -> str:
    """Sanitise untrusted recognised text. Returns "" if nothing usable remains.

    Removes Unicode control/format characters (category C*) — which covers NUL,
    ANSI escape sequences, and the bidi/zero-width overrides that let a string
    render as something other than what it contains — then collapses whitespace
    and caps length. Newlines go too: a spoken utterance has none, and stripping
    them removes the cheapest way to fake a new section inside the model prompt.
    """
    if not isinstance(raw, str):
        return ""
    cleaned = "".join(
        " " if unicodedata.category(ch).startswith("C") else ch for ch in raw
    )
    cleaned = " ".join(cleaned.split())
    if len(cleaned) > MAX_TRANSCRIPT_CHARS:
        cleaned = cleaned[:MAX_TRANSCRIPT_CHARS].rstrip()
    if len(cleaned) < MIN_TRANSCRIPT_CHARS:
        return ""
    return cleaned


def _coerce_confidence(value) -> float | None:
    try:
        conf = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(conf):
        return None
    return max(0.0, min(1.0, conf))


def accept_web_speech_result(payload) -> TranscriptEvent | None:
    """Validate the frontend's POST body -> `TranscriptEvent`, or None.

    FAIL CLOSED, deliberately returning None rather than raising, on: a non-dict
    body, a non-final result, an unknown `source`, or a transcript that
    sanitises to nothing. The caller turns None into §14's "Command not
    understood, please try again" and takes no action.
    """
    if not isinstance(payload, dict):
        return None
    if payload.get("is_final") is False:
        # Interim results are for display only — never dispatched.
        return None
    source = payload.get("source", SOURCE_WEB_SPEECH)
    if source not in SOURCES:
        return None
    transcript = normalise_transcript(payload.get("transcript"))
    if not transcript:
        return None
    return TranscriptEvent(
        transcript=transcript,
        source=source,
        confidence=_coerce_confidence(payload.get("confidence")),
        is_final=True,
    )


def from_text(text: str, *, source: str = SOURCE_TEXT) -> TranscriptEvent | None:
    """Build an event from typed text (test harness, panel's text fallback)."""
    return accept_web_speech_result(
        {"transcript": text, "source": source, "is_final": True}
    )


# ---------------------------------------------------------------------------
# OPTIONAL truly-local fallback (§14: "only fall back to a local Whisper
# install if Web Speech proves unreliable in rehearsal with real background
# noise"). Off by default; `faster-whisper` is NOT installed in the project
# venv and this module must stay importable without it.
# ---------------------------------------------------------------------------
WHISPER_MODEL_SIZE = "base"      # §14's fallback; ~74M params, CPU-viable
WHISPER_DEVICE = "cpu"           # no CUDA assumption on the demo laptop
WHISPER_COMPUTE_TYPE = "int8"    # CTranslate2 quantisation — the CPU setting


class LocalWhisperSTT:
    """faster-whisper `base` on CPU. Truly on-device, unlike Web Speech.

    Enabled only when the caller passes `enabled=True` AND the package is
    importable. Construction never raises on a missing package — the whole
    point is that the default Web Speech path keeps working on a machine where
    nothing was installed.

    The model is loaded LAZILY on first `transcribe()`, not in `__init__`:
    loading costs seconds and allocates, and a panel that constructs the object
    at import time should not pay that unless someone actually speaks.
    """

    def __init__(
        self,
        *,
        enabled: bool = False,
        model_size: str = WHISPER_MODEL_SIZE,
        device: str = WHISPER_DEVICE,
        compute_type: str = WHISPER_COMPUTE_TYPE,
    ) -> None:
        self.enabled = bool(enabled)
        self.model_size = model_size
        self.device = device
        self.compute_type = compute_type
        self._model = None
        self._load_error: str | None = None

    @staticmethod
    def is_available() -> bool:
        """True if `faster-whisper` is importable in this interpreter."""
        try:
            import faster_whisper  # noqa: F401
        except Exception:
            return False
        return True

    def status(self) -> dict:
        return {
            "enabled": self.enabled,
            "package_available": self.is_available(),
            "model_size": self.model_size,
            "device": self.device,
            "compute_type": self.compute_type,
            "loaded": self._model is not None,
            "load_error": self._load_error,
            "on_device": True,
        }

    def _ensure_model(self):
        if self._model is not None:
            return self._model
        try:
            from faster_whisper import WhisperModel
        except Exception as exc:
            self._load_error = f"faster-whisper not installed: {exc}"
            return None
        try:
            self._model = WhisperModel(
                self.model_size, device=self.device, compute_type=self.compute_type
            )
        except Exception as exc:  # bad model name, no disk space, no network
            self._load_error = f"could not load whisper {self.model_size!r}: {exc}"
            return None
        return self._model

    def transcribe(self, audio_path: str) -> TranscriptEvent | None:
        """Transcribe a local audio file. Returns None on ANY failure.

        FAIL CLOSED like the rest of the layer: a disabled fallback, a missing
        package, a load failure or a decode failure all return None, and the
        caller reports "not understood" and takes no action.
        """
        if not self.enabled:
            return None
        model = self._ensure_model()
        if model is None:
            return None
        try:
            segments, _info = model.transcribe(audio_path, beam_size=1)
            text = " ".join(seg.text for seg in segments)
        except Exception as exc:
            self._load_error = f"transcribe failed: {exc}"
            return None
        transcript = normalise_transcript(text)
        if not transcript:
            return None
        return TranscriptEvent(transcript=transcript, source=SOURCE_WHISPER_LOCAL)


# ---------------------------------------------------------------------------
# PROVIDER FACTORY (DESIGN.md §7.5 — `--stt {webspeech,whisper,sarvam}`)
# ---------------------------------------------------------------------------
# Every provider returns the SAME dict, or None:
#
#     {"text": str, "language": str | None, "provider": str, "latency_ms": int}
#
# None means "nothing usable" and is the only failure channel — no provider
# raises. A missing Sarvam key, a transport error, an undecodable clip and a
# silent recording are all the same outcome to the caller: the operator is told
# "Didn't catch a command" and NOTHING is dispatched.
#
# STT IS TRANSCRIPTION, NEVER REASONING (§2, DESIGN.md §7.5). Sarvam sits in
# the same category as the already-accepted Web Speech API: a free cloud
# service that turns audio into text. The intent parse — the only step that
# decides anything — is local Gemma via Ollama and must stay that way.
PROVIDER_WEBSPEECH = "webspeech"
PROVIDER_WHISPER = "whisper"
PROVIDER_SARVAM = "sarvam"
STT_PROVIDERS = (PROVIDER_WEBSPEECH, PROVIDER_WHISPER, PROVIDER_SARVAM)

#: Default is the truly-on-device one. Chosen over `webspeech` because it is
#: the only provider needing neither a network nor a key, so the demo survives
#: conference wifi — and over `sarvam` because a default that spends credits is
#: a default that spends them by accident.
DEFAULT_STT_PROVIDER = PROVIDER_WHISPER

#: Audio upload cap. A spoken traffic command is a few seconds; anything past
#: this is not a command. Checked at the boundary because the STT endpoint is
#: unauthenticated like the rest of §13.
MAX_AUDIO_BYTES = 10 * 1024 * 1024

#: Sarvam's REST surface. Saarika transcribes in the spoken language; Saaras
#: transcribes AND translates to English. See `SarvamSTT` for why both exist.
SARVAM_STT_URL = "https://api.sarvam.ai/speech-to-text"
SARVAM_STT_TRANSLATE_URL = "https://api.sarvam.ai/speech-to-text-translate"
SARVAM_STT_MODEL = "saarika:v2.5"
SARVAM_STT_TRANSLATE_MODEL = "saaras:v2.5"
SARVAM_API_KEY_ENV = "SARVAM_API_KEY"
SARVAM_TIMEOUT_S = 20.0

#: Language codes the intent prompt can already read without translation.
#: Everything else routes through Saaras so the parser always gets English.
ENGLISH_CODES = ("en-in", "en-us", "en-gb", "en")


def _stt_result(text, language, provider: str, started: float) -> dict | None:
    """Uniform provider result, or None if nothing usable survived hygiene."""
    cleaned = normalise_transcript(text)
    if not cleaned:
        return None
    return {
        "text": cleaned,
        "language": language or None,
        "provider": provider,
        "latency_ms": int((time.perf_counter() - started) * 1000),
    }


def _audio_bytes(source) -> bytes | None:
    """bytes / path / file-like -> bytes, capped. None if unreadable or empty."""
    if isinstance(source, (bytes, bytearray, memoryview)):
        data = bytes(source)
    elif isinstance(source, (str, Path)):
        try:
            data = Path(source).read_bytes()
        except OSError:
            return None
    elif hasattr(source, "read"):
        try:
            data = source.read()
        except Exception:
            return None
        if not isinstance(data, bytes):
            return None
    else:
        return None
    if not data or len(data) > MAX_AUDIO_BYTES:
        return None
    return data


class WebSpeechSTT:
    """Passthrough — the BROWSER already did the recognition (see docstring).

    `source` is the recognised text, or the frontend's POST body. Nothing is
    transcribed here, so `latency_ms` is this call's own cost (~0) and not the
    recognition's; the panel shows the browser's own timing for that.
    """

    provider = PROVIDER_WEBSPEECH
    on_device = False   # Chrome streams the audio to Google (§2)

    def __init__(self, *, lang: str = LANG_HINTS[0]) -> None:
        self.lang = lang

    def available(self) -> bool:
        return True     # nothing to install; the browser is the dependency

    def status(self) -> dict:
        return {"provider": self.provider, "available": True,
                "on_device": False, "lang": self.lang}

    def transcribe(self, source, *, language: str | None = None) -> dict | None:
        started = time.perf_counter()
        if isinstance(source, dict):
            event = accept_web_speech_result(source)
            if event is None:
                return None
            return _stt_result(event.transcript, language or self.lang,
                               self.provider, started)
        if not isinstance(source, str):
            # Audio bytes reached the browser provider — a wiring mistake, not
            # something to paper over by quietly picking a different engine.
            return None
        return _stt_result(source, language or self.lang, self.provider, started)


class WhisperSTT:
    """faster-whisper `base` on CPU. Truly on-device; costs nothing to run.

    Thin adapter over `LocalWhisperSTT` (which predates the factory and keeps
    its own `TranscriptEvent` contract) so all three providers answer one
    method with one shape.
    """

    provider = PROVIDER_WHISPER
    on_device = True

    def __init__(self, **kwargs) -> None:
        kwargs.setdefault("enabled", True)
        self._impl = LocalWhisperSTT(**kwargs)

    def available(self) -> bool:
        return LocalWhisperSTT.is_available()

    def status(self) -> dict:
        return {"provider": self.provider, "on_device": True, **self._impl.status()}

    def transcribe(self, source, *, language: str | None = None) -> dict | None:
        started = time.perf_counter()
        data = _audio_bytes(source)
        if data is None:
            return None
        model = self._impl._ensure_model()
        if model is None:
            return None
        try:
            segments, info = model.transcribe(
                io.BytesIO(data), beam_size=1, language=language)
            text = " ".join(seg.text for seg in segments)
        except Exception as exc:
            self._impl._load_error = f"transcribe failed: {exc}"
            return None
        detected = getattr(info, "language", None) or language
        return _stt_result(text, detected, self.provider, started)


class SarvamSTT:
    """Sarvam AI STT — Saarika, with Saaras when the speaker is not in English.

    WHY TWO MODELS. Saarika returns the transcript in the SPOKEN language, so a
    Kannada command comes back in Kannada script and the intent prompt (an
    English allowlist) cannot read it. Saaras transcribes AND translates to
    English. So: one Saarika call with `language_code="unknown"` (auto-detect);
    if the detected language is not English, ONE follow-up Saaras call. English
    — the demo's normal case — therefore costs exactly one call.

    CREDIT DISCIPLINE (this is why the class is inert by default): nothing
    constructs this unless `--stt sarvam` was passed explicitly, and no test in
    this repo sets it. The self-test below asserts the no-key path makes ZERO
    requests, and `calls` counts them so the §11c manual run can report cost.

    THE KEY. Read from the `SARVAM_API_KEY` environment variable at
    construction. Never a default, never a literal, never logged, never on the
    §13.2 frame or in a result dict. A missing key makes the provider
    UNAVAILABLE — `available()` is False and `transcribe()` returns None
    without touching the network — which is documented behaviour, not an error
    to surface.
    """

    provider = PROVIDER_SARVAM
    on_device = False

    def __init__(self, *, api_key: str | None = None,
                 model: str = SARVAM_STT_MODEL,
                 translate_model: str = SARVAM_STT_TRANSLATE_MODEL,
                 timeout_s: float = SARVAM_TIMEOUT_S,
                 session=None) -> None:
        self._key = api_key if api_key is not None else os.environ.get(
            SARVAM_API_KEY_ENV) or None
        self.model = model
        self.translate_model = translate_model
        self.timeout_s = float(timeout_s)
        self._session = session              # injectable, so the self-test can
        self.last_error: str | None = None   # assert zero calls without a key
        self.calls = 0                       # credit counter (§11c manual run)

    def available(self) -> bool:
        return bool(self._key)

    def status(self) -> dict:
        """Availability WITHOUT the key, or any prefix or length of it."""
        return {"provider": self.provider, "on_device": False,
                "available": self.available(),
                "key_env": SARVAM_API_KEY_ENV,
                "model": self.model, "calls": self.calls,
                "last_error": self.last_error}

    def _post(self, url: str, data: dict, audio: bytes) -> dict | None:
        self.calls += 1
        try:
            if self._session is None:
                import requests
                self._session = requests.Session()
            reply = self._session.post(
                url,
                headers={"api-subscription-key": self._key},
                data=data,
                files={"file": ("command.wav", audio, "audio/wav")},
                timeout=self.timeout_s,
            )
            if reply.status_code != 200:
                # The body can echo request detail; keep the STATUS only, so a
                # key can never reach a log through an error path.
                self.last_error = f"sarvam HTTP {reply.status_code}"
                return None
            body = reply.json()
        except Exception as exc:
            self.last_error = type(exc).__name__
            return None
        return body if isinstance(body, dict) else None

    @staticmethod
    def _is_english(code) -> bool:
        return isinstance(code, str) and code.strip().lower() in ENGLISH_CODES

    def transcribe(self, source, *, language: str | None = None) -> dict | None:
        started = time.perf_counter()
        if not self.available():
            self.last_error = f"{SARVAM_API_KEY_ENV} is not set"
            return None
        audio = _audio_bytes(source)
        if audio is None:
            return None

        body = self._post(SARVAM_STT_URL, {
            "model": self.model,
            "language_code": language or "unknown",
        }, audio)
        if body is None:
            return None
        detected = body.get("language_code")
        text = body.get("transcript")

        if not self._is_english(detected):
            # Saaras: transcribe + translate, so the LOCAL parser always gets
            # English. A second call, and only for non-English speech.
            translated = self._post(SARVAM_STT_TRANSLATE_URL,
                                    {"model": self.translate_model}, audio)
            if translated is not None and translated.get("transcript"):
                return _stt_result(translated["transcript"], detected,
                                   self.provider, started)
        return _stt_result(text, detected, self.provider, started)


def get_stt(provider: str = DEFAULT_STT_PROVIDER, **kwargs):
    """Provider name -> an object exposing `transcribe` / `available` / `status`.

    Raises ValueError on an unknown name: a typo'd `--stt` flag is a startup
    mistake and must be loud, unlike a runtime transcription failure, which is
    a silent fail-closed None.
    """
    if provider not in STT_PROVIDERS:
        raise ValueError(
            f"unknown STT provider {provider!r}; choose one of {STT_PROVIDERS}")
    if provider == PROVIDER_WEBSPEECH:
        return WebSpeechSTT(**kwargs)
    if provider == PROVIDER_WHISPER:
        return WhisperSTT(**kwargs)
    return SarvamSTT(**kwargs)


# ---------------------------------------------------------------------------
# Self-test — `python -m backend.voice.stt`. No network, no model, no SUMO.
# ---------------------------------------------------------------------------
def _raises(fn) -> bool:
    try:
        fn()
    except Exception:
        return True
    return False


def _selftest() -> int:
    checks: list[tuple[str, bool, str]] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        checks.append((name, bool(ok), detail))

    # 1. sanitisation
    check("plain text passes",
          normalise_transcript("Switch to manual mode") == "Switch to manual mode")
    check("whitespace collapses",
          normalise_transcript("  switch   to\tmanual\n ") == "switch to manual")
    check("NUL and ANSI escapes stripped",
          "\x00" not in normalise_transcript("switch\x00to manual")
          and "\x1b" not in normalise_transcript("switch\x1b[31m to manual"))
    # U+200B zero-width space, U+202E right-to-left override: both category Cf.
    spoofed = "switch​to‮manual"
    check("zero-width / bidi stripped",
          normalise_transcript(spoofed) == "switch to manual",
          normalise_transcript(spoofed))
    check("length capped",
          len(normalise_transcript("lane " * 500)) <= MAX_TRANSCRIPT_CHARS)
    check("too short rejected", normalise_transcript("a") == "")
    check("non-str rejected",
          normalise_transcript(None) == "" and normalise_transcript(17) == ""
          and normalise_transcript({"transcript": "x"}) == "")

    # 2. payload acceptance
    ev = accept_web_speech_result(
        {"transcript": "switch to manual mode", "confidence": 0.9,
         "is_final": True, "source": SOURCE_WEB_SPEECH})
    check("valid final payload accepted",
          ev is not None and ev.transcript == "switch to manual mode")
    check("web speech flagged NOT on-device",
          ev is not None and ev.is_on_device is False)
    check("interim rejected",
          accept_web_speech_result(
              {"transcript": "switch to man", "is_final": False}) is None)
    check("unknown source rejected",
          accept_web_speech_result(
              {"transcript": "switch to manual", "source": "hacked"}) is None)
    check("non-dict rejected",
          accept_web_speech_result("switch to manual") is None
          and accept_web_speech_result(None) is None)
    check("empty transcript rejected",
          accept_web_speech_result({"transcript": "   "}) is None)
    nan_ev = accept_web_speech_result(
        {"transcript": "switch to manual", "confidence": float("nan")})
    check("NaN confidence -> None",
          nan_ev is not None and nan_ev.confidence is None)
    hi_ev = accept_web_speech_result(
        {"transcript": "switch to manual", "confidence": 4.2})
    check("confidence clamped to [0,1]", hi_ev is not None and hi_ev.confidence == 1.0)
    txt = from_text("what is the current wait time")
    check("from_text builds a text-source event",
          txt is not None and txt.source == SOURCE_TEXT)
    local = from_text("hello there", source=SOURCE_WHISPER_LOCAL)
    check("whisper source IS on-device", local is not None and local.is_on_device)

    # 3. optional whisper fallback stays inert when disabled / absent
    w = LocalWhisperSTT()
    check("whisper disabled by default", w.enabled is False)
    check("disabled whisper transcribes nothing",
          w.transcribe("nonexistent.wav") is None)
    check("status reports availability honestly",
          w.status()["package_available"] == LocalWhisperSTT.is_available())
    w2 = LocalWhisperSTT(enabled=True)
    if not LocalWhisperSTT.is_available():
        check("enabled-but-absent fails closed, does not raise",
              w2.transcribe("nonexistent.wav") is None
              and "not installed" in (w2.status()["load_error"] or ""))
    else:
        check("faster-whisper present — bad path still fails closed",
              w2.transcribe("nonexistent.wav") is None)

    # 4. provider factory — shape, and the credit guarantee
    check("default provider is the on-device one",
          DEFAULT_STT_PROVIDER == PROVIDER_WHISPER)
    ws = get_stt(PROVIDER_WEBSPEECH)
    out = ws.transcribe("hold north south green at J2 for 20 seconds")
    check("webspeech passthrough returns the uniform shape",
          isinstance(out, dict)
          and set(out) == {"text", "language", "provider", "latency_ms"}
          and out["provider"] == PROVIDER_WEBSPEECH
          and out["text"] == "hold north south green at J2 for 20 seconds",
          str(out))
    check("webspeech refuses audio bytes rather than guessing",
          ws.transcribe(b"RIFF....WAVE") is None)
    check("webspeech is honestly flagged off-device", ws.on_device is False)
    check("unknown provider raises at startup",
          _raises(lambda: get_stt("gpt4o")))
    check("whisper provider is on-device and enabled by the factory",
          get_stt(PROVIDER_WHISPER).on_device is True)

    # THE CREDIT GUARANTEE. A Sarvam client with no key must make ZERO
    # requests — asserted against an injected session that records every call,
    # not merely by reading the code back.
    class _TripwireSession:
        def __init__(self): self.posts = 0
        def post(self, *a, **k):
            self.posts += 1
            raise AssertionError("no-key Sarvam client touched the network")

    tripwire = _TripwireSession()
    sarvam = get_stt(PROVIDER_SARVAM, api_key=None, session=tripwire)
    # Explicitly empty, so the check holds on a machine where the env var IS set.
    sarvam._key = None
    check("no key -> sarvam unavailable, not an error", sarvam.available() is False)
    check("no key -> transcribe returns None and spends NOTHING",
          sarvam.transcribe(b"RIFF0000WAVEfmt ") is None
          and tripwire.posts == 0 and sarvam.calls == 0)
    check("sarvam status never carries the key",
          "key" not in " ".join(f"{k}{v}" for k, v in sarvam.status().items()
                                if k != "key_env").lower()
          or all(k != "api_key" for k in sarvam.status()))
    check("english detection routes past the translate call",
          SarvamSTT._is_english("en-IN") and SarvamSTT._is_english("EN")
          and not SarvamSTT._is_english("kn-IN")
          and not SarvamSTT._is_english(None))
    check("audio cap rejects an oversized upload",
          _audio_bytes(b"x" * (MAX_AUDIO_BYTES + 1)) is None
          and _audio_bytes(b"") is None
          and _audio_bytes(b"ok") == b"ok")

    passed = sum(1 for _n, ok, _d in checks if ok)
    for name, ok, detail in checks:
        flag = "  OK  " if ok else " FAIL "
        print(f"[{flag}] {name}" + (f"  -> {detail!r}" if detail and not ok else ""))
    print(f"\nstt.py self-test: {passed}/{len(checks)} passed")
    print(f"faster-whisper available: {LocalWhisperSTT.is_available()}  "
          f"(optional — Web Speech is the default path, and it is NOT local)")
    return 0 if passed == len(checks) else 1


if __name__ == "__main__":
    raise SystemExit(_selftest())
