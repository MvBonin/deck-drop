"""Translation catalogs: every language has every key, with the same placeholders.

Backend: deckdrop/core/i18n.py. Web UI: frontend/i18n/<lang>.js.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from deckdrop.core import i18n

FRONTEND_I18N = Path(__file__).parent.parent / "frontend" / "i18n"
_PLACEHOLDER = re.compile(r"\{(\w+)\}")
_JS_ENTRY = re.compile(r"^\s*'([\w.]+)':\s*'((?:[^'\\]|\\.)*)',\s*$", re.M)


def _js_catalog(lang: str) -> dict[str, str]:
    return dict(_JS_ENTRY.findall((FRONTEND_I18N / f"{lang}.js").read_text(encoding="utf-8")))


def _frontend_languages() -> list[str]:
    index = (FRONTEND_I18N / "index.js").read_text(encoding="utf-8")
    return re.findall(r"\{ code: '(\w+)'", index)


def _assert_same(base: dict[str, str], other: dict[str, str], lang: str) -> None:
    assert set(other) == set(base), (
        f"{lang}: missing {sorted(set(base) - set(other))}, extra {sorted(set(other) - set(base))}"
    )
    for key, text in base.items():
        assert set(_PLACEHOLDER.findall(other[key])) == set(_PLACEHOLDER.findall(text)), (
            f"{lang}: placeholders differ for {key!r}"
        )


@pytest.mark.parametrize("lang", [x for x in i18n.SUPPORTED_LANGUAGES if x != "en"])
def test_backend_catalog_complete(lang):
    _assert_same(i18n.MESSAGES["en"], i18n.MESSAGES[lang], lang)


def test_frontend_and_backend_offer_the_same_languages():
    assert _frontend_languages() == list(i18n.SUPPORTED_LANGUAGES)


@pytest.mark.parametrize("lang", [x for x in i18n.SUPPORTED_LANGUAGES if x != "en"])
def test_frontend_catalog_complete(lang):
    en = _js_catalog("en")
    assert len(en) > 100  # the regex really parsed the file
    _assert_same(en, _js_catalog(lang), lang)


def test_t_interpolates_and_falls_back():
    assert i18n.t("err.game_busy", lang="de", state="hashing") == (
        "Spiel ist gerade beschäftigt (hashing)"
    )
    assert i18n.t("err.game_not_found", lang="xx") == "Game not found"
    assert i18n.t("no.such.key") == "no.such.key"


def test_set_language_ignores_unknown():
    i18n.set_language("de")
    assert i18n.get_language() == "de"
    i18n.set_language("xx")
    assert i18n.get_language() == "en"


def test_matches_recognises_messages_in_every_language():
    for lang in i18n.SUPPORTED_LANGUAGES:
        msg = i18n.t("transfer.no_host_connection", lang=lang, address="10.0.0.2")
        assert i18n.matches(msg, "transfer.no_host_connection")
        assert not i18n.matches(msg, "transfer.midstall")
