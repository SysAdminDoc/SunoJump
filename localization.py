"""Qt-compatible JSON localization with fallback, plurals, and pseudo locale."""
from __future__ import annotations

import json
import os
from pathlib import Path
import re

from PyQt6.QtCore import QCoreApplication, QLocale, QTranslator, Qt


CATALOG_SCHEMA_VERSION = 1
CATALOG_DIR = Path(__file__).resolve().parent / "locales"
DEFAULT_LOCALE = "en"
PSEUDO_LOCALE = "qps-ploc"
LOCALE_ENV = "SUNOJUMP_LOCALE"
_PLACEHOLDER = re.compile(r"(\{[^{}]+\})")
_PSEUDO_MAP = str.maketrans({
    "a": "á", "b": "ƀ", "c": "ç", "d": "ď", "e": "é",
    "f": "ƒ", "g": "ğ", "h": "ħ", "i": "í", "j": "ĵ",
    "k": "ķ", "l": "ľ", "m": "ɱ", "n": "ñ", "o": "ó",
    "p": "þ", "q": "ʠ", "r": "ŕ", "s": "š", "t": "ţ",
    "u": "ú", "v": "ṽ", "w": "ŵ", "x": "ẋ", "y": "ý",
    "z": "ž", "A": "Á", "B": "Ɓ", "C": "Ç", "D": "Ď",
    "E": "É", "F": "Ƒ", "G": "Ğ", "H": "Ħ", "I": "Í",
    "J": "Ĵ", "K": "Ķ", "L": "Ľ", "M": "Ṁ", "N": "Ñ",
    "O": "Ó", "P": "Þ", "Q": "Ɋ", "R": "Ŕ", "S": "Š",
    "T": "Ţ", "U": "Ú", "V": "Ṽ", "W": "Ŵ", "X": "Ẋ",
    "Y": "Ý", "Z": "Ž",
})


def _normalized_locale(locale_name: str | None) -> str:
    raw = (locale_name or "").strip().replace("_", "-").lower()
    return raw or DEFAULT_LOCALE


def _catalog_candidates(locale_name: str) -> list[str]:
    normalized = _normalized_locale(locale_name)
    candidates = [normalized]
    if "-" in normalized and normalized != PSEUDO_LOCALE:
        candidates.append(normalized.split("-", 1)[0])
    if DEFAULT_LOCALE not in candidates:
        candidates.append(DEFAULT_LOCALE)
    return candidates


def _load_catalog(locale_name: str) -> dict:
    path = CATALOG_DIR / f"{locale_name}.json"
    if not path.is_file():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema_version") != CATALOG_SCHEMA_VERSION:
        raise ValueError(f"unsupported localization schema in {path.name}")
    return payload


def _pseudo_text(text: str) -> str:
    pieces = []
    for piece in _PLACEHOLDER.split(text):
        if not piece:
            continue
        pieces.append(piece if _PLACEHOLDER.fullmatch(piece) else piece.translate(_PSEUDO_MAP))
    translated = "".join(pieces)
    expansion = " ·" * max(1, len(text) // 12)
    return f"⟦!! {translated}{expansion} !!⟧"


class CatalogTranslator(QTranslator):
    def __init__(self, locale_name: str):
        super().__init__()
        requested = _normalized_locale(locale_name)
        loaded = [_load_catalog(name) for name in reversed(_catalog_candidates(requested))]
        translations = {}
        direction = None
        pseudo = requested == PSEUDO_LOCALE
        resolved = DEFAULT_LOCALE
        for payload in loaded:
            if not payload:
                continue
            translations.update(payload.get("translations", {}))
            direction = payload.get("direction", direction)
            pseudo = bool(payload.get("pseudo", pseudo))
            resolved = payload.get("locale", resolved)
        self.requested_locale = requested
        self.resolved_locale = resolved
        self.translations = translations
        self.pseudo = pseudo
        self.direction = direction or "ltr"

    def translate(
        self,
        _context,
        source_text,
        _disambiguation=None,
        n=-1,
    ):
        value = self.translations.get(source_text, source_text)
        if isinstance(value, dict):
            form = "one" if n == 1 else "other"
            value = value.get(form, value.get("other", source_text))
        text = str(value)
        return _pseudo_text(text) if self.pseudo else text

    @property
    def layout_direction(self):
        return (
            Qt.LayoutDirection.RightToLeft
            if self.direction == "rtl"
            else Qt.LayoutDirection.LeftToRight
        )


_ACTIVE_TRANSLATOR = CatalogTranslator(DEFAULT_LOCALE)
_INSTALLED_TRANSLATOR = None


def requested_locale_from_argv(tokens) -> str | None:
    tokens = list(tokens)
    for index, token in enumerate(tokens):
        if token.startswith("--locale="):
            return token.split("=", 1)[1]
        if token == "--locale" and index + 1 < len(tokens):
            return tokens[index + 1]
    return None


def without_locale_args(tokens) -> list[str]:
    result = []
    skip_next = False
    for token in tokens:
        if skip_next:
            skip_next = False
            continue
        if token == "--locale":
            skip_next = True
            continue
        if token.startswith("--locale="):
            continue
        result.append(token)
    return result


def configure_locale(
    locale_name: str | None = None,
    app: QCoreApplication | None = None,
) -> CatalogTranslator:
    global _ACTIVE_TRANSLATOR, _INSTALLED_TRANSLATOR
    requested = (
        locale_name
        or os.environ.get(LOCALE_ENV)
        or QLocale.system().name()
        or DEFAULT_LOCALE
    )
    translator = CatalogTranslator(requested)
    application = app or QCoreApplication.instance()
    if application is not None:
        if _INSTALLED_TRANSLATOR is not None:
            application.removeTranslator(_INSTALLED_TRANSLATOR)
        application.installTranslator(translator)
        application.setLayoutDirection(translator.layout_direction)
        _INSTALLED_TRANSLATOR = translator
    _ACTIVE_TRANSLATOR = translator
    return translator


def tr(source_text: str, *, n: int = -1, context: str = "SunoJump", **values) -> str:
    application = QCoreApplication.instance()
    if application is not None and _INSTALLED_TRANSLATOR is _ACTIVE_TRANSLATOR:
        translated = QCoreApplication.translate(
            context,
            source_text,
            None,
            n,
        )
    else:
        translated = _ACTIVE_TRANSLATOR.translate(context, source_text, None, n)
    if n >= 0:
        values.setdefault("n", n)
    return translated.format(**values) if values else translated


def active_locale() -> str:
    return _ACTIVE_TRANSLATOR.requested_locale


def active_layout_direction():
    return _ACTIVE_TRANSLATOR.layout_direction

