import importlib

import pytest

from hardware_monitor_widget import i18n


def test_languages_and_apply():
    assert set(i18n.LANGUAGES) == {"es", "en"}
    i18n.apply_language_setting("en")
    assert i18n.get_language() == "en" and i18n.tr("hw_vram") == "Virtual memory"
    i18n.apply_language_setting("es")
    assert i18n.tr("hw_vram") == "Memoria virtual"
    i18n.apply_language_setting("auto")
    assert i18n.get_language() in ("es", "en")
    i18n.apply_language_setting("xx")
    assert i18n.get_language() in ("es", "en")


def test_base_parity():
    assert set(i18n._STRINGS["es"]) == set(i18n._STRINGS["en"])


def test_curation_keys_exist():
    from hardware_monitor_widget.curation_rules import GROUP_ORDER
    for key in [f"grp_{g}" for g in GROUP_ORDER] + ["hw_vram", "hw_dimms", "kind_timing"]:
        for lang in ("es", "en"):
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


def test_placeholders_match_between_languages():
    import string
    f = string.Formatter()
    for key, es in i18n._STRINGS["es"].items():
        en = i18n._STRINGS["en"][key]
        names = lambda t: {n for _l, n, _s, _c in f.parse(t) if n}  # noqa: E731
        assert names(es) == names(en), key
