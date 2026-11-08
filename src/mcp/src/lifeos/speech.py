"""Atlas's voice (phase 5, talk mode level L2): a short spoken line of one of the person's own answers, read by
OpenAI's speech model through a provider seam, metered like voice notes.

What is read: only the answer's own words (`spoken_line`): the plain text of its first part (no tables, no code, no
links' addresses, no fenced outside text), at most SPOKEN_MAX characters cut at a sentence end, and "The rest is on
screen." when something was left out. The words are read, never followed: the speech model takes them as `input`
next to fixed INSTRUCTIONS that only say how to sound (outside text quoted inside an answer is read as words, which is
fine). The app speaks the same rule with the device's own voice at $0 (web screens/chat/talk/spoken.ts).

The seam: SpeechOut (one call, `speak(text)` → Spoken: the audio and what OpenAI says it billed). OpenAISpeech posts
to /v1/audio/speech with stream_format "sse" so the answer ends with its usage (speech.audio.done); FakeSpeech is for
tests and the dev preview. OpenAI shuts every text-to-speech model down on 2027-01-06 (llm.TTS_MODEL): a replacement
is one more SpeechOut.

Gradium (Ishan, 2026-11-07: "Let's use Gradium first. It will save some credit on OpenAI."): GradiumSpeech reads the
same line through Gradium's REST text-to-speech, on credits Ishan redeemed (1 credit a character; booked at $0, the
true cost, with the characters as its input count). `gradium` picks it only while LIFEOS_GRADIUM lets the person have
it, a voice is set, the person is under a fair share of characters in the last 24 hours and the balance (read at most
every BALANCE_TTL_S) is above a floor; otherwise, and once if Gradium fails, OpenAI reads it exactly as before. Only
the line goes out (Shield-checked like every read); the key is sent as Gradium's header and never written anywhere.

Money (lifeos.api.talk): budget.reserve_talk holds `estimate_usd(line)` first and commits it (the person's minutes of
Atlas's voice a day, then the month, day and pool caps); the call is made outside any transaction (no database
connection waits on OpenAI); then `book` writes its llm_calls row (job 'voice_out', from OpenAI's usage, never under
the estimate when the usage is missing) and the hold is settled in one short transaction (a failed call: `failed`'s
row and the release; a 200 with nothing to play raises Unusable and is booked as paid, Y5)."""

from __future__ import annotations

import base64
import json
import re
import struct
import threading
import time
import uuid
from dataclasses import dataclass
from decimal import ROUND_CEILING, Decimal
from typing import Protocol

import httpx
from psycopg import Connection

from lifeos import chatmodes, config, llm

SPOKEN_MAX = 400  # characters read aloud at most: about half a minute
READ_MAX = 2000  # Read aloud on an answer (whole=True): the whole answer's words, at most this many
CHARS_PER_S = 15  # speech at about 150 words a minute
ESTIMATE_SLACK = Decimal("1.5")  # the hold covers a slower voice than CHARS_PER_S
AUDIO_MAX_BYTES = (
    4 * 1024 * 1024
)  # a 400-character line is about 0.5 MB of MP3; anything far past it is refused
REST = "The rest is on screen."
ONLY_SCREEN = "It's on screen."  # an answer that is all table or code
VOICE_SETTING = "LIFEOS_TTS_VOICE"
DEFAULT_VOICE = "marin"  # OpenAI's guide: "we recommend using marin or cedar"
VOICES = frozenset({"alloy", "ash", "ballad", "coral", "echo", "fable", "nova", "onyx", "sage", "shimmer", "verse",
                    "marin", "cedar"})  # fmt: skip
# how to sound, never what to do: the line itself is only ever `input`
INSTRUCTIONS = "Read the text aloud exactly as written, in a calm, warm and natural voice at an easy pace."

_FENCE = re.compile(r"```.*?(```|\Z)", re.DOTALL)
_UNTRUSTED = re.compile(r"<(untrusted[a-z_]*)\b[^>]*>.*?(</\1\s*>|\Z)", re.DOTALL | re.IGNORECASE)
_TAG = re.compile(r"</?[A-Za-z][^>]{0,200}>")
_IMAGE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_URL = re.compile(r"\bhttps?://\S+|\bwww\.\S+", re.IGNORECASE)
_EMPHASIS = re.compile(r"(\*\*|__|~~|\*|_|`)(?=\S)(.+?)(?<=\S)\1")
_BULLET = re.compile(r"^\s*(?:[-*+]|\d{1,3}[.)])\s+")
_SENTENCE_END = re.compile(r"[.!?…](?=\s|$)")


def _plain(line: str) -> str:
    """One line of Markdown as words: links keep their words, addresses and pictures go, emphasis marks go."""
    out = _IMAGE.sub("", line)
    out = _LINK.sub(r"\1", out)
    out = _URL.sub("", out)
    out = _TAG.sub("", out)
    for _ in range(3):  # nested emphasis (**_x_**)
        out = _EMPHASIS.sub(r"\2", out)
    out = _BULLET.sub("", out.lstrip("> ")).replace("`", "")
    return " ".join(out.split())


def _visual(line: str) -> str | None:
    """What a non-empty line is when it isn't words to read: "table", "heading" or "rule"; None for words."""
    if line.startswith("|"):
        return "table"
    if line.startswith("#"):
        return "heading"
    if set(line) <= set("-=*_ ") and len(line.replace(" ", "")) >= 3:
        return "rule"
    return None


def spoken_line(text: str, *, whole: bool = False) -> str:
    """The words read aloud for an answer: the plain text of its first part (paragraphs and list items until the first
    table, heading or rule after some words; a heading before any words is skipped), at most SPOKEN_MAX characters cut
    at a sentence end, then REST when anything was left out (a table, code, more paragraphs). Empty when the answer has
    no words to read. Fenced outside text (<untrusted_…>) is never read. `whole` (Read aloud on an answer): every
    paragraph and list item, what is for the screen skipped (a table, heading, rule, code), at most READ_MAX."""
    body = _UNTRUSTED.sub("\n\n", str(text or ""))
    left_out = bool(_FENCE.search(body))
    body = _FENCE.sub("\n\n", body)
    parts: list[str] = []
    for raw in body.splitlines():
        line = raw.strip()
        if not line:
            continue
        kind = _visual(line)
        if kind is not None:
            if parts and not whole:
                left_out = True  # the first part ends here; the rest is for the screen
                break
            left_out = left_out or kind == "table"
            continue
        words = _plain(line)
        if words:
            parts.append(words if words[-1] in ".!?…:;," else f"{words}.")
    said = " ".join(parts)
    if not said:
        return ONLY_SCREEN if left_out else ""
    most = READ_MAX if whole else SPOKEN_MAX
    if len(said) > most:
        left_out = True
        cut = said[:most]
        ends = [m.end() for m in _SENTENCE_END.finditer(cut)]
        if ends and ends[-1] >= most // 3:
            said = cut[: ends[-1]]
        else:
            said = cut.rsplit(" ", 1)[0].rstrip(",;:") + "…"
    if whole and left_out and len(said) <= most:
        # the whole answer read: what was skipped (a table, code) is on the screen, but nothing was cut short
        return f"{said} {REST}" if len(" ".join(parts)) > most else said
    return f"{said} {REST}" if left_out else said


def shielded(conn: Connection, user: str, line: str) -> bool:
    """Atlas Shield (D125) is on for `user` and would hide something in `line` (or couldn't check it): then the line
    never goes to OpenAI's voice (the device's voice reads it instead). The answer the person sees is unmasked, so
    sending it whole would hand the AI provider exactly what Shield kept from the model."""
    from lifeos import shield, shieldprefs  # here: shieldprefs reads the person's row with this connection

    guard = shieldprefs.for_run(conn, user)
    if guard is None:
        return False
    vault = shield.Vault()
    return guard.mask_text(line, vault) == shield.FAILED_NOTE or bool(vault)


def estimate_usd(line: str) -> Decimal:
    """What reading `line` should cost at most (the hold): its length at CHARS_PER_S, with ESTIMATE_SLACK, at the
    price a minute; never under one second."""
    seconds = max(1.0, len(line) / CHARS_PER_S) * float(ESTIMATE_SLACK)
    usd = Decimal(str(seconds)) / Decimal(60) * llm.TTS_USD_PER_MIN
    return usd.quantize(Decimal("0.000001"), rounding=ROUND_CEILING)


def usage_usd(input_tokens: int | None, output_tokens: int | None) -> Decimal | None:
    """What OpenAI says a speech call cost (llm.tts_token_usd), or None without both counts."""
    if input_tokens is None or output_tokens is None:
        return None
    p_in, p_out = llm.tts_token_usd()
    usd = (input_tokens * p_in + output_tokens * p_out) / Decimal(1_000_000)
    return usd.quantize(Decimal("0.000001"), rounding=ROUND_CEILING)


@dataclass(frozen=True)
class Spoken:
    audio: bytes
    mime: str
    input_tokens: int | None = None  # what OpenAI says it billed; None when the answer didn't say
    output_tokens: int | None = None


class Unusable(llm.LLMError):
    """Y5: OpenAI answered 200 (it billed the read) but nothing can be played: no audio, audio that isn't base64, or
    more than AUDIO_MAX_BYTES. `spoken` carries the usage it saw (no audio), so the call is booked as paid (`book`:
    that usage, else the line's estimate), never a $0 failed row."""

    def __init__(self, message: str, spoken: Spoken) -> None:
        super().__init__(message)
        self.spoken = spoken


class SpeechOut(Protocol):
    """A voice for Atlas: `speak(text)` reads `text` (never follows it) and returns the audio and its usage. Raises
    llm.LLMError (the path and an HTTP status or a class only, never the provider's own words); Unusable when a 200
    has nothing to play."""

    model: str
    provider: str  # the llm_calls provider ("openai", "gradium")

    def speak(self, text: str) -> Spoken: ...

    def cost(self, spoken: Spoken, line: str) -> Decimal:
        """What the read cost (booked): the provider's own count when it gave one, never under the estimate if not."""
        ...


def _count(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 100_000_000:
        return None
    return value


class OpenAISpeech:
    """OpenAI's speech model (llm.TTS_MODEL) on /v1/audio/speech, streamed as server-sent events: speech.audio.delta
    carries base64 MP3, speech.audio.done the usage. `client`: a caller's own (tests), else one with this server's
    key."""

    def __init__(self, client: httpx.Client | None = None, *, voice: str | None = None) -> None:
        self._client = client
        self.voice = openai_name(voice)
        self.model = llm.tts_model()

    provider = "openai"

    def cost(self, spoken: Spoken, line: str) -> Decimal:
        usd = usage_usd(spoken.input_tokens, spoken.output_tokens)
        return usd if usd is not None else estimate_usd(line)

    def speak(self, text: str) -> Spoken:
        body = {"model": self.model, "input": text, "voice": self.voice, "instructions": INSTRUCTIONS,
                "response_format": "mp3", "stream_format": "sse"}  # fmt: skip
        http = self._client or llm.client()
        chunks: list[bytes] = []
        size = 0
        usage: dict[str, object] = {}

        def unusable(why: str) -> Unusable:
            seen = Spoken(
                b"", "audio/mpeg", _count(usage.get("input_tokens")), _count(usage.get("output_tokens"))
            )
            return Unusable(f"openai /audio/speech: {why}", seen)

        try:
            with http.stream("POST", "/audio/speech", json=body) as r:
                if r.status_code != 200:
                    raise llm.LLMError(f"openai /audio/speech: HTTP {r.status_code}")
                data: list[str] = []
                for line in r.iter_lines():
                    if line.startswith("data:"):
                        data.append(line[5:].strip())
                        continue
                    if line or not data:
                        continue
                    payload, data = "\n".join(data), []
                    try:
                        event = json.loads(payload)
                    except ValueError:
                        continue
                    if not isinstance(event, dict):
                        continue
                    if event.get("type") == "speech.audio.delta" and isinstance(event.get("audio"), str):
                        try:
                            piece = base64.b64decode(event["audio"], validate=False)
                        except ValueError:  # binascii.Error: not base64
                            raise unusable("bad_audio") from None
                        size += len(piece)
                        if size > AUDIO_MAX_BYTES:
                            raise unusable("too_long")
                        if piece:
                            chunks.append(piece)
                    elif event.get("type") == "speech.audio.done" and isinstance(event.get("usage"), dict):
                        usage = event["usage"]
        except httpx.HTTPError as e:
            raise llm.LLMError(f"openai /audio/speech: {type(e).__name__}") from None
        finally:
            if self._client is None:
                http.close()
        if not chunks:
            raise unusable("no_audio")
        return Spoken(
            b"".join(chunks),
            "audio/mpeg",
            _count(usage.get("input_tokens")),
            _count(usage.get("output_tokens")),
        )


class FakeSpeech:
    """Tests and the dev preview: a few bytes of "audio" and the usage the real model would report for `text` (its
    characters / 4 in, 20 audio tokens a second of speech at CHARS_PER_S out). `fail`: raise like a broken call;
    `unusable`: a billed 200 with nothing to play (Unusable)."""

    model = llm.TTS_MODEL
    provider = "openai"

    def __init__(self, *, fail: bool = False, usage: bool = True, unusable: bool = False) -> None:
        self.fail, self.usage, self.unusable = fail, usage, unusable
        self.said: list[str] = []

    def speak(self, text: str) -> Spoken:
        self.said.append(text)
        if self.fail:
            raise llm.LLMError("openai /audio/speech: HTTP 500")
        seconds = max(1.0, len(text) / CHARS_PER_S)
        audio = b"" if self.unusable else b"ID3fake-mp3"
        spoken = Spoken(audio, "audio/mpeg", -(-len(text) // 4), round(seconds * 20))
        if not self.usage:
            spoken = Spoken(audio, "audio/mpeg")
        if self.unusable:
            raise Unusable("openai /audio/speech: no_audio", spoken)
        return spoken

    def cost(self, spoken: Spoken, line: str) -> Decimal:
        usd = usage_usd(spoken.input_tokens, spoken.output_tokens)
        return usd if usd is not None else estimate_usd(line)


# ---------- Gradium ----------

GRADIUM_URL = "https://api.gradium.ai"
GRADIUM_TTS = "/api/post/speech/tts"  # docs.gradium.ai api-reference tts-post (2026-11-07)
GRADIUM_CREDITS = "/api/usages/credits"  # docs.gradium.ai api-reference get-credits
GRADIUM_KEY = "GRADIUM_API_KEY"
GRADIUM_VOICE_SETTING = "LIFEOS_GRADIUM_VOICE"  # a voice's uid (GET /api/voices/); empty = GRADIUM_VOICE
GRADIUM_VOICE = (
    "NbpkqMVS3CJeq2j8"  # Zoey: Ishan's pick by ear (2026-11-07, Atlas_Voice-Choices_v1), Settings' Natural
)
# credits kept back (below this, OpenAI reads) and a person's fair share of characters a day (~5 minutes)
GRADIUM_FLOOR_SETTING, GRADIUM_FLOOR = "LIFEOS_GRADIUM_FLOOR", 5_000
GRADIUM_DAY_SETTING, GRADIUM_DAY_CHARS = "LIFEOS_GRADIUM_DAY_CHARS", 3_750
# Hz: raw 16-bit mono PCM, wrapped in a WAV header here (Gradium has no MP3; WAV plays everywhere)
GRADIUM_RATE = 24_000
# the balance is read at most this often per process; each read line takes its characters off
BALANCE_TTL_S = 300
VOICE_UID = re.compile(r"^[A-Za-z0-9_-]{4,64}$")


def wav(pcm: bytes, rate: int = GRADIUM_RATE) -> bytes:
    """16-bit mono PCM as a WAV file (the 44-byte RIFF header, then the samples)."""
    pcm = pcm[: len(pcm) // 2 * 2]
    head = (
        b"RIFF"
        + struct.pack("<I", 36 + len(pcm))
        + b"WAVEfmt "
        + struct.pack("<IHHIIHH", 16, 1, 1, rate, rate * 2, 2, 16)
    )
    return head + b"data" + struct.pack("<I", len(pcm)) + pcm


def _gradium_client() -> httpx.Client:
    return httpx.Client(base_url=GRADIUM_URL, timeout=httpx.Timeout(30.0, connect=5.0))


class GradiumSpeech:
    """Gradium's text-to-speech (REST, one shot): the line in, raw PCM out (only_audio), wrapped as WAV. `client`: a
    caller's own (tests), else a fresh one. Errors carry the path and an HTTP status or a class only (Gradium's own
    words are never passed on); a 200 with nothing to play is Unusable."""

    provider = "gradium"
    model = "gradium"

    def __init__(self, client: httpx.Client | None = None, *, voice: str, key: str) -> None:
        self._client, self.voice, self._key = client, voice, key

    def speak(self, text: str) -> Spoken:
        body = {
            "text": text,
            "voice_id": self.voice,
            "output_format": f"pcm_{GRADIUM_RATE}",
            "only_audio": True,
        }
        http = self._client or _gradium_client()
        chunks: list[bytes] = []
        size = 0
        try:
            with http.stream("POST", GRADIUM_TTS, json=body, headers={"x-api-key": self._key}) as r:
                if r.status_code != 200:
                    raise llm.LLMError(f"gradium /tts: HTTP {r.status_code}")
                for piece in r.iter_bytes():
                    size += len(piece)
                    if size > AUDIO_MAX_BYTES:
                        raise Unusable("gradium /tts: too_long", Spoken(b"", "audio/wav", len(text)))
                    chunks.append(piece)
        except httpx.HTTPError as e:
            raise llm.LLMError(f"gradium /tts: {type(e).__name__}") from None
        finally:
            if self._client is None:
                http.close()
        pcm = b"".join(chunks)
        if len(pcm) < 2:
            raise Unusable("gradium /tts: no_audio", Spoken(b"", "audio/wav", len(text)))
        return Spoken(
            wav(pcm), "audio/wav", len(text)
        )  # its input count is the characters: the credits it took

    def cost(self, spoken: Spoken, line: str) -> Decimal:
        return Decimal(0)  # redeemed credits: nothing is paid (a bought plan's price becomes a setting then)


class _Balance:
    """Gradium's balance as this process last read it (None: unknown, so Gradium isn't used), at most every
    BALANCE_TTL_S, less the characters read since."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._at = float("-inf")
        self._left: int | None = None

    def left(self, key: str, client: httpx.Client | None = None) -> int | None:
        with self._lock:
            if time.monotonic() - self._at < BALANCE_TTL_S:
                return self._left
            self._at = time.monotonic()  # a failed read waits its turn too (never a read per line)
        http = client or _gradium_client()
        try:
            r = http.get(GRADIUM_CREDITS, headers={"x-api-key": key})
            got = r.json().get("remaining_credits") if r.status_code == 200 else None
            left = got if isinstance(got, int) and not isinstance(got, bool) and got >= 0 else None
        except (httpx.HTTPError, ValueError, AttributeError):
            left = None
        finally:
            if client is None:
                http.close()
        with self._lock:
            self._left = left
        return left

    def took(self, chars: int) -> None:
        with self._lock:
            if self._left is not None:
                self._left = max(0, self._left - chars)

    def forget(self) -> None:
        with self._lock:
            self._at, self._left = float("-inf"), None


BALANCE = _Balance()


def _setting_int(name: str, default: int) -> int:
    raw = config.get(name, "").strip()
    return int(raw) if raw.isdigit() else default


def gradium_today(conn: Connection, user: str) -> int:
    """Characters Gradium read for `user` in the last 24 hours (their llm_calls rows)."""
    row = conn.execute(
        "select coalesce(sum(input_tokens), 0) from public.llm_calls "
        "where owner_id = %s and job = 'voice_out' and provider = 'gradium' and ok and ts > now() - interval '24 hours'",
        (user,),
    ).fetchone()
    return int(row[0]) if row else 0


def gradium(
    conn: Connection, user: str, line: str, client: httpx.Client | None = None
) -> GradiumSpeech | None:
    """Gradium for this read, or None (OpenAI reads it): its flag lets `user` have it, a key is set and the voice uid
    (the setting's, else GRADIUM_VOICE) is good, `line` fits the person's fair share of the last 24 hours, and the
    balance is known and stays above the floor."""
    key = config.get(GRADIUM_KEY, "").strip()
    voice = config.get(GRADIUM_VOICE_SETTING, "").strip() or GRADIUM_VOICE
    if not chatmodes.flag_on("gradium", user) or not key or not VOICE_UID.match(voice):
        return None
    if gradium_today(conn, user) + len(line) > _setting_int(GRADIUM_DAY_SETTING, GRADIUM_DAY_CHARS):
        return None
    left = BALANCE.left(key, client)
    if left is None or left - len(line) < _setting_int(GRADIUM_FLOOR_SETTING, GRADIUM_FLOOR):
        return None
    return GradiumSpeech(client, voice=voice, key=key)


def provider(voice: str | None = None) -> SpeechOut:
    """OpenAI's voice lifeos-api uses (`voice`: one of VOICES, else the setting's; tests replace this)."""
    return OpenAISpeech(voice=voice)


def openai_name(voice: str | None) -> str:
    """The OpenAI voice that reads: `voice` (a pick's), else the setting's, else DEFAULT_VOICE; never one not in VOICES."""
    chosen = (voice or config.get(VOICE_SETTING, DEFAULT_VOICE)).strip().lower()
    return chosen if chosen in VOICES else DEFAULT_VOICE


# the line Settings › Personalize › Read aloud plays in each voice: the server's own words around the assistant's name
# (persona.assistant_name: the person's name for it, checked and cleaned, else Atlas), so the voice says who it is
SAMPLE = "Hi, it's {name}. This is how I'll sound when I read to you."


def sample_line(name: str) -> str:
    return SAMPLE.format(name=name)


# a person's pick (Settings › Personalize › Read aloud; profiles.prefs.voice, settings.PrefsIn): natural = Gradium while
# it may read for them (else OpenAI's default), clear and warm = OpenAI's marin and cedar
PICKS: dict[str, str | None] = {"natural": None, "clear": "marin", "warm": "cedar"}


def picked(conn: Connection, user: str) -> str:
    """The person's pick, or natural when they haven't made one (or it isn't one of PICKS)."""
    row = conn.execute("select prefs->>'voice' from public.profiles where owner_id = %s", (user,)).fetchone()
    pick = row[0] if row else None
    return pick if isinstance(pick, str) and pick in PICKS else "natural"


def book(
    conn: Connection, user: str, line: str, voice: SpeechOut, spoken: Spoken, latency_ms: int
) -> Decimal:
    """Book a read line (rule 4): one llm_calls row, job 'voice_out', at what the voice says it cost (OpenAI: what it
    billed, never under the estimate when it says nothing; Gradium: $0 on redeemed credits, its characters counted).
    Returns the dollars booked. The caller settles the hold in this transaction."""
    cost = voice.cost(spoken, line)
    llm.record(conn, user, job="voice_out", model=voice.model, agent_id="app", provider=voice.provider,
               fresh=spoken.input_tokens or 0, output=spoken.output_tokens or 0, cost_usd=cost, latency_ms=latency_ms,
               source_key=f"voice_out:{uuid.uuid4()}")  # fmt: skip
    if voice.provider == "gradium" and spoken.audio:
        BALANCE.took(spoken.input_tokens or len(line))
    return cost


def failed(conn: Connection, user: str, voice: SpeechOut, e: BaseException, latency_ms: int) -> None:
    """A failed read is a row too (ok=false, its class, no cost); written with the hold's release."""
    llm.record(conn, user, job="voice_out", model=voice.model, agent_id="app", provider=voice.provider, ok=False,
               error_class=llm.error_class(e), latency_ms=latency_ms)  # fmt: skip


def elapsed_ms(t: float) -> int:
    return int((time.monotonic() - t) * 1000)
