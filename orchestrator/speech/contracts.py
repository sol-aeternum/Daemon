"""Small provider contract; no model/runtime or deployment-location dependencies."""

from dataclasses import dataclass
from typing import Literal, Protocol
import re

AudioFormat = Literal["mp3", "opus", "wav"]
MAX_TEXT_CHARS = 3000
MAX_AUDIO_BYTES = 16_000_000
MAX_AUDIO_SECONDS = 300
DEFAULT_VOICE = "daemon-default"
# Old settings offered these names without a working voice catalogue. Migrate
# explicitly; do not claim the replacement reproduces a vendor voice identity.
LEGACY_VOICES = frozenset(
    "Xb7hH8MSUJpSbSDYk0k2 allay amy aria ashley char emma josh rachel sage "
    "sam james ari adam drew clyde diana ellen fiona george grace henry io "
    "jenny kevin lily marcus michelle patrick sarah steve tiffany tim will".split()
)


class SpeechError(Exception):
    """Safe, typed failure; raw runtime errors must not reach clients."""

    def __init__(self, code: str, status: int = 503):
        self.code = code
        self.status = status
        super().__init__(code)


@dataclass(frozen=True)
class SpeechRequest:
    text: str
    voice: str = DEFAULT_VOICE
    speed: float = 1.0
    format: AudioFormat = "mp3"

    def __post_init__(self) -> None:
        if not self.text.strip():
            raise SpeechError("text_required", 400)
        if len(self.text) > MAX_TEXT_CHARS:
            raise SpeechError("text_too_long", 413)
        if not 0.5 <= self.speed <= 2.0:
            raise SpeechError("invalid_speed", 422)
        if self.format not in ("mp3", "opus", "wav"):
            raise SpeechError("unsupported_format", 422)
        if self.voice != DEFAULT_VOICE:
            raise SpeechError("voice_unavailable", 422)
        # Reject pathological runs without modifying normal content.
        words = self.text.lower().split()
        if re.search(r"(.)\1{127}", self.text) or (len(words) >= 80 and len(set(words)) <= 2):
            raise SpeechError("repetitive_text", 422)
        if any(ord(c) < 32 and c not in "\n\r\t" for c in self.text):
            raise SpeechError("invalid_text", 422)


@dataclass(frozen=True)
class SpeechAudio:
    content: bytes
    duration: float
    sample_rate: int
    synthesis_seconds: float


@dataclass(frozen=True)
class SpeechCapabilities:
    voices: tuple[str, ...] = (DEFAULT_VOICE,)
    formats: tuple[AudioFormat, ...] = ("mp3", "opus", "wav")
    streaming: bool = False
    speaking_rate: bool = True
    max_characters: int = MAX_TEXT_CHARS


class SpeechProvider(Protocol):
    name: str
    model: str

    def capabilities(self) -> SpeechCapabilities: ...

    async def health(self) -> bool: ...

    async def synthesize(self, request: SpeechRequest) -> SpeechAudio: ...


def canonical_voice(value: str | None) -> str:
    if value is None or value == DEFAULT_VOICE or value in LEGACY_VOICES:
        return DEFAULT_VOICE
    raise SpeechError("voice_unavailable", 422)
