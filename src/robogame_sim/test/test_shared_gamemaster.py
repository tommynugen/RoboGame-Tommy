"""The shareable Gamemaster: one HTML file, only the editor works."""

import json
import re

from robogame_sim import shared_gamemaster


def test_build_has_every_tab():
    page = shared_gamemaster.build()
    assert not re.search(r'<script src=', page)
    tabs = re.findall(r'<a href="#(\w+)"[^>]*data-tab=', page)
    assert set(tabs) == set(shared_gamemaster.PAGES)
    pages = json.loads(re.search(r"const PAGES = (\{.*?\});\n", page)
                       .group(1).replace("<\\/", "</"))
    assert set(pages) == set(tabs)
    # The editor is the shared editor; every other tab says it is off.
    assert "const SHARED = " in pages["editor"]
    assert "Not usable in the web version" not in pages["editor"]
    for tab in tabs:
        if tab != "editor":
            assert "Not usable in the web version" in pages[tab], tab
            assert '<script src=' not in pages[tab], tab
    # A tag on each off tab, and once in the note at the top.
    assert page.count("App only") == len(tabs)
    for body in re.findall(r"<script>(.*?)</script>", page, re.S):
        assert "</script" not in body
