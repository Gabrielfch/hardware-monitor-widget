import importlib
import string

import pytest

from hardware_monitor_widget import i18n


ALL_LANGS = ("en", "es", "pt", "fr", "ru", "zh", "ja")


def test_languages_and_apply():
    assert set(i18n.LANGUAGES) == set(ALL_LANGS)
    i18n.apply_language_setting("en")
    assert i18n.get_language() == "en" and i18n.tr("hw_vram") == "Virtual memory"
    i18n.apply_language_setting("es")
    assert i18n.tr("hw_vram") == "Memoria virtual"
    i18n.apply_language_setting("auto")
    assert i18n.get_language() in ALL_LANGS
    i18n.apply_language_setting("xx")
    assert i18n.get_language() in ALL_LANGS
    for lang in ALL_LANGS:
        i18n.apply_language_setting(lang)
        assert i18n.get_language() == lang


def test_base_parity():
    for lang in ALL_LANGS:
        assert set(i18n._STRINGS[lang]) == set(i18n._STRINGS["en"]), lang


def test_curation_keys_exist():
    from hardware_monitor_widget.curation_rules import GROUP_ORDER
    for key in [f"grp_{g}" for g in GROUP_ORDER] + ["hw_vram", "hw_dimms", "kind_timing"]:
        for lang in ALL_LANGS:
            assert key in i18n._STRINGS[lang], (lang, key)


def _present_modules():
    out = []
    for name in i18n.EXTENSION_MODULES:
        try:
            out.append(importlib.import_module(f"hardware_monitor_widget.{name}"))
        except ImportError:
            pass
    return out


def test_extension_modules_parity_and_merged():
    for mod in _present_modules():
        table = mod.STRINGS
        assert set(table) == {"es", "en"}, mod.__name__
        assert set(table["es"]) == set(table["en"]), mod.__name__
        for lang in ("es", "en"):
            for key, text in table[lang].items():
                assert key in i18n._STRINGS[lang], (mod.__name__, key)
                assert i18n._STRINGS[lang][key] == text or any(
                    m.STRINGS[lang].get(key) == i18n._STRINGS[lang][key] for m in _present_modules())


def test_translation_modules_cover_every_key():
    """Each machine-translated language has exactly the keys of English (all strings_*.py modules)."""
    expected = set(i18n._STRINGS["en"])
    for name, code in zip(i18n.TRANSLATION_MODULES, i18n.MACHINE_TRANSLATED):
        mod = importlib.import_module(f"hardware_monitor_widget.{name}")
        assert set(mod.STRINGS) == {code}, name
        assert set(mod.STRINGS[code]) == expected, (code, expected ^ set(mod.STRINGS[code]))
        assert all(isinstance(v, str) and v.strip() for v in mod.STRINGS[code].values()), code


def test_placeholders_match_between_languages():
    f = string.Formatter()

    def names(t):
        return sorted(n for _l, n, _s, _c in f.parse(t) if n)

    for lang in ALL_LANGS:
        for key, en in i18n._STRINGS["en"].items():
            assert names(i18n._STRINGS[lang][key]) == names(en), (lang, key)


def test_every_text_formats_with_its_placeholders():
    f = string.Formatter()
    for lang in ALL_LANGS:
        for key, text in i18n._STRINGS[lang].items():
            kw = {n: "1" for _l, n, _s, _c in f.parse(text) if n}
            assert text.format(**kw)


@pytest.mark.parametrize("loc,expected", [
    ("pt-BR", "pt"), ("pt-PT", "pt"), ("fr-FR", "fr"), ("fr_CA", "fr"), ("ru-RU", "ru"),
    ("zh-CN", "zh"), ("zh-Hans-CN", "zh"), ("zh-TW", "zh"), ("ja-JP", "ja"), ("es-ES", "es"),
    ("en-US", "en"), ("de-DE", "en"), ("", "en"),
])
def test_language_from_locale(loc, expected):
    assert i18n.language_from_locale(loc) == expected


def test_decimal_separator_per_language():
    for lang, sep in (("en", "."), ("es", ","), ("pt", ","), ("fr", ","), ("ru", ","),
                      ("zh", "."), ("ja", ".")):
        i18n.set_language(lang)
        assert i18n.decimal_sep() == sep, lang
