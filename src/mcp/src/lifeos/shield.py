"""Atlas Shield (D125, D132): plain code, no AI, hides private data before any text reaches the model, and our side puts
the real values back after: in the words the user sees and in the calls the model asks for. Pure functions, no
database (the user's choices: lifeos.shieldprefs; where it runs: lifeos.runtime.studio).

What it hides depends on the user's level (Settings › Atlas Shield):
  standard (default)  the hard secrets: CARD (the Luhn check and the card brands' lengths), BANK (routing numbers with
                      the ABA checksum near a banking word, account numbers after an account word, IBANs with mod-97),
                      SSN, PASSWORD (after a password word, the second half of "login / password" pairs), KEY (known key
                      prefixes, private-key blocks, a key word and a random value, a long random run that isn't a link or
                      an id), CODE (a 4 to 8 digit number near a code word: one-time, sign-in, gate, door, alarm, PIN)
  private             + EMAIL, PHONE, ADDRESS
  strict              + PERSON and COMPANY: names after a greeting, in a sign-off or a "From:" line, before an email
                      address, the people the user told Atlas about (`people`), companies with a suffix (Inc, LLC...),
                      and a name model's finds when one is installed (`ner`, lifeos.shieldprefs.ner_finder)
  custom              standard + the groups the user picks (emails, phones, addresses, names)
Never hidden: the user's "always visible" list and their own and the assistant's names (`visible`); a hard secret is
hidden whatever the list says.

Stand-ins: [CARD_1] [BANK_1] [SSN_1] [PASSWORD_1] [KEY_1] [CODE_1] [EMAIL_1] [PHONE_1] [ADDRESS_1] [PERSON_A]
[COMPANY_A], numbered (names lettered) in the order the values first appear in one run's input. The Vault is rebuilt
from the run's own input at every step and never stored, so the same value keeps its stand-in through the run and
through the chat's turns while it is in the thread, a checkpoint holds no mapping, and one chat's or one user's values
can never be swapped into another's. A value hidden once is hidden wherever it appears again in the run, as written
(the run keeps real values, so the model's own "I won't share [PASSWORD_1]" comes back without the word "password").
Outside text can't forge a stand-in: anything shaped like one in incoming text is defused first ("[CARD_1]" becomes
"[CARD-1]"), so only the masker writes live ones. Atlas's own handles (ids, links, Undo tokens) are never secrets, and
the ids a provider issued (message, thread, draft and event ids; a mail's Message-ID, In-Reply-To and References
headers, as a field, in a header map or in a name/value header list) are never read as a long random run, so a reply's
thread headers go back into send_email as they were read. A known key shape, or a key word and its value, is still a
key in any field.

Detection reads a folded copy of each text (P2-308: NFKC, zero-width characters out, any space, dash or dot as one
kind, any script's digits as 0-9; cards and SSNs also with odd gaps between digit groups) and masks the characters the
user wrote, so a card pasted from a web page or a PDF is hidden like a typed one; the last check (`leaks`) reads the
same copy. A card written in words ("four one one one ...", "oh" or "o" for zero, "double"/"triple", words mixed with
digit groups, split by spaces, commas or hyphens) is read as its digits and goes through the same Luhn and brand checks;
the stand-in covers exactly the words (_spelled_cards).

Back to real values (unmask): what the user sees (streamed words: StreamUnmask; `present` blocks; a question; an app's
answer) gets every real value, except inside a link or a web address (P2-309): there a hard secret always stays its
stand-in, and a contact detail or name stays one unless the link goes to LINK_HOSTS. A tool call gets real contact
details and names (so the approval card shows the real recipient and the signed grant covers what runs), and a call
that names a hidden hard secret is refused before any card (tier D: runtime.policy.gate `hidden`), whatever the user
would approve. A detector error fails closed: the text is replaced by FAILED_NOTE.

One request Atlas writes from its own words and pieces of the user's (the Agent Builder, a creature): `Request` masks
only the user's pieces, and its answer gets contact details and names back but never a hard secret (`hidden`: "[hidden
card]"; RULES S8: an agent never keeps one).

Pictures: Shield can't look inside one. The user chooses (Settings › Atlas Shield › Pictures): sent as they are (the
default), or kept from the AI (`keep_pictures`): each becomes PICTURE_KEPT for the model, and the last check
(`leaks`) stops a call that still carries one. Limits, said in the app: it hides the shapes it recognises (unusual
formats can slip through), it can't see inside pictures, a voice is turned into text by a speech service before Shield
reads the text, and it works on Atlas's own chat engine (Runtime B), not on the Telegram path."""

from __future__ import annotations

import functools
import hashlib
import json
import logging
import math
import re
import threading
import unicodedata
from collections import Counter, OrderedDict
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from itertools import pairwise
from typing import Any

log = logging.getLogger(__name__)

# ---------- kinds and levels ----------

HARD: tuple[str, ...] = ("CARD", "BANK", "SSN", "PASSWORD", "KEY", "CODE")
CONTACT: tuple[str, ...] = ("EMAIL", "PHONE", "ADDRESS")
NAMES: tuple[str, ...] = ("PERSON", "COMPANY")
KINDS: tuple[str, ...] = (*HARD, *CONTACT, *NAMES)
# RE-7 (D178): text another person wrote in a shared item (lifeos.workspace_tools' `collab` fence) keeps its contact
# details from the AI whatever the reader's own level: a collaborator's email or phone isn't the reader's to send on.
COLLAB_FLOOR = frozenset(CONTACT)
_COLLAB = re.compile(r"<untrusted_collab>(.*?)</untrusted_collab>", re.DOTALL)
LETTERED = frozenset(NAMES)  # [PERSON_A], [COMPANY_B]; every other kind is numbered: [CARD_1]
LEVELS: tuple[str, ...] = ("standard", "private", "strict", "custom")
DEFAULT_LEVEL = "standard"
# the groups a Custom level may add to the hard secrets (Settings › Atlas Shield › Custom), in their order
GROUPS: dict[str, tuple[str, ...]] = {
    "emails": ("EMAIL",),
    "phones": ("PHONE",),
    "addresses": ("ADDRESS",),
    "names": NAMES,
}
_LEVEL_GROUPS: dict[str, tuple[str, ...]] = {
    "standard": (),
    "private": ("emails", "phones", "addresses"),
    "strict": ("emails", "phones", "addresses", "names"),
}
# what the receipt and the model's note call each kind (plural forms: COUNT_WORDS)
WORDS: dict[str, str] = {
    "CARD": "card", "BANK": "bank number", "SSN": "SSN", "PASSWORD": "password", "KEY": "key", "CODE": "code",
    "EMAIL": "email address", "PHONE": "phone number", "ADDRESS": "address", "PERSON": "name", "COMPANY": "company",
}  # fmt: skip

FAILED_NOTE = "[Atlas Shield couldn't check this text, so it was left out]"
# the model's note (the last input item of a step that hid something; never in the cached prefix)
MODEL_NOTE = (
    "Atlas Shield replaced private details in this conversation with stand-ins such as [CARD_1], [EMAIL_1] or "
    "[PERSON_A]. Write a stand-in exactly as it appears, brackets included, whenever you mean that detail: Atlas puts "
    "the real value back for the user and in actions they approve. Never guess or reconstruct a hidden value. Hidden "
    "cards, bank numbers, SSNs, passwords, keys and codes never go into any tool."
)
# RULES S8: never stored, never sent. The model reads this and tells the user, calmly, where such things belong.
REFUSAL = (
    "This call holds a value Atlas Shield hid from you (a card, bank number, SSN, password, key or code). Atlas never "
    "saves or sends those, even with the user's approval. Leave it out and carry on; tell the user kindly that a "
    "password manager is the safe place to keep it, and that they can type it in themselves wherever it is needed."
)
# the note after the user's words in an Agent Builder request that hid something (never in its instructions)
BUILDER_NOTE = (
    "Atlas Shield replaced private details in the user's words with stand-ins such as [EMAIL_1] or [PERSON_A]. Write "
    "a stand-in exactly as it appears, brackets included, wherever the agent needs that detail: Atlas puts the real "
    "value back. Never write a hidden card, bank number, SSN, password, key or code into the agent: agents never keep "
    "them."
)
# what the user reads when a request was stopped because something couldn't be hidden (fail closed)
STOPPED_WORDS = (
    "Atlas Shield stopped this: a private detail couldn't be hidden from the AI. Nothing was sent."
)
# what the model reads where a picture was, when the user keeps pictures from the AI
PICTURE_KEPT = (
    "(a picture was attached here; Atlas Shield kept it from you because the user keeps pictures from the AI. You "
    "can't see it: if it matters, say so and ask them to describe it)"
)
# what the user reads when an app was asked to read a picture they keep from the AI (lifeos.llm.complete)
PICTURES_KEPT = (
    "Atlas Shield kept this picture from the AI, so it wasn't read. Type the details instead, or check Pictures in "
    "Settings › Atlas Shield."
)


class Stopped(Exception):
    """A request Shield won't let go: a hard secret, or a picture the user keeps from the AI, is still in it after
    masking (fail closed). `kinds` only, never a value."""

    def __init__(self, kinds: Iterable[str]) -> None:
        self.kinds = tuple(sorted(set(kinds)))
        super().__init__(", ".join(self.kinds))


class PicturesKept(ValueError):
    """An app asked the AI to read a picture the user keeps from it: nothing was sent. A ValueError, so the screens
    show its words (PICTURES_KEPT) as they show any other refusal."""

    def __init__(self, message: str = PICTURES_KEPT) -> None:
        super().__init__(message)


def kinds_for(level: str, groups: Iterable[str] = ()) -> frozenset[str]:
    """What a level hides: the hard secrets always, then the level's groups (custom: the groups the user picked).
    An unknown level counts as standard: never less than the floor."""
    picked = tuple(groups) if level == "custom" else _LEVEL_GROUPS.get(level, ())
    out = set(HARD)
    for g in picked:
        out.update(GROUPS.get(g, ()))
    return frozenset(out)


# ---------- checksums ----------


def luhn(digits: str) -> bool:
    total = 0
    for i, ch in enumerate(reversed(digits)):
        d = ord(ch) - 48
        if i % 2:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


def card_brand(n: str) -> bool:
    """The number's length fits its brand's prefix (Visa, Mastercard, Amex, Discover, JCB, Diners, UnionPay)."""
    ln = len(n)
    if n[0] == "4":
        return ln in (13, 16, 19)
    two, three, four, six = int(n[:2]), int(n[:3]), int(n[:4]), int(n[:6])
    if 51 <= two <= 55 or 2221 <= four <= 2720:
        return ln == 16
    if two in (34, 37):
        return ln == 15
    if four == 6011 or two == 65 or 644 <= three <= 649 or 622126 <= six <= 622925:
        return 16 <= ln <= 19
    if 3528 <= four <= 3589:
        return 16 <= ln <= 19
    if two in (36, 38) or 300 <= three <= 305:
        return ln == 14
    if two == 62:
        return 16 <= ln <= 19
    return False


def aba(n: str) -> bool:
    """A US routing number: the 3-7-1 checksum and a Federal Reserve district prefix."""
    d = [ord(c) - 48 for c in n]
    if (3 * (d[0] + d[3] + d[6]) + 7 * (d[1] + d[4] + d[7]) + d[2] + d[5] + d[8]) % 10:
        return False
    pre = d[0] * 10 + d[1]
    return pre <= 12 or 21 <= pre <= 32 or 61 <= pre <= 72 or pre == 80


# IBAN lengths per country (ISO 13616 registry)
IBAN_LEN: dict[str, int] = {
    "AD": 24, "AE": 23, "AL": 28, "AT": 20, "AZ": 28, "BA": 20, "BE": 16, "BG": 22, "BH": 22, "BR": 29, "BY": 28,
    "CH": 21, "CR": 22, "CY": 28, "CZ": 24, "DE": 22, "DK": 18, "DO": 28, "EE": 20, "EG": 29, "ES": 24, "FI": 18,
    "FO": 18, "FR": 27, "GB": 22, "GE": 22, "GI": 23, "GL": 18, "GR": 27, "GT": 28, "HR": 21, "HU": 28, "IE": 22,
    "IL": 23, "IQ": 23, "IS": 26, "IT": 27, "JO": 30, "KW": 30, "KZ": 20, "LB": 28, "LC": 32, "LI": 21, "LT": 20,
    "LU": 20, "LV": 21, "MC": 27, "MD": 24, "ME": 22, "MK": 19, "MR": 27, "MT": 31, "MU": 30, "NL": 18, "NO": 15,
    "PK": 24, "PL": 28, "PS": 29, "PT": 25, "QA": 29, "RO": 24, "RS": 22, "SA": 24, "SC": 31, "SE": 24, "SI": 19,
    "SK": 24, "SM": 27, "ST": 25, "SV": 28, "TL": 23, "TN": 24, "TR": 26, "UA": 29, "VA": 22, "VG": 24, "XK": 20,
}  # fmt: skip


def iban(raw: str) -> bool:
    if IBAN_LEN.get(raw[:2]) != len(raw):
        return False
    moved = raw[4:] + raw[:4]
    return int("".join(str(int(c, 36)) for c in moved)) % 97 == 1


def _ssn_ok(area: str, group: str, serial: str) -> bool:
    return area not in ("000", "666") and area[0] != "9" and group != "00" and serial != "0000"


def _bits(s: str) -> float:
    n = len(s)
    return -sum(k / n * math.log2(k / n) for k in Counter(s).values())


# ---------- patterns ----------

# card candidates: 13 to 19 digits in one run, or in the brands' groupings (4-4-4-4 with an optional 3-digit group
# after it, 4-6-5, 4-6-4) split by one space, dash or dot, so a card next to another number is never swallowed by it
_CARD_PLAIN = re.compile(r"(?<![\w.,$/-])\d{13,19}(?![\w/-]|[.,]\d)")
_CARD_GROUPED = re.compile(
    r"(?<![\w.,$/-])(?:\d{4}([ .-])\d{4}\1\d{4}\1\d{4}(?:\1\d{3})?|\d{4}([ .-])\d{6}\2\d{4,5})(?![\w/-]|[.,]\d)"
)
_NEXT_GROUP = re.compile(r"[ .-]\d{4}(?!\d)")
# a card spoken or typed in words: digit words (zero, oh, o, one ... nine; "double"/"triple" before one of them) and digit
# groups, split by spaces, commas, dots, semicolons or hyphens (up to four such characters), are read as digits
_DIGIT_WORDS = {"zero": "0", "oh": "0", "o": "0", "one": "1", "two": "2", "three": "3", "four": "4", "five": "5",
                "six": "6", "seven": "7", "eight": "8", "nine": "9"}  # fmt: skip
_SPELLED = re.compile(
    r"(?<![\w/$'’])(?:(double|triple)[ \t]+)?(zero|one|two|three|four|five|six|seven|eight|nine|oh|o)(?![\w/'’])"
    r"|(?<![\w/$])(\d{1,19})(?![\w/'’])",
    re.IGNORECASE,
)
_SPELLED_GAP = re.compile(r"[ \t\r\n,;.-]{1,4}")
_SPELLED_MAX = 24  # a card and a little more (an expiry); a longer run of number words is a list, not a card
_CVV = re.compile(r"(?i)\b(?:cvv2?|cvc2?|cid|csc|card security code)\b\s*(?:is|:|#|=)?\s*(\d{3,4})(?![\w/])")
_NINE = re.compile(r"(?<![\w.,$/-])\d{9}(?![\w/-]|[.,]\d)")
_BANK_WORD = re.compile(r"(?i)\b(?:routing|aba|rtn|transit|wire|direct deposit|bank|checking|savings)\b")
_ACCOUNT = re.compile(
    r"(?i)\b(?:(?:bank |checking |savings )?account(?: number| no\.?| num(?:ber)?| #)?|acct\.?(?: no\.?| #)?|a/c)"
    r"(?:\s*(?:is|:|#|=))?\s*(\d(?:[ -]?\d){5,16})(?![\w/-]|[.,]\d)"
)
_IBAN = re.compile(r"\b[A-Z]{2}\d{2}(?: ?[A-Z0-9]{4}){2,7}(?: ?[A-Z0-9]{1,4})?\b")
# 3-2-4 digits split by one dash, space or dot (never part of a longer dotted number: 1.123.45.6789)
_SSN = re.compile(r"(?<![\w-])(?<!\d\.)(\d{3})([- .])(\d{2})\2(\d{4})(?![\w-]|\.\d)")
_SSN_WORD = re.compile(r"(?i)\b(?:ssn|social security|social|tin|taxpayer|w-?9|itin)\b")
_KEY_SHAPES: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bsk-(?:proj-|ant-(?:api|admin)\d\d-)?[A-Za-z0-9_\-]{20,}"),
    re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,})"),
    re.compile(r"\bglpat-[A-Za-z0-9_\-]{20,}"),
    re.compile(r"\bxox[abposr]-[A-Za-z0-9\-]{10,}"),
    re.compile(r"\b(?:AKIA|ASIA)[A-Z2-7]{16}\b"),
    re.compile(r"\bAIza[A-Za-z0-9_\-]{35}"),
    re.compile(r"\b(?:sk|rk|pk)_(?:live|test)_[A-Za-z0-9]{20,}"),
    re.compile(r"\bhf_[A-Za-z]{30,}"),
    re.compile(r"\bSG\.[A-Za-z0-9_\-]{16,}\.[A-Za-z0-9_\-]{30,}"),
    re.compile(r"\bSK[0-9a-f]{32}\b"),
    re.compile(r"\bnpm_[A-Za-z0-9]{36}\b"),
    re.compile(r"\beyJ[A-Za-z0-9_\-]{8,}\.eyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}"),
    re.compile(r"(?<![A-Za-z0-9~])\d{1,8}~[A-Za-z0-9]{32,}(?![A-Za-z0-9])"),  # a Canvas access token
    re.compile(
        r"-----BEGIN (?:[A-Z]+ )*PRIVATE KEY-----[\s\S]{0,8000}?-----END (?:[A-Z]+ )*PRIVATE KEY-----"
    ),
)
_SK_TAIL = re.compile(r"\bsk-")
# a value right after a key word: api_key=..., token: ..., Bearer ...; 16+ characters with some randomness
_KEY_WORD = re.compile(
    r"(?i)(?:api[_ -]?key|secret(?:[_ -]key)?|access[_ -]?key|auth[_ -]?token|token|bearer|authorization)"
    r"[\"']?\s*(?:is\s+)?[:=]?\s*[\"']?(?:bearer\s+)?([A-Za-z0-9_\-./+=]{16,})"
)
_URL = re.compile(r"(?i)\b(?:https?|wss?|ftp)://[^\s<>\"'`]+|\bwww\.[^\s<>\"'`]+")
_RUN = re.compile(r"[A-Za-z0-9+/=_\-]{32,}")
_ASSIGN = re.compile(r"[A-Za-z_][A-Za-z0-9_\-]{1,30}=(?=[A-Za-z0-9])")
_HEX = re.compile(r"^[0-9A-Fa-f\-]+$")
_RUN_MIN, _RUN_BITS = 32, 3.7
_PASSWORD = re.compile(
    r"(?i)\b(?:password|passwd|passphrase|pass ?code|pwd|pw|pass)\b"
    r"((?:[ \t]+(?!is\b|was\b)[\w'-]{1,20}){0,4}?)"  # up to four words: "for the airbnb"
    r"[ \t]*(?:is|was|:|=)[ \t]*(?:is[ \t]*)?[\"'“‘]?(\S+)"
)
_PAIR = re.compile(
    r"(?i)\b(?:login|log-?in|username|user ?name|user|sign-?in|creds|credentials)\b[ \t]*(?:is|:|=)?[ \t]*"
    r"[\"']?([\w.@+-]{2,64})[\"']?[ \t]*/[ \t]*[\"'“‘]?(\S+)"
)
_USERINFO = re.compile(r"(?i)\b[a-z][a-z0-9+.-]*://[^\s/:@]{1,64}:([^\s/@]{1,128})@")
_EMAIL_PAIR = re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}[ \t]*/[ \t]*[\"'“‘]?(\S+)")
# fmt: off
_NOT_PASSWORD = frozenset({
    "reset", "required", "incorrect", "expired", "the", "a", "an", "too", "must", "should", "will", "has",
    "was", "is", "are", "be", "been", "being", "protected", "needed", "changed", "change", "below", "above",
    "here", "there", "same", "different", "case-sensitive", "at", "least", "not", "wrong", "invalid", "weak",
    "strong", "strength", "manager", "managers", "reset.", "updated", "sent", "and", "or", "for", "to", "of",
    "on", "in", "it", "this", "that", "your", "my", "our", "their", "his", "her", "please", "link", "page",
    "field", "box", "hint", "hidden", "saved", "policy", "rules", "rule", "requirement", "requirements",
    "characters", "long", "short", "secure", "safe", "correct", "missing", "blank", "empty", "set", "new",
    "old", "forgot", "forgotten", "again", "only", "also", "still", "now", "later", "soon", "then", "one",
    "two", "three", "login", "sign", "signin", "email", "username", "user", "account", "none", "n/a", "na",
    "null", "undefined", "tbd", "unknown", "password", "passwords", "pass", "pwd", "pw", "passphrase",
    "passcode",
})
# fmt: on
_CODE_WORD = re.compile(
    r"(?i:\b(?:(?:verification|security|sign-?in|log-?in|one-time|single-use|2fa|two-factor|mfa|auth(?:entication)?"
    r"|access|login|gate|door|alarm|garage|lock|locker|building|entry|entrance|keypad|safe|lockbox|backup|recovery)"
    r"\s+(?:code|passcode|pin)|otp|passcode|pin\s+(?:code|number))\b)|\bPIN\b"
)
_CODE_BARE = re.compile(
    r"(?i)\bcode(?:(?:\s+[\w'’]+){0,4}?\s+(?:is|was)|\s+(?:is|was))?\s*[:#=]?\s*(\d{4,8}|\d{3}[ -]\d{3})"
    r"(?![\w/%:]|[.,]\d|-\d)"
)
_CODE_YOURS = re.compile(
    r"(?<![\w.,$/:-])(?:[A-Z]-)?(\d{4,8}|\d{3}[ -]\d{3}) is your(?: [\w-]+){0,3} (?i:code|passcode|pin)\b"
)
# code words that name something else: never a secret
_NOT_CODE = re.compile(
    r"(?i)\b(?:zip|postal|post|area|error|status|course|discount|promo|promotion|coupon|confirmation|country|tracking|"
    r"booking|reference|order|product|dial|qr|source|bar|http|exit|response|class|style|dress|color|colour|program|"
    r"tax|shipping|voucher|gift|referral|invite|invitation|event|room|item|sku|reason|return|cpt|icd|naics|swift|"
    r"bic|sort|airport|iata)\s+code\b"
)
_CODE_NUM = re.compile(r"(?<![\w.,$/:-])(?:\d{4,8}|\d{3}[ -]\d{3})(?![\w/%:]|[.,]\d|-\d)")
_CODE_VERB = re.compile(
    r"(?i)\b(?:enter|use|type|input|key in)\s+(\d{4,8}|\d{3}[ -]\d{3})\s+(?:to|as|for|at)\s+(?:verify|confirm|continue|"
    r"sign in|log in|login|complete|finish|access|unlock|proceed|reset)"
    r"|\bto (?:verify|confirm|continue|sign in|log in|finish signing in|reset your password),? (?:use|enter|type)"
    r"(?: (?:this|the) code)?:? (\d{4,8}|\d{3}[ -]\d{3})(?![\w/%:]|[.,]\d|-\d)"
)
_SENTENCE_END = re.compile(r"[.!?\n](?:\s|$)")
_EMAIL = re.compile(r"(?<![\w.%+-])[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,24}\b")
_PHONE = re.compile(
    r"(?<![\w-])(?:\+?1[ .-]?)?(?:\(\d{3}\)\s?|\d{3}[ .-])\d{3}[ .-]\d{4}(?![\w-])"
    r"|(?<![\w-])\+\d{1,3}(?:[ .-]\d{1,8}){2,5}(?![\w-])"
)
_PHONE_WORD = re.compile(r"(?i)\b(?:phone|tel|cell|mobile|call|text|whatsapp|fax)\b\W{0,3}(\d{10,11})(?!\d)")
_SUFFIX = (
    r"(?:Street|St|Avenue|Ave|Road|Rd|Lane|Ln|Drive|Dr|Boulevard|Blvd|Court|Ct|Way|Place|Pl|Terrace|Ter|Parkway|Pkwy"
    r"|Highway|Hwy|Square|Sq|Circle|Cir|Trail|Trl|Plaza|Row|Loop|Crescent|Close|Mews|Alley|Pike|Turnpike)"
)
_ADDR_WORD = r"(?:[A-Z][a-z'’]+|[NSEW]\.?|NE|NW|SE|SW|\d{1,3}(?:st|nd|rd|th))"
_ADDRESS = re.compile(
    rf"(?<![\w-])\d{{1,6}}[A-Z]?(?:-\d{{1,3}}(?!\d))?,? (?:{_ADDR_WORD} ){{1,4}}{_SUFFIX}\b\.?"
    r"(?:,? (?:Apt|Apartment|Suite|Ste|Unit|Floor|Fl|#)\.? ?#?[\w-]{1,6})?"
    r"(?:,? [A-Z][a-z]+(?: [A-Z][a-z]+){0,2},? [A-Z]{2}(?: \d{5}(?:-\d{4})?)?)?"
)
_ADDRESS_LOWER = re.compile(
    r"(?<![\w-])\d{1,6}[a-z]? (?!(?:the|a|an|of|on|in|to|down|up|more|miles?|blocks?|minutes?)\b)[a-z]{3,20}"
    r"(?: [a-z]{3,20})? (?:st|street|ave|avenue|rd|road|blvd|dr|drive|ln|lane)\b\.?"
)
_PO_BOX = re.compile(r"(?i)\bP\.? ?O\.? Box \d{1,7}\b")
_NAME = r"[A-Z][a-z'’]+(?:[ -](?:[A-Z][a-z'’]+|[A-Z]\.?))?(?:[ -][A-Z][a-z'’]+)?"
_PERSON_CUES: tuple[re.Pattern[str], ...] = (
    re.compile(rf"^(?:From|To|Cc|Reply-To): ?({_NAME}) <", re.MULTILINE),
    re.compile(rf"(?:^|[\s\"(,])({_NAME}) <[^<>@\s]+@"),
    re.compile(rf"\b(?:Hi|Hello|Hey|Dear|Thanks|Thank you),? ({_NAME})(?=[,!.:\n])"),
    re.compile(
        rf"(?:Best|Thanks|Regards|Cheers|Sincerely|Warmly|Best regards|Kind regards|Thank you),[ \t]*\n({_NAME})\s*$",
        re.MULTILINE,
    ),
    re.compile(rf"\b(?:Dr|Mr|Mrs|Ms|Mx|Prof)\.? ({_NAME})"),
    re.compile(
        rf"\b(?:with|ask|meet|met|text|call|email|tell|cc|Intro:|and|[Rr]each) ({_NAME})(?='s\b|\b)(?! [A-Z]{{2}})"
    ),
    re.compile(
        rf"(?:^|[.!?\"] )({_NAME}) (?:said|says|confirmed|asked|wrote|will|is|was|thinks|wants)\b",
        re.MULTILINE,
    ),
    re.compile(rf"\b(?:name on (?:the )?card|cardholder|name)[: ]+({_NAME})"),
)
_COMPANY = re.compile(
    r"\b((?:[A-Z][\w&'’.-]*[ ]){0,3}[A-Z][\w&'’.-]*),?[ ](Inc\.?|LLC|L\.L\.C\.|Ltd\.?|Limited|Corp\.?|Corporation|"
    r"Co\.|GmbH|AG|S\.A\.|SAS|PLC|plc|LLP|Holdings|Technologies|Labs|Partners)(?![\w])"
)
# capitalised words that start sentences or name days, months and places in mail, never a person by themselves
# fmt: off
_NOT_NAMES = frozenset({
    "The", "This", "That", "These", "Those", "Please", "Use", "Send", "Your", "Our", "My", "Ship", "Card",
    "Meeting", "Order", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday", "January",
    "February", "March", "April", "May", "June", "July", "August", "September", "October", "November",
    "December", "Bank", "Mobile", "Front", "Pickup", "Here", "There", "Updated", "Pay", "Transfer", "Team",
    "Support", "Hello", "Hi", "Hey", "Dear", "Thanks", "Thank", "Best", "Regards", "Cheers", "All", "Everyone",
    "Folks", "Sir", "Madam", "Atlas", "Today", "Tomorrow", "Yesterday", "Re", "Fwd", "Subject", "From", "To",
    "Cc", "Note", "Notes", "Call", "Text", "Email", "Ask", "Meet", "Tell", "With", "And", "For", "Customer",
    "Client", "Billing", "Security", "Account", "Admin", "Office", "Manager", "Room", "Street", "Avenue",
    "Road", "Lane", "Drive", "Way", "Court", "It", "We", "You", "They", "He", "She", "I", "Apt", "Suite",
    "Unit", "Inc", "Corp", "Workspace", "Undo",  # Atlas's own words in agents' instructions ("with Undo")
})
# fmt: on
# stand-in grammar: the live form the masker writes, and every spelling outside text might use to forge one
_TOKEN = re.compile(r"\[(" + "|".join(KINDS) + r")_(\d{1,4}|[A-Z]{1,3})\]")
_FORGED = re.compile(r"(?i)(?<![A-Za-z0-9_])(" + "|".join(KINDS) + r")\s*[_＿‗]\s*(\d{1,4}|[A-Z]{1,3})\b")
_PARTIAL = re.compile(r"\[(?:[A-Z]{1,8}(?:_[A-Z0-9]{0,4})?)?")
TOKEN_MAX = max(len(f"[{k}_9999]") for k in KINDS)
# fullwidth forms fold to ASCII one character for one (offsets stay), so ［ＣＡＲＤ＿１］ is defused too
_FULLWIDTH = str.maketrans({chr(c): chr(c - 0xFEE0) for c in range(0xFF01, 0xFF5F)})
# JSON keys whose values are ids, links or names of things, never a key: the "random run" rule skips them. The words may
# be joined by "_", "-" or a space ("message_id", "Message-ID") or run together ("threadId", "internetMessageId"); the
# mail headers that carry message ids count too (2026-11-08: draft_email's in_reply_to, a reply's Message-ID, was read
# as a key, so every reply was refused as a hidden secret). A known key shape, or a key word and its value, is still a
# key in any field (_hard).
_ID_KEY = re.compile(r"(?i)(?:^|[_\- ])(?:id|ids|uid|uuid|url|uri|link|href|src|path|file|filename|name|slug|etag|"
                     r"cursor|undo_tokens?|page_token|sync_token|token_type|thread|ts|hash|sha\d*|ref|key_id|"
                     r"in[_\- ]reply[_\- ]to|references|thread[_\- ]index)$"
                     r"|(?-i:[a-z0-9](?:Id|Ids|ID|IDs|Uid|UID))$")  # fmt: skip


# ---------- finding ----------


@dataclass(frozen=True)
class Found:
    """One private value in a text: [start, end) and its kind."""

    start: int
    end: int
    kind: str


NerFinder = Callable[[str], Sequence[tuple[int, int, str]]]  # text → (start, end, "PERSON" | "COMPANY")
_CAPITAL = re.compile(r"(?<![\w-])[A-Z][\w'’-]*")


class People:
    """The names a user told Atlas about (People facts), found in one pass over a text's capitalised words: each full
    name, and its first word on its own ("Dana" for "Dana Smith"). Case as written; longest name first."""

    def __init__(self, names: Iterable[str]) -> None:
        self.by_first: dict[str, list[str]] = {}
        for raw in names:
            name = " ".join(str(raw).split())
            words = name.split(" ")
            if len(name) < 3 or not words[0][:1].isupper() or words[0] in _NOT_NAMES:
                continue
            forms = [name] + ([words[0]] if len(words) > 1 and len(words[0]) >= 3 else [])
            for form in forms:
                bucket = self.by_first.setdefault(form.split(" ")[0], [])
                if form not in bucket:
                    bucket.append(form)
        for bucket in self.by_first.values():
            bucket.sort(key=len, reverse=True)

    def __bool__(self) -> bool:
        return bool(self.by_first)

    def names(self) -> list[str]:
        return sorted({n for b in self.by_first.values() for n in b})

    def finditer(self, text: str) -> Iterable[tuple[int, int]]:
        for m in _CAPITAL.finditer(text):
            word = m.group(0).rstrip("'’-")
            if word.endswith(("'s", "’s")):
                word = word[:-2]
            for name in self.by_first.get(word, ()):
                end = m.start() + len(name)
                if text.startswith(name, m.start()) and not (
                    end < len(text) and (text[end].isalnum() or text[end] == "-")
                ):
                    yield m.start(), end
                    break


def _random_run(run: str) -> bool:
    """A run that reads as random (lifeos.secretshape.random_run's rule): letters and digits switch at least twice, at
    most one in eight characters is a separator, at least 3.7 bits of entropy per character, and not hex only."""
    if len(run) < _RUN_MIN or _HEX.match(run):
        return False
    kinds = ["d" if c.isdigit() else "l" for c in run if c.isalnum()]
    switches = sum(a != b for a, b in pairwise(kinds))
    separators = sum(c in "+/=_-" for c in run)
    return switches >= 2 and separators * 8 <= len(run) and _bits(run) >= _RUN_BITS


def _mixed(token: str) -> bool:
    return (
        any(c.isdigit() for c in token)
        and any(c.isupper() for c in token)
        and any(c.islower() for c in token)
    )


def _password_value(raw: str) -> tuple[int, int] | None:
    """The secret part of a value after a password word: trailing punctuation off; never a link, an id-free common
    word, a masked run (••••, ****) or anything shorter than 4."""
    if _TOKEN.match(raw):  # already a stand-in ("[PASSWORD_1]!")
        return None
    v = raw.rstrip(".,;:)]}\"'”’!?")
    if v[:1] in "\"'“‘(":
        v = v[1:]
    if (
        len(v) < 4
        or v.lower().strip(".") in _NOT_PASSWORD
        or _URL.match(v)
        or v.lower().startswith(("http", "www."))
    ):
        return None
    if len(set(v)) == 1 or not any(c.isalnum() for c in v):
        return None
    if (
        _TOKEN.fullmatch(v) or _EMAIL.fullmatch(v) or _PHONE.fullmatch(v)
    ):  # a stand-in, or "email / email" lists
        return None
    start = raw.find(v)
    return start, start + len(v)


def _cards(text: str, out: list[Found]) -> None:
    # CARD: Luhn and a brand's length; a 4-4-4-4 card followed by a 3-digit group (its CVV) is checked without it, and
    # four groups followed by a fifth group of four (a tracking number) are not a card
    for m in _CARD_PLAIN.finditer(text):
        if luhn(m.group(0)) and card_brand(m.group(0)):
            out.append(Found(m.start(), m.end(), "CARD"))
    for m in _CARD_GROUPED.finditer(text):
        raw = m.group(0)
        digits = re.sub(r"[ .-]", "", raw)
        if len(digits) == 16 and _NEXT_GROUP.match(text, m.end()):
            continue
        if luhn(digits) and card_brand(digits):
            out.append(Found(m.start(), m.end(), "CARD"))
        elif len(digits) == 19 and luhn(digits[:16]) and card_brand(digits[:16]):
            out.append(
                Found(m.start(), m.start() + 19, "CARD")
            )  # 19 characters: the four groups, the CVV left
    for m in _CVV.finditer(text):  # the card's security code goes with the card
        out.append(Found(m.start(1), m.end(1), "CARD"))
    _spelled_cards(text, out)


def _spelled_runs(text: str) -> Iterator[list[tuple[int, int, str, bool]]]:
    """Runs of digit tokens in `text`: (start, end, its digits, is it a word). A token is a digit word ("four", "oh",
    "triple one" = 111) or a whole number ("4111"); the next token must follow within four spaces, commas, dots,
    semicolons or hyphens, so any other word ends the run."""
    run: list[tuple[int, int, str, bool]] = []
    for m in _SPELLED.finditer(text):
        if run and not _SPELLED_GAP.fullmatch(text, run[-1][1], m.start()):
            yield run
            run = []
        if m.group(3) is not None:
            run.append((m.start(), m.end(), m.group(3), False))
        else:
            times = {"double": 2, "triple": 3}.get((m.group(1) or "").lower(), 1)
            run.append((m.start(), m.end(), _DIGIT_WORDS[m.group(2).lower()] * times, True))
    if run:
        yield run


def _spelled_cards(text: str, out: list[Found]) -> None:
    # CARD written in words ("four one one one ...", or "4111 one one one ..."): the digits of a window of the run go
    # through the same Luhn and brand checks, the longest window from each start; it must hold a word (digits alone are
    # read above) and the run at most _SPELLED_MAX digits, so a card with a few digits next to it (an expiry) is found
    # and a long list of number words is not
    for run in _spelled_runs(text):
        if not any(t[3] for t in run) or not 13 <= sum(len(t[2]) for t in run) <= _SPELLED_MAX:
            continue
        i = 0
        while i < len(run):
            digits, best = "", -1
            for j in range(i, len(run)):
                digits += run[j][2]
                if len(digits) > 19:
                    break
                if (
                    len(digits) >= 13
                    and luhn(digits)
                    and card_brand(digits)
                    and any(t[3] for t in run[i : j + 1])
                ):
                    best = j
            if best >= 0:
                out.append(Found(run[i][0], run[best][1], "CARD"))
            i = best + 1 if best >= 0 else i + 1


def spelled_digits(value: str) -> str:
    """The digits a number written in words and digits stands for ("four one 1111" → "411111"); "" when it has none."""
    return "".join(t[2] for run in _spelled_runs(value) for t in run)


def _ssns(text: str, out: list[Found]) -> None:
    for m in _SSN.finditer(text):
        if _ssn_ok(m.group(1), m.group(3), m.group(4)):
            out.append(Found(m.start(), m.end(), "SSN"))


def _hard(text: str, out: list[Found], *, ids: bool) -> None:
    _cards(text, out)
    # BANK: a routing number (checksum + district + a banking word nearby), an account number after an account word,
    # an IBAN (country length + mod-97); SSN: nine bare digits after an SSN word
    for m in _NINE.finditer(text):
        n, a, b = m.group(0), m.start(), m.end()
        near = text[max(0, a - 40) : a] + " " + text[b : b + 30]
        if aba(n) and _BANK_WORD.search(near):
            out.append(Found(a, b, "BANK"))
        elif _SSN_WORD.search(text[max(0, a - 30) : a]) and _ssn_ok(n[:3], n[3:5], n[5:]):
            out.append(Found(a, b, "SSN"))
    for m in _ACCOUNT.finditer(text):
        out.append(Found(m.start(1), m.end(1), "BANK"))
    for m in _IBAN.finditer(text):
        raw = m.group(0).replace(" ", "")
        if iban(raw):
            out.append(Found(m.start(), m.start() + len(m.group(0).rstrip()), "BANK"))
    _ssns(text, out)
    # KEY: known prefixes and private-key blocks anywhere (a key in a link is still a key); a key word and a random-looking
    # value, and a long random run, outside links (links carry ids and hashes) and, in JSON, outside id and link fields
    # (`ids` False: a Drive file id is not a key, nor a reply's Message-ID: _ID_KEY)
    for pat in _KEY_SHAPES:
        for m in pat.finditer(text):
            if pat.pattern.startswith(r"\bsk-") and not _mixed(m.group(0)[3:]):
                continue
            out.append(Found(m.start(), m.end(), "KEY"))
    plain = _URL.sub(lambda u: " " * len(u.group(0)), text) if "/" in text or "www." in text else text
    for m in _KEY_WORD.finditer(plain):
        v = m.group(1)
        if any(c.isdigit() for c in v) and any(c.isalpha() for c in v) and _bits(v) >= 3.5:
            out.append(Found(m.start(1), m.end(1), "KEY"))
    if ids:
        for m in _RUN.finditer(plain):
            name = _ASSIGN.match(m.group(0))  # "api_key=VALUE" in one run: only the value
            start = m.start() + (name.end() if name else 0)
            if _random_run(plain[start : m.end()]):
                out.append(Found(start, m.end(), "KEY"))
    # PASSWORD: after a password word (up to four words between: "the wifi pw for the airbnb is X"), the second half
    # of "login / password" and "email / password" pairs, outside links (a meeting link's pwd= stays usable); a
    # password inside a link's user part (https://user:secret@host)
    for m in _PASSWORD.finditer(plain):
        got = _password_value(m.group(2))
        if got is not None:
            out.append(Found(m.start(2) + got[0], m.start(2) + got[1], "PASSWORD"))
    for pat in (_PAIR, _EMAIL_PAIR):
        for m in pat.finditer(plain):
            grp = m.lastindex or 1
            got = _password_value(m.group(grp))
            if got is not None:
                out.append(Found(m.start(grp) + got[0], m.start(grp) + got[1], "PASSWORD"))
    for m in _USERINFO.finditer(text):
        out.append(Found(m.start(1), m.end(1), "PASSWORD"))
    # CODE: the nearest 4-8 digit number in the same sentence as a code word (one-time, sign-in, gate, door, PIN...: 28
    # characters after it, or 40 before), unless the words name something else (zip, area, error, discount... code);
    # after a bare "code" only the number right after it ("code: 4471"); "123456 is your code"; "enter 123456 to verify"
    for kw in _CODE_WORD.finditer(text):
        if _NOT_CODE.search(text[max(0, kw.start() - 24) : kw.end()]):
            continue
        after = text[kw.end() : kw.end() + 28]
        cut = _SENTENCE_END.search(after)
        after = after[: cut.start()] if cut else after
        m = _CODE_NUM.search(after)
        if m:
            out.append(Found(kw.end() + m.start(), kw.end() + m.end(), "CODE"))
            continue
        lo = max(0, kw.start() - 40)
        before = text[lo : kw.start()]
        stop = max(before.rfind(". "), before.rfind("\n"), before.rfind("! "), before.rfind("? "))
        base = lo + (stop + 1 if stop >= 0 else 0)
        hits = list(_CODE_NUM.finditer(text[base : kw.start()]))
        if hits:
            out.append(Found(base + hits[-1].start(), base + hits[-1].end(), "CODE"))
    for pat in (_CODE_VERB, _CODE_BARE, _CODE_YOURS):
        for m in pat.finditer(text):
            if pat is _CODE_BARE and _NOT_CODE.search(text[max(0, m.start() - 24) : m.start() + 4]):
                continue
            g = m.lastindex or 1
            out.append(Found(m.start(g), m.end(g), "CODE"))


def _contact(text: str, kinds: frozenset[str], out: list[Found]) -> None:
    if "EMAIL" in kinds:
        out.extend(Found(m.start(), m.end(), "EMAIL") for m in _EMAIL.finditer(text))
    if "PHONE" in kinds:
        out.extend(Found(m.start(), m.end(), "PHONE") for m in _PHONE.finditer(text))
        out.extend(Found(m.start(1), m.end(1), "PHONE") for m in _PHONE_WORD.finditer(text))
    if "ADDRESS" in kinds:
        for pat in (_ADDRESS, _ADDRESS_LOWER, _PO_BOX):
            out.extend(Found(m.start(), m.end(), "ADDRESS") for m in pat.finditer(text))


def _name_ok(name: str) -> bool:
    words = re.split(r"[ -]", name)
    return words[0] not in _NOT_NAMES and not any(w in _NOT_NAMES for w in words[1:] if len(w) > 2)


def _names(
    text: str, kinds: frozenset[str], people: People | None, ner: NerFinder | None, out: list[Found]
) -> None:
    if "PERSON" in kinds:
        # the people Atlas knows first: exact, so a cue's guess that overlaps one ("Later Dana said") gives way
        known = [Found(a, b, "PERSON") for a, b in people.finditer(text)] if people else []
        out.extend(known)
        for pat in _PERSON_CUES:
            for m in pat.finditer(text):
                a, b = m.start(1), m.end(1)
                if _name_ok(m.group(1)) and not any(a < k.end and k.start < b for k in known):
                    out.append(Found(a, b, "PERSON"))
    if "COMPANY" in kinds:
        for m in _COMPANY.finditer(text):
            if m.group(1).split()[0] not in _NOT_NAMES:
                out.append(Found(m.start(1), m.end(2), "COMPANY"))
    if ner is not None:
        try:
            for a, b, kind in ner(text):
                if kind in kinds and 0 <= a < b <= len(text) and _name_ok(text[a:b]):
                    out.append(Found(a, b, kind))
        except Exception as e:  # noqa: BLE001 - names are extra: the cues still ran; class only
            log.warning("shield: name model failed (%s)", type(e).__name__)


_RANK = {k: i for i, k in enumerate(KINDS)}  # hard kinds first: they win an overlap


def resolve(found: Iterable[Found]) -> list[Found]:
    """Non-overlapping finds in text order: where two overlap, a hard secret beats a contact detail or a name, then the
    longer one wins."""
    ranked = sorted(found, key=lambda f: (_RANK[f.kind] >= len(HARD), -(f.end - f.start), f.start))
    taken: list[Found] = []
    for f in ranked:
        if f.end > f.start and all(f.end <= t.start or f.start >= t.end for t in taken):
            taken.append(f)
    return sorted(taken, key=lambda f: f.start)


# ---------- the text detection reads (P2-308) ----------

# Text copied from a web page, a PDF or a phone writes numbers in ways the patterns above don't read: no-break and
# narrow spaces, fullwidth or Arabic-Indic digits, zero-width characters, en and em dashes, middle dots. Detection reads
# a folded copy instead (_View): each character NFKC-folded on its own, format characters (Unicode Cf: zero-width
# spaces and joiners, the soft hyphen, direction marks) left out, any space read as " ", any dash as "-", any dot as
# ".", any script's digit as 0-9. Cards and SSNs are read once more with odd gaps between digit groups (two or three
# spaces, a line break with or without a dash or dot before it, a dash or a dot with spaces round it) as one separator
# (_View.gaps). Each find maps back to the characters the user wrote, so masking replaces exactly those, and the checks
# behind each shape (Luhn, the brands' lengths, the SSN rules) still decide.
_DASHES = frozenset("\u2010\u2011\u2012\u2013\u2014\u2015\u2043\u2212\u2e3a\u2e3b\ufe58\ufe63\uff0d")
_DOTS = frozenset("\u00b7\u2022\u2024\u2027\u2219\u22c5\u30fb\uff0e\uff65")
_NOT_ASCII = re.compile(r"[^\x00-\x7f]")
_ODD_GAP = re.compile(
    r"(?<=\d)(?:[ \t]{0,3}[-.]?[ \t]{0,3}(?:\r\n|[\n\r])[ \t]{0,3}|[ \t]{0,3}[-.][ \t]{0,3}|[ \t]{2,3}|\t)(?=\d)"
)


@functools.lru_cache(maxsize=8192)
def _fold_char(ch: str) -> str:
    """One character as detection reads it ("" for a format character)."""
    if unicodedata.category(ch) == "Cf":
        return ""
    out = []
    for c in unicodedata.normalize("NFKC", ch):
        cat = unicodedata.category(c)
        if c in _DASHES:
            c = "-"
        elif c in _DOTS:
            c = "."
        elif cat == "Zs":
            c = " "
        elif cat in ("Zl", "Zp"):
            c = "\n"
        elif cat == "Nd" and not c.isascii():
            c = str(unicodedata.decimal(c))
        out.append(c)
    return "".join(out)


@dataclass(frozen=True)
class _View:
    """A text as detection reads it, and where each of its characters came from in the text it was read from (`src`,
    one more entry than `text` has characters; None: the same text)."""

    text: str
    src: tuple[int, ...] | None = None

    @classmethod
    def of(cls, text: str) -> _View:
        if text.isascii():
            return cls(text)
        parts: list[str] = []
        src: list[int] = []
        at = 0
        for m in _NOT_ASCII.finditer(text):
            i = m.start()
            parts.append(text[at:i])
            src.extend(range(at, i))
            got = _fold_char(text[i])
            parts.append(got)
            src.extend([i] * len(got))
            at = i + 1
        parts.append(text[at:])
        src.extend(range(at, len(text)))
        src.append(len(text))
        folded = "".join(parts)
        return cls(text) if folded == text else cls(folded, tuple(src))

    def gaps(self) -> _View | None:
        """This view with each odd gap between two digits read as one separator ("-" when it holds a dash, "." when it
        holds a dot, else " "), mapped back to the original text; None when there is none."""
        parts: list[str] = []
        src: list[int] = []
        t, at = self.text, 0
        for m in _ODD_GAP.finditer(t):
            run = m.group(0)
            sep = "-" if "-" in run else "." if "." in run else " "
            if run == sep:
                continue
            parts += [t[at : m.start()], sep]
            src.extend(range(at, m.start() + 1))
            at = m.end()
        if not parts:
            return None
        parts.append(t[at:])
        src.extend(range(at, len(t) + 1))
        mine = self.src
        return _View("".join(parts), tuple(src) if mine is None else tuple(mine[i] for i in src))

    def back(self, f: Found) -> Found:
        """A find in this view as the span of the characters it came from."""
        if self.src is None or f.end <= f.start:
            return f
        return Found(self.src[f.start], self.src[f.end - 1] + 1, f.kind)


def detect(
    text: str,
    kinds: Iterable[str] = HARD,
    *,
    people: People | None = None,
    ner: NerFinder | None = None,
    ids: bool = True,
) -> list[Found]:
    """Every private value of the given kinds in `text`, non-overlapping, in order, read as _View folds it (spans are
    the original characters). `ids` False: the text is the value of an id or link field (JSON), so the
    long-random-run rule is skipped (a Drive file id is not a key)."""
    want = frozenset(kinds)
    out: list[Found] = []
    if not text:
        return out
    view = _View.of(text)
    _hard(view.text, out, ids=ids)
    _contact(view.text, want, out)
    _names(view.text, want, people, ner, out)
    found = [view.back(f) for f in out]
    gaps = view.gaps()
    if gaps is not None:  # cards and SSNs split by odd gaps (two spaces, a line break, a spaced dash)
        odd: list[Found] = []
        _cards(gaps.text, odd)
        _ssns(gaps.text, odd)
        found += [gaps.back(f) for f in odd]
    return resolve(f for f in found if f.kind in want or f.kind in HARD)


# ---------- the vault ----------


def _letters(n: int) -> str:
    """1 → A, 26 → Z, 27 → AA (bijective base 26)."""
    out = ""
    while n > 0:
        n, r = divmod(n - 1, 26)
        out = chr(65 + r) + out
    return out


def identity(kind: str, value: str) -> str:
    """Two spellings of one value share a stand-in: digits only for numbers, case and spaces folded for the rest, each
    read as detection reads it (fullwidth digits, no-break spaces: _View). A password or key is exactly as written."""
    if kind in ("PASSWORD", "KEY"):
        return value
    value = _View.of(value).text
    if kind in ("CARD", "SSN", "CODE", "PHONE"):
        digits = (
            spelled_digits(value)
            if kind == "CARD" and re.search(r"[A-Za-z]", value)
            else re.sub(r"\D", "", value)
        )
        return digits[-10:] if kind == "PHONE" else digits
    if kind == "BANK":
        return re.sub(r"[^0-9A-Za-z]", "", value).upper()
    return " ".join(value.casefold().split())


@dataclass
class Vault:
    """One run's stand-ins, both ways. Rebuilt from the run's own input at every step (never stored)."""

    by_value: dict[tuple[str, str], str] = field(default_factory=dict)
    by_token: dict[str, tuple[str, str]] = field(default_factory=dict)  # "[CARD_1]" → (kind, the real value)
    counts: Counter[str] = field(default_factory=Counter)
    seen: dict[str, str] = field(
        default_factory=dict
    )  # each value as written → its kind (repeats are hidden too)

    def token(self, kind: str, value: str) -> str:
        key = (kind, identity(kind, value))
        got = self.by_value.get(key)
        if got is None:
            self.counts[kind] += 1
            n = self.counts[kind]
            got = f"[{kind}_{_letters(n) if kind in LETTERED else n}]"
            self.by_value[key] = got
            self.by_token[got] = (kind, value)
        self.seen.setdefault(value, kind)
        return got

    def __bool__(self) -> bool:
        return bool(self.by_token)

    def tally(self) -> dict[str, int]:
        """How many distinct values of each kind were hidden (the receipt; never a value)."""
        return {k: n for k, n in self.counts.items() if n}


# ---------- visible ----------


def _norm(text: str) -> str:
    return " ".join(text.casefold().split())


@dataclass(frozen=True)
class Visible:
    """What the user keeps visible to the AI: whole values, and for names any of their words ("Dana" in "Dana Smith").
    Never a hard secret."""

    whole: frozenset[str] = frozenset()
    words: frozenset[str] = frozenset()
    phones: frozenset[str] = frozenset()

    @classmethod
    def of(cls, entries: Iterable[str]) -> Visible:
        whole, words, phones = set(), set(), set()
        for e in entries:
            n = _norm(str(e))
            if not n:
                continue
            whole.add(n)
            digits = re.sub(r"\D", "", n)
            if len(digits) >= 7 and len(digits) >= len(n.replace(" ", "")) - 4:
                phones.add(digits[-10:])
            if "@" not in n and not any(c.isdigit() for c in n):
                words.update(w for w in re.split(r"[\s-]+", n) if len(w) >= 2)
        return cls(frozenset(whole), frozenset(words), frozenset(phones))

    def shows(self, kind: str, value: str) -> bool:
        if kind in HARD:
            return False
        n = _norm(value)
        if n in self.whole:
            return True
        if kind == "PHONE":
            return re.sub(r"\D", "", value)[-10:] in self.phones
        if kind in NAMES:
            parts = [w for w in re.split(r"[\s-]+", n) if w]
            return bool(parts) and all(w in self.words for w in parts)
        if kind == "ADDRESS":
            return any(len(w) >= 6 and w in n for w in self.whole)
        return False


# ---------- masking ----------

_CACHE_MAX = 4096
_CACHE: OrderedDict[tuple[str, bytes], tuple[tuple[int, int, str], ...]] = OrderedDict()
_LOCK = threading.Lock()


def _cached(fp: str, text: str, find: Callable[[], list[Found]]) -> tuple[tuple[int, int, str], ...]:
    """Detection per (settings, text) is kept for later steps (offsets and kinds only, under a hash of the text): each
    step resends the whole run, and only its new items need reading."""
    key = (fp, hashlib.sha256(text.encode("utf-8", "surrogatepass")).digest())
    with _LOCK:
        hit = _CACHE.get(key)
        if hit is not None:
            _CACHE.move_to_end(key)
            return hit
    got = tuple((f.start, f.end, f.kind) for f in find())
    with _LOCK:
        _CACHE[key] = got
        while len(_CACHE) > _CACHE_MAX:
            _CACHE.popitem(last=False)
    return got


def clear_cache() -> None:
    with _LOCK:
        _CACHE.clear()


def defuse(text: str) -> str:
    """Anything shaped like a stand-in in incoming text (any brackets, case, fullwidth letters) loses its underscore,
    so the unmasker never reads it as one: "[CARD_1]" → "[CARD-1]". Offsets don't move."""
    folded = text.translate(_FULLWIDTH)
    if not _FORGED.search(folded):
        return text
    chars = list(text)
    for m in _FORGED.finditer(folded):
        for i in range(m.end(1), m.start(2)):
            if folded[i] in "_‗":
                chars[i] = "-"
    return "".join(chars)


class Shield:
    """One user's Shield for one run: what it hides (`kinds`), what stays visible, the names it knows, and whether
    pictures stay with the user (`keep_pictures`: Settings › Atlas Shield › Pictures; Shield can't look inside one)."""

    def __init__(
        self,
        kinds: Iterable[str] = HARD,
        *,
        visible: Iterable[str] = (),
        people: Iterable[str] = (),
        ner: NerFinder | None = None,
        level: str = DEFAULT_LEVEL,
        keep_pictures: bool = False,
    ) -> None:
        self.kinds = frozenset(kinds) | frozenset(HARD)
        self.level = level
        self.keep_pictures = keep_pictures
        self.visible = Visible.of(visible)
        known = [p for p in people if not self.visible.shows("PERSON", str(p))][:500]
        self._people = People(known) if "PERSON" in self.kinds else None
        names = self._people.names() if self._people else []
        self._ner = ner if self.kinds & set(NAMES) else None
        fp = json.dumps(
            [sorted(self.kinds), sorted(self.visible.whole), names, ner is not None], ensure_ascii=False
        )
        self.fingerprint = hashlib.sha256(fp.encode()).hexdigest()[:24]

    def find(self, text: str, *, ids: bool = True, key: str = "") -> list[Found]:
        """The finds this Shield hides in `text` (cached), visible values left out. `key`: the field of a tool's JSON
        result the text is the value of; a field named like a secret ("password", "pin", "ssn", "routing_number"...)
        counts as the words before it ("password: <value>"), so a bare value in such a field is found too."""
        lead = _lead(key)
        whole = lead + text

        def go() -> list[Found]:
            got = detect(whole, self.kinds, people=self._people, ner=self._ner, ids=ids)
            n = len(lead)
            return [Found(max(f.start, n) - n, f.end - n, f.kind) for f in got if f.end > n]

        spans = _cached(f"{self.fingerprint}:{int(ids)}:{lead}", text, go)
        found = [Found(a, b, k) for a, b, k in spans if not self.visible.shows(k, text[a:b])]
        return found + self._collab_floor(text, found, ids)

    def _collab_floor(self, text: str, found: list[Found], ids: bool) -> list[Found]:
        """Contact details inside a `collab` fence that this Shield's level would leave visible (COLLAB_FLOOR), never
        overlapping what it already hides. The always-visible list doesn't apply: it is the reader's, not the
        writer's."""
        extra_kinds = COLLAB_FLOOR - self.kinds
        if not extra_kinds or "<untrusted_collab>" not in text:
            return []
        out: list[Found] = []
        for m in _COLLAB.finditer(text):
            at = m.start(1)
            for f in detect(m.group(1), extra_kinds, ids=ids):
                g = Found(at + f.start, at + f.end, f.kind)
                if not any(g.start < h.end and h.start < g.end for h in (*found, *out)):
                    out.append(g)
        return out

    def mask_text(self, text: str, vault: Vault, *, ids: bool = True, key: str = "") -> str:
        """`text` with each hidden value as its stand-in (fail closed: FAILED_NOTE when detection fails)."""
        if not text:
            return text
        try:
            text = defuse(text)
            found = self.find(text, ids=ids, key=key)
            found = sorted([*found, *_repeats(text, found, vault)], key=lambda f: f.start)
        except Exception as e:  # noqa: BLE001 - class only; a text nobody could check never reaches the model
            log.warning("shield: text not checked (%s)", type(e).__name__)
            return FAILED_NOTE
        if not found:
            return text
        parts, at = [], 0
        for f in found:
            parts += [text[at : f.start], vault.token(f.kind, text[f.start : f.end])]
            at = f.end
        parts.append(text[at:])
        return "".join(parts)

    def mask_json(self, raw: str, vault: Vault, *, fields: bool = True) -> str:
        """A JSON text (a tool's output, a call's arguments) masked value by value: offsets never cut an escape, an id
        or link field never counts as a key, and (`fields`, a tool's output) a field named like a secret counts as its
        words; JSON that doesn't parse is masked as plain text. The same bytes when nothing is hidden."""
        try:
            data = json.loads(raw)
        except (ValueError, RecursionError):
            return self.mask_text(raw, vault)
        changed = [False]

        def one(v: str, key: str) -> str:
            out = self.mask_text(v, vault, ids=not _ID_KEY.search(key), key=_field(key, fields))
            changed[0] = changed[0] or out != v
            return out

        out = _walk(data, one)
        return json.dumps(out, default=str) if changed[0] else raw

    def mask_items(
        self, items: Sequence[Mapping[str, Any]], vault: Vault | None = None
    ) -> tuple[list[dict[str, Any]], Vault]:
        """A model call's input items masked in order (the vault numbers values by first sight): message text and
        text parts, calls' arguments and tools' outputs. Pictures pass as they are (Shield can't look inside one),
        unless the user keeps them from the AI (`keep_pictures`): then each becomes PICTURE_KEPT."""
        vault = vault if vault is not None else Vault()
        out: list[dict[str, Any]] = []
        for item in items:
            it = dict(item)
            kind = it.get("type")
            if kind == "function_call":  # the model's own words: only what it saw, so no field words
                it["arguments"] = self.mask_json(str(it.get("arguments") or "{}"), vault, fields=False)
            elif kind == "function_call_output":
                it["output"] = self.mask_json(str(it.get("output") or ""), vault)
            elif isinstance(it.get("content"), str):
                it["content"] = self.mask_text(it["content"], vault)
            elif isinstance(it.get("content"), list):
                parts = []
                for p in it["content"]:
                    if isinstance(p, dict) and isinstance(p.get("text"), str):
                        p = {**p, "text": self.mask_text(p["text"], vault)}
                    elif self.keep_pictures and _picture(p):
                        p = {"type": "input_text", "text": PICTURE_KEPT}
                    parts.append(p)
                it["content"] = parts
            out.append(it)
        return out, vault

    def leaks(self, items: Sequence[Mapping[str, Any]]) -> list[str]:
        """Belt and braces before a model call, apart from the masking: the kinds of any hard secret still in the
        input (should be none), and PICTURE for each picture still anywhere in it when the user keeps pictures from
        the AI. Every text an item carries is read with the hard rules, JSON value by value with its field's words, so
        an item that skipped masking (a new kind of item, a bug) is caught here."""
        seen: list[str] = []
        for text, key, fields in _texts(items):
            lead = _lead(_field(key, fields))
            ids = not _ID_KEY.search(key)
            seen += [
                f.kind for f in detect(lead + text, HARD, ids=ids) if f.end > len(lead) and f.kind in HARD
            ]
        if self.keep_pictures:
            seen += ["PICTURE"] * pictures(items)
        return seen


# a JSON field named like a secret, and the words its value counts as following ("password: <value>")
_SECRET_KEYS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"(?i)(?:^|[_ -])(?:password|passwd|pwd|pw|passphrase)$"), "password: "),
    (
        re.compile(r"(?i)(?:^|[_ -])(?:pin|otp|passcode|verification[_ -]?code|one[_ -]?time[_ -]?code)$"),
        "otp: ",
    ),
    (
        re.compile(
            r"(?i)(?:^|[_ -])(?:secret|token|api[_ -]?key|access[_ -]?key|private[_ -]?key|authorization|bearer)$"
        ),
        "api key: ",
    ),
    (re.compile(r"(?i)(?:^|[_ -])(?:ssn|social[_ -]?security(?:[_ -]?number)?|tin)$"), "ssn "),
    (re.compile(r"(?i)(?:^|[_ -])(?:routing(?:[_ -]?number)?|aba)$"), "routing number "),
    (
        re.compile(r"(?i)(?:^|[_ -])(?:account[_ -]?(?:number|no|num)|acct(?:[_ -]?(?:number|no))?)$"),
        "account number ",
    ),
    (re.compile(r"(?i)(?:^|[_ -])(?:cvv2?|cvc2?|csc)$"), "cvv "),
)


def _field(key: str, fields: bool) -> str:
    """The field name that may lend its words: a tool result's, never an id's or Atlas's own handle's."""
    return key if fields and key and not _ID_KEY.search(key) else ""


def _repeats(text: str, found: Sequence[Found], vault: Vault) -> list[Found]:
    """Values hidden earlier in the run (or found in this text) wherever they appear again in `text`, as written: the
    run keeps real values, so "I won't share <the password>" holds no word that says "password". Whole words only, 3
    characters or more, never overlapping a find."""
    known = dict(vault.seen)
    for f in found:
        known.setdefault(text[f.start : f.end], f.kind)
    known = {v: k for v, k in known.items() if len(v) >= 3 and v in text}
    if not known:
        return []
    alts = "|".join(re.escape(v) for v in sorted(known, key=len, reverse=True))
    taken = [(f.start, f.end) for f in found]
    out: list[Found] = []
    for m in re.finditer(rf"(?<![A-Za-z0-9])(?:{alts})(?![A-Za-z0-9])", text):
        if not any(m.start() < b and a < m.end() for a, b in taken):
            out.append(Found(m.start(), m.end(), known[m.group(0)]))
            taken.append((m.start(), m.end()))
    return out


def _lead(key: str) -> str:
    if not key:
        return ""
    for pat, words in _SECRET_KEYS:
        if pat.search(key):
            return words
    return ""


def _walk(data: Any, fn: Callable[[str, str], str], key: str = "") -> Any:
    """`data` with fn(text, its field's name) applied to every string value (keys stay as they are). A header given as
    a name and a value (Gmail's {"name": "Message-ID", "value": "<...>"}) whose name is an id's (_ID_KEY) is that
    field: its value is read as the header, not as "value"."""
    if isinstance(data, str):
        return fn(data, key)
    if isinstance(data, list):
        return [_walk(x, fn, key) for x in data]
    if isinstance(data, dict):
        name = data.get("name")
        head = name if isinstance(name, str) and "value" in data and _ID_KEY.search(name) else None
        return {
            k: _walk(x, fn, head if head is not None and k == "value" else str(k)) for k, x in data.items()
        }
    return data


def _picture(part: Any) -> bool:
    return isinstance(part, Mapping) and part.get("type") == "input_image"


def pictures(data: Any) -> int:
    """How many pictures (input_image parts) `data` carries anywhere: a turn's parts, the items of a list prompt, a
    part nested deeper by a new kind of item."""
    if isinstance(data, Mapping):
        return int(_picture(data)) + sum(pictures(v) for v in data.values())
    if isinstance(data, list | tuple):
        return sum(pictures(v) for v in data)
    return 0


def _texts(items: Sequence[Mapping[str, Any]]) -> Iterable[tuple[str, str, bool]]:
    """Every text a model call's items carry, with its JSON field's name ("" outside JSON) and whether that field may
    lend its words (a tool's result, as masking reads it)."""

    def json_texts(raw: str, fields: bool) -> Iterable[tuple[str, str, bool]]:
        try:
            data = json.loads(raw)
        except (ValueError, RecursionError):
            yield raw, "", False
            return
        got: list[tuple[str, str, bool]] = []
        _walk(data, lambda t, k: got.append((t, k, fields)) or t)
        yield from got

    for it in items:
        if it.get("type") == "function_call":
            yield from json_texts(str(it.get("arguments") or ""), False)
        elif it.get("type") == "function_call_output":
            yield from json_texts(str(it.get("output") or ""), True)
        elif isinstance(it.get("content"), str):
            yield it["content"], "", False
        elif isinstance(it.get("content"), list):
            for p in it["content"]:
                if isinstance(p, dict) and isinstance(p.get("text"), str):
                    yield p["text"], "", False


# ---------- unmasking ----------

# Links in what the user sees (P2-309). A model told by outside text can write a stand-in into a link
# ("[Check your card](https://site.example/?c=[CARD_1])"); put back, one tap would send the real value to that site.
# So in words shown to the user a value is judged by the word it lands in (what lies between two spaces, as the chat
# reads a link's address: web/src/lib/markdown.ts): inside a link or a web address a hard secret always stays its
# stand-in, and a contact detail or name stays one unless the link goes to one of the sites Atlas itself sends people
# to with their details (LINK_HOSTS: a map search, a calendar invite, a mail draft) or opens the user's own mail,
# phone or messages app. Plain words get every value back, as before.
#
# What ends a word: JavaScript's \s, the characters that end a link's address in the chat (never more, or a value after
# one the chat keeps inside a link would come back).
_SPACE = frozenset(
    "\t\n\v\f\r \u00a0\u1680\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a\u2028\u2029\u202f"
    "\u205f\u3000\ufeff"
)
WORD_MAX = 4096
# a word cut to its last WORD_MAX characters starts with this, and counts as a link (fail closed)
_CUT = "\x00"
# where a web address starts inside a word: any scheme://, //site, www., a scheme that opens an app or runs (mailto:,
# tel:, sms:, data:, javascript:...), a site with a path (site.example/...)
_SITE = re.compile(
    r"(?i)[a-z][a-z0-9+.-]*://|(?<![:/])//[a-z0-9]|www\.|\b(?:mailto|tel|sms|data|javascript|vbscript|file|intent):"
    r"|\b[a-z0-9-]+(?:\.[a-z0-9-]+)*\.[a-z]{2,}[/?#]"
)
_TARGET = re.compile(r"(?i)\]\(|\b(?:href|src)\s*=")  # a Markdown link's address, an HTML link's
_APP_LINK = re.compile(r"(?i)(?:mailto|tel|sms):")
_WEB = re.compile(r"(?i)https?://")
# The sites Atlas sends people to with their own contact details and names, and the paths there (one ending in "/" is a
# start; "/" alone is the site's front page): only in a link to one of these does a hidden email, phone, address or
# name come back. Never a hard secret, whatever the site.
LINK_HOSTS: dict[str, tuple[str, ...]] = {
    "www.google.com": ("/maps", "/maps/"),
    "google.com": ("/maps", "/maps/"),
    "maps.google.com": ("/", "/maps", "/maps/"),
    "maps.apple.com": ("/",),
    "calendar.google.com": ("/calendar/",),
    "mail.google.com": ("/mail/",),
}


def _after(word: str, piece: str, nxt: str | None = None) -> str:
    """The word in progress once `piece` follows `word`: what follows the last space, at most WORD_MAX characters.
    Spaces or tabs next to an em dash end no word: house style drops the dash with them ("x \u2014." becomes "x.",
    lifeos.style), joining the words on each side; nor do ones at the very end while what follows is unknown (`nxt`
    None: a dash may come next)."""
    s = word + piece
    i = len(s) - 1
    while i >= 0:
        if s[i] in _SPACE:
            if s[i] not in " \t":
                break
            j = i
            while j >= 0 and s[j] in " \t":
                j -= 1
            follows = s[i + 1] if i + 1 < len(s) else nxt
            if follows is not None and follows != "\u2014" and (j < 0 or s[j] != "\u2014"):
                break
            i = j
            continue
        i -= 1
    word = s[i + 1 :]
    if len(word) > WORD_MAX or word.startswith(_CUT):
        word = _CUT + word.lstrip(_CUT)[-WORD_MAX:]
    return word


def _site_ok(url: str) -> bool:
    """Whether a link that starts as `url` (cut where a stand-in sits) opens the user's own mail, phone or messages app,
    or goes to LINK_HOSTS. A host not written out in full yet (the stand-in is part of it) is never one."""
    if _APP_LINK.match(url):
        return True
    m = _WEB.match(url)
    rest = url[m.end() :] if m else url.removeprefix("//")
    if m is None and re.match(r"(?i)[a-z][a-z0-9+.-]*:", rest):
        return False  # another scheme (ftp:, data:, javascript:...)
    end = next((i for i, c in enumerate(rest) if c in "/?#"), -1)
    if end < 0:
        return False
    host = rest[:end].rsplit("@", 1)[-1].split(":", 1)[0].rstrip(".").lower()
    path = rest[end:].split("?", 1)[0].split("#", 1)[0] or "/"
    if "/." in path or "%2e" in path.lower() or "\\" in path:
        return False  # a browser would read another path ("/maps/../url" is "/url")
    return any(
        path == p or (len(p) > 1 and p.endswith("/") and path.startswith(p)) for p in LINK_HOSTS.get(host, ())
    )


def _may_show(kind: str, word: str) -> bool:
    """Whether a value of `kind` may come back where `word` (the word so far, up to its stand-in) ends."""
    if word.startswith(_CUT):
        return False
    sites = list(_SITE.finditer(word))
    if not sites and not _TARGET.search(word):
        return True  # plain words
    if kind in HARD:
        return False
    return bool(sites) and all(_site_ok(word[s.start() :]) for s in sites)


def _shown(text: str, vault: Vault, *, hard: bool, escape: bool, before: str) -> tuple[str, str]:
    """`text` with stand-ins back as the user sees it (unmask_text `shown`), and the word in progress after it."""
    if not vault or "[" not in text:
        return text, _after(before, text)
    out: list[str] = []
    word, at = before, 0
    for m in _TOKEN.finditer(text):
        piece = text[at : m.start()]
        word = _after(word, piece, "[")
        got = vault.by_token.get(m.group(0))
        if got is None or (not hard and got[0] in HARD) or not _may_show(got[0], word):
            back = m.group(0)
        else:
            back = json.dumps(got[1], ensure_ascii=False)[1:-1] if escape else got[1]
        out += [piece, back]
        word = _after(word, back, text[m.end()] if m.end() < len(text) else None)
        at = m.end()
    out.append(text[at:])
    return "".join(out), _after(word, text[at:])


def unmask_text(
    text: str, vault: Vault, *, hard: bool = True, escape: bool = False, shown: bool = False
) -> str:
    """Stand-ins back to real values. `hard` False leaves hard secrets as their stand-ins. `escape`: the text is JSON,
    so each value goes in JSON-escaped (a password with a quote keeps the JSON whole). `shown`: the text is shown to
    the user (the chat's words, a question, blocks, an app's answer), so no value comes back inside a link (P2-309:
    a hard secret never; a contact detail or name only in a link to LINK_HOSTS). A stand-in the vault doesn't hold (the
    model made it up) stays as written: never a guess, never another run's value."""
    if not vault or "[" not in text:
        return text
    if shown:
        return _shown(text, vault, hard=hard, escape=escape, before="")[0]

    def swap(m: re.Match[str]) -> str:
        got = vault.by_token.get(m.group(0))
        if got is None or (not hard and got[0] in HARD):
            return m.group(0)
        return json.dumps(got[1], ensure_ascii=False)[1:-1] if escape else got[1]

    return _TOKEN.sub(swap, text)


def hidden(text: str, vault: Vault) -> str:
    """The vault's hard-secret stand-ins in `text` as plain words ("[CARD_1]" → "[hidden card]"), for answers that
    become something kept (an agent the builder wrote): RULES S8, no card, bank number, SSN, password, key or code is
    ever stored, and a live stand-in in a saved agent could later read as another run's value. Other stand-ins stay."""
    if not vault or "[" not in text:
        return text

    def swap(m: re.Match[str]) -> str:
        got = vault.by_token.get(m.group(0))
        return f"[hidden {WORDS[got[0]]}]" if got is not None and got[0] in HARD else m.group(0)

    return _TOKEN.sub(swap, text)


def named(text: str, vault: Vault) -> set[str]:
    """The kinds of the vault's stand-ins `text` names."""
    return {vault.by_token[m.group(0)][0] for m in _TOKEN.finditer(text) if m.group(0) in vault.by_token}


def unmask_args(raw: Any, vault: Vault, *, show: bool, record: bool = False) -> tuple[str, frozenset[str]]:
    """A model's call arguments (JSON text) with real values, and the hard kinds they named. `show`: the call only
    shows its arguments to the user (`present`, a question), so hard secrets come back too, though never inside a link
    (unmask_text `shown`, value by value); otherwise they stay stand-ins (and the gate refuses the call: policy.gate
    `hidden`). `record` (with `show`): not shown but kept or read as what the model meant (the run's own log, the
    builder's safety judge), so every value comes back, links too. The same text when it names no stand-in."""
    text = str(raw if raw is not None else "{}")
    if not vault or "[" not in text:
        return text, frozenset()
    shown = show and not record
    try:
        data = json.loads(text)
    except (ValueError, RecursionError):
        hard = frozenset(k for k in named(text, vault) if k in HARD)
        return unmask_text(text, vault, hard=show, escape=True, shown=shown), hard
    hidden: set[str] = set()

    def one(v: str, _key: str) -> str:
        hidden.update(k for k in named(v, vault) if k in HARD)
        return unmask_text(v, vault, hard=show, shown=shown)

    out = _walk(data, one)
    if out == data:
        return text, frozenset(hidden)
    return json.dumps(out, ensure_ascii=False), frozenset(hidden)


class StreamUnmask:
    """The streamed words back to real values as they arrive, as the user sees them (unmask_text `shown`: no value
    inside a link): a stand-in cut between two pieces ("[CAR", "D_1]") is held until it is whole (at most TOKEN_MAX
    characters), so no half stand-in ever shows. `word` is the word in progress, so a link that began in an earlier
    piece still counts; `before`: the words shown just before these (the run's earlier step)."""

    def __init__(self, vault: Vault, *, before: str = "") -> None:
        self.vault = vault
        self.held = ""
        self.word = _after("", before)

    def feed(self, delta: str) -> str:
        text = self.held + delta
        self.held = ""
        i = text.rfind("[")
        if i != -1 and len(text) - i < TOKEN_MAX and _PARTIAL.fullmatch(text[i:]):
            self.held, text = text[i:], text[:i]
        return self._show(text)

    def flush(self) -> str:
        text, self.held = self.held, ""
        return self._show(text)

    def _show(self, text: str) -> str:
        out, self.word = _shown(text, self.vault, hard=True, escape=False, before=self.word)
        return out


def looks_json(text: str) -> bool:
    """A model's answer that is (or wraps) JSON: values going back in are JSON-escaped (llm.complete)."""
    return text.lstrip().startswith(("{", "[", "```"))


class Request:
    """One model request Atlas writes from its own fixed words and pieces of the user's (the Agent Builder: a
    description, the user's page and app names, an agent as it stands, an ask, an answer; a creature's description).
    `text` / `json` give one of the user's pieces as the model may see it, masked with this request's vault in the order
    they are read; Atlas's own words (menus, instructions) are never masked, so Strict never mistakes a menu word for a
    name. `ready` checks the whole input before it goes (Stopped when a hard secret is still in it: fail closed) and
    adds BUILDER_NOTE after it when anything was hidden. `answer` puts the real contact details and names into the
    model's answer (JSON-escaped: these answers are JSON); a hidden hard secret comes back only as words (`hidden`).
    Without a Shield (the switch is off for this user) each hands back what it was given."""

    def __init__(self, guard: Shield | None) -> None:
        self.guard, self.vault = guard, Vault()

    def text(self, text: str) -> str:
        return self.guard.mask_text(text, self.vault) if self.guard is not None else text

    def json(self, raw: str) -> str:
        """A JSON piece (an agent as it stands) masked value by value; field names never lend their words."""
        return self.guard.mask_json(raw, self.vault, fields=False) if self.guard is not None else raw

    def ready(self, body: dict[str, Any]) -> dict[str, Any]:
        if self.guard is None:
            return body
        given = body.get("input")
        items = given if isinstance(given, list) else [{"role": "user", "content": str(given or "")}]
        try:
            leaked = self.guard.leaks(items)
        except Exception as e:  # noqa: BLE001 - class only; a request nobody could check never goes (fail closed)
            log.warning("shield: a request was not checked (%s)", type(e).__name__)
            raise Stopped(["CHECK"]) from None
        if leaked:
            log.warning("shield: a request was stopped, %s left in it", ",".join(sorted(set(leaked))))
            raise Stopped(leaked)
        if not self.vault:
            return body
        if isinstance(given, list):
            return {**body, "input": [*given, {"role": "developer", "content": BUILDER_NOTE}]}
        return {**body, "input": f"{given}\n\n{BUILDER_NOTE}"}

    def answer(self, data: dict[str, Any]) -> dict[str, Any]:
        if self.guard is None or not self.vault:
            return data

        def back(text: str) -> str:
            return hidden(unmask_text(text, self.vault, hard=False, escape=looks_json(text)), self.vault)

        items = []
        for item in data.get("output") or []:
            if isinstance(item, dict) and item.get("type") == "message":
                parts = [{**p, "text": back(p["text"])} if isinstance(p, dict) and p.get("type") == "output_text"
                         and isinstance(p.get("text"), str) else p for p in item.get("content") or []]  # fmt: skip
                item = {**item, "content": parts}
            items.append(item)
        return {**data, "output": items}


# ---------- receipt words ----------


def receipt(tally: Mapping[str, int]) -> str:
    """ "1 card, 2 codes": what the AI never saw (counts only), hard secrets first; empty when nothing was hidden."""
    parts = []
    for kind in KINDS:
        n = int(tally.get(kind, 0))
        if n > 0:
            word = WORDS[kind]
            if n != 1:
                word = (
                    "companies" if word == "company" else ("addresses" if word == "address" else f"{word}s")
                )
            parts.append(f"{n} {word}")
    return ", ".join(parts)


_RELATION = re.compile(
    r"(?i:(?:my|our)\s+)?(?i:sister|brother|mom|mum|mother|dad|father|aunt|uncle|cousin|grandma|grandpa|wife|husband|"
    r"partner|son|daughter|boss|manager|friend|colleague|coworker|roommate|landlord|neighbou?r|doctor|professor|coach|"
    r"mentor)\s+(?=[A-Z])"
)


def person_name(text: str) -> str:
    """The name a People fact starts with ("Dana Smith is my manager" → "Dana Smith"; "Sister Meera's birthday" →
    "Meera"): up to three capitalised words after an optional relation word, or "" when it doesn't start with one."""
    text = unicodedata.normalize("NFC", text or "").lstrip()
    lead = _RELATION.match(text)
    m = re.match(rf"({_NAME})", text[lead.end() :] if lead else text)
    if not m or not _name_ok(m.group(1)):
        return ""
    return m.group(1).split("'")[0].split("’")[0].strip()
