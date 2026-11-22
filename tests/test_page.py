"""The explorer page in a real browser: a headless Chrome draws it from a small store (tests/demo_store.py) and a mouse, a
finger and keys act on it (tests/chrome.py). Skipped when no Chrome or Chromium is installed (BREADCRUMBS_CHROME names one).

Each test keeps its pictures: in a scratch folder, or in the folder that BREADCRUMBS_SHOTS names (the pictures in
screenshots/ were made that way: tests/shots.py)."""
import json
import os
import tempfile
import threading
import unittest

import chrome
import demo_store
from breadcrumbs_spacegraph import disclosure, view

BODY_WORDS = (demo_store.SECRET, demo_store.SHARED, demo_store.QUERY)
# Everything that the page holds of the store as text: what it drew on the canvas comes from here, and the DOM beside it.
HELD = """(() => {
  const out = [document.body.innerText, document.title];
  for (const n of nodes.values()) {
    out.push(n.title || "", (n.cues || []).join(" "), n.tip || "", n.say || "", n.head || "", n.label || "", n.detail || "", n.trail || "",
             n.full || "", n.gist || "", n.sum || "", [...(n.texts ? n.texts.values() : [])].join("\\n"));
  }
  for (const e of cueEdges) out.push((e.shared || []).join(" "));
  for (const r of MEA.rows.values()) out.push(r.label || "");
  out.push(JSON.stringify(ctxData || {}), [...MEA_NAMES.values()].join(" "));
  return out.join("\\n");
})()"""


@unittest.skipUnless(chrome.available(), "no Chrome or Chromium to draw the page (set BREADCRUMBS_CHROME)")
class PageCase(unittest.TestCase):
    DISCLOSE = None  # the flag that the server of the class starts with
    SIZE = (1280, 800)
    TOUCH = False

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.root = os.path.join(cls.tmp.name, "profile", "breadcrumbs")
        cls.saved = {k: os.environ.pop(k, None) for k in (disclosure.ENV, disclosure.ENV_TTL)}
        cls.store = demo_store.build(cls.root)
        cls.server = view.make_server(cls.root, port=0, disclose=cls.DISCLOSE)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.url = "http://127.0.0.1:%d/#k=%s" % (cls.server.server_address[1], view.token(cls.root))
        cls.shots = os.environ.get("BREADCRUMBS_SHOTS") or os.path.join(cls.tmp.name, "shots")
        os.makedirs(cls.shots, exist_ok=True)
        try:
            cls.tab = chrome.Chrome(*cls.SIZE, touch=cls.TOUCH)
        except BaseException:
            cls.tearDownClass()
            raise

    @classmethod
    def tearDownClass(cls):
        if getattr(cls, "tab", None):
            cls.tab.close()
        cls.server.shutdown()
        cls.server.server_close()
        for k, v in cls.saved.items():
            if v is not None:
                os.environ[k] = v
        cls.tmp.cleanup()

    def open(self, more="", ready=True):
        """A new page (so at level 2 again), with the map drawn."""
        tab = self.tab
        tab.events.clear()
        tab.goto("about:blank")
        tab.goto(self.url + more)
        if ready:
            tab.wait("typeof GRAPH === 'object' && GRAPH.state === 'ready' && roots.length > 0 && !$('disclose').hidden")
        return tab

    def shot(self, name):
        png = self.tab.screenshot(os.path.join(self.shots, name + ".png"))
        self.assertEqual(chrome.png_size(png), self.SIZE, name)
        self.assertGreater(chrome.png_colors(png), 16, "%s is a blank picture" % name)
        return png

    def held(self):
        return self.tab.js(HELD)

    def absent(self, words=BODY_WORDS):
        text = self.held()
        self.assertEqual([w for w in words if w in text], [])

    def log(self):
        return disclosure.read_log(self.root, 50)

    def open_session(self, sid=None):
        """Zoom to a chat so that its document opens; the key of its box."""
        ref = self.store["sess"][sid or self.store["private"]]
        self.tab.js("focus(%s)" % json.dumps(ref))
        self.tab.wait("(() => { const n = nodes.get(%s); return n && n.doc && n.doc.state === 'ready'; })()" % json.dumps(ref))
        return ref

    def no_errors(self):
        self.assertEqual(self.tab.errors(), [])


class DefaultLevel(PageCase):
    def test_the_page_starts_at_level_2_and_holds_no_word_of_a_body(self):
        tab = self.open()
        self.assertEqual(tab.js("[DISCLOSE.level, DISCLOSE.ceiling, GRANT]"), [2, 3, ""])
        self.assertIn("Level 2", tab.js("$('disclose').innerText"))
        self.absent()
        ref = self.open_session()
        tab.wait("cam.k > 2")  # the camera is on its way into the chat
        tab.js("new Promise(r => setTimeout(r, 900))")
        self.absent()
        cards = tab.js("nodes.get(%s).doc.cards.map(n => n.label)" % json.dumps(ref))
        self.assertIn("read_file", cards, "a tool card is named by its tool")
        self.assertIn("user", cards)
        self.assertIn("kettle question 1", self.held(), "titles show")
        self.shot("level2-session")
        self.no_errors()

    def test_the_side_panel_says_why_the_text_is_not_there(self):
        tab = self.open()
        tab.js("show(%s, 1, false)" % json.dumps(self.store["first"][self.store["private"]]))
        tab.wait("!!document.querySelector('#sbody .dl-note')")
        note = tab.js("document.querySelector('#sbody .dl-note').innerText")
        self.assertIn("level 2", note)
        self.assertNotIn(demo_store.SECRET, tab.js("$('side').innerText"))
        b = tab.box("#sbody .dl-note .btn")
        tab.click(b["cx"], b["cy"])
        tab.wait("!$('disclose-panel').hidden")
        self.no_errors()

    def test_level_4_is_not_offered_and_the_panel_says_what_it_takes(self):
        tab = self.open()
        tab.click_on("#disclose")
        tab.wait("!$('disclose-panel').hidden && $('disclose-levels').children.length === 3")
        self.assertTrue(tab.js("$('disclose-four').hidden"))
        self.assertIsNone(tab.box("#disclose-slide"), "no bar to slide")
        text = tab.js("$('disclose-four-no').innerText")
        self.assertIn("--disclose 4", text)
        self.assertIn(disclosure.ENV, text)
        got = tab.js("discloseAsk(4, 'drag').then(() => [DISCLOSE.level, $('disclose-status').innerText])")
        self.assertEqual(got[0], 2, "the server refuses whatever the page sends")
        self.assertIn("--disclose 4", got[1])
        self.assertEqual(self.log()[-1]["why"], "ceiling")
        self.shot("level2-panel-no-flag")
        tab.key("Escape")
        tab.wait("$('disclose-panel').hidden")
        self.no_errors()

    def test_level_3_is_one_click_and_shows_public_text_only(self):
        tab = self.open()
        tab.click_on("#disclose")
        tab.wait("$('disclose-levels').children.length === 3")
        tab.click_on('#disclose-levels .dl-row[data-level="3"]')
        tab.wait("DISCLOSE.level === 3 && GRAPH.state === 'ready' && roots.length > 0")
        self.assertTrue(tab.js("GRANT.length > 0 && $('disclose').classList.contains('l3') && $('disclose-banner').hidden"))
        tab.js("show(%s, 1, false)" % json.dumps(self.store["first"][self.store["public"]]))
        tab.wait("$('sbody').innerText.includes(%s)" % json.dumps(demo_store.SHARED))
        tab.js("show(%s, 1, false)" % json.dumps(self.store["first"][self.store["private"]]))
        tab.wait("!!document.querySelector('#sbody .dl-note')")
        self.assertIn("private", tab.js("document.querySelector('#sbody .dl-note').innerText"))
        self.absent((demo_store.SECRET, demo_store.QUERY))
        self.assertEqual((self.log()[-1]["level"], self.log()[-1]["ok"]), ("3", "1"))
        tab.click_on("#disclose-lock")
        tab.wait("DISCLOSE.level === 2 && GRANT === '' && GRAPH.state === 'ready'")
        self.absent()
        self.no_errors()

    def test_structure_only_has_no_title(self):
        tab = self.open()
        tab.js("discloseAsk(1, '')")
        tab.wait("DISCLOSE.level === 1 && GRAPH.state === 'ready' && roots.length > 0")
        self.open_session()
        self.absent(BODY_WORDS + ("kettle", "question", "garden"))
        self.shot("level1-structure")
        self.no_errors()


class LevelFour(PageCase):
    DISCLOSE = 4

    def slide(self, tab, to=1.0):
        tab.click_on("#disclose")
        tab.wait("!$('disclose-panel').hidden && !$('disclose-four').hidden")
        b = tab.box("#disclose-slide")
        return b, (b["x"] + 24, b["cy"], b["x"] + 24 + (b["w"] - 30) * to, b["cy"])

    def test_the_drag_bar_unlocks_private_text_with_a_banner_and_a_line_in_the_log(self):
        tab = self.open()
        before = len(self.log())
        self.assertEqual(tab.js("[DISCLOSE.level, DISCLOSE.ceiling]"), [2, 4], "the flag is the ceiling: the page still starts at 2")
        b, path = self.slide(tab)
        self.assertGreaterEqual(b["h"], 44, "a finger can hold the bar")
        self.shot("level4-drag-bar")
        tab.drag(*path)
        tab.wait("DISCLOSE.level === 4 && GRAPH.state === 'ready' && roots.length > 0")
        self.assertEqual(tab.js("[$('disclose-banner').hidden, TOP, $('disclose').classList.contains('l4'), GRANT.length > 0]"), [False, 102, True, True])
        banner = tab.js("$('disclose-banner').innerText")
        self.assertIn("Private text is shown", banner)
        self.assertRegex(banner, r"locks itself in \d+:\d\d")
        line = self.log()[-1]
        self.assertEqual(len(self.log()), before + 1)
        self.assertEqual((line["ok"], line["from"], line["level"], line["how"], line["via"], line["ceiling"]), ("1", "2", "4", "drag", "page", "4"))
        self.assertEqual(line["store"], os.path.abspath(self.root))
        tab.key("Escape")
        tab.js("show(%s, 1, false)" % json.dumps(self.store["first"][self.store["private"]]))
        tab.wait("$('sbody').innerText.includes(%s)" % json.dumps(demo_store.SECRET))
        self.assertIsNone(tab.js("document.querySelector('#sbody .dl-note')"))
        self.open_session()
        tab.js("new Promise(r => setTimeout(r, 900))")
        self.shot("level4-banner")
        tab.click_on("#disclose-banner-lock")
        tab.wait("DISCLOSE.level === 2 && GRANT === '' && GRAPH.state === 'ready' && roots.length > 0")
        self.assertEqual(tab.js("[$('disclose-banner').hidden, TOP]"), [True, 72])
        self.absent()
        self.assertFalse(tab.js("$('side').classList.contains('on')"), "the panel that held the text is closed")
        self.assertEqual([(l["from"], l["level"]) for l in self.log()[-2:]], [("2", "4"), ("4", "2")])
        self.no_errors()

    def test_a_press_at_the_end_of_the_bar_and_a_slide_that_stops_short_do_not_unlock(self):
        tab = self.open()
        before = len(self.log())
        b, path = self.slide(tab)
        tab.click(b["x"] + b["w"] - 8, b["cy"])
        tab.js("new Promise(r => setTimeout(r, 300))")
        self.assertEqual(tab.js("[DISCLOSE.level, $('disclose-slide').value]"), [2, "0"], "a press at the end is no slide")
        b, path = self.slide_again(tab, 0.7)
        tab.drag(*path)
        tab.js("new Promise(r => setTimeout(r, 300))")
        self.assertEqual(tab.js("[DISCLOSE.level, $('disclose-slide').value]"), [2, "0"], "let go before the end: back to the start")
        self.assertEqual(len(self.log()), before, "nothing was asked")
        self.no_errors()

    def slide_again(self, tab, to):
        b = tab.box("#disclose-slide")
        return b, (b["x"] + 24, b["cy"], b["x"] + 24 + (b["w"] - 30) * to, b["cy"])

    def test_the_grant_is_of_one_page_and_a_new_page_starts_at_2(self):
        tab = self.open()
        b, path = self.slide(tab)
        tab.drag(*path)
        tab.wait("DISCLOSE.level === 4")
        tab = self.open()
        self.assertEqual(tab.js("[DISCLOSE.level, GRANT, $('disclose-banner').hidden]"), [2, "", True])
        self.absent()

    def test_a_grant_that_runs_out_locks_the_page_by_itself(self):
        self.server_gate().ttl = 2.0
        self.addCleanup(setattr, self.server_gate(), "ttl", disclosure.TTL)
        tab = self.open()
        b, path = self.slide(tab)
        tab.drag(*path)
        tab.wait("DISCLOSE.level === 4")
        tab.wait("DISCLOSE.level === 2 && $('disclose-banner').hidden", timeout=15)
        tab.wait("GRAPH.state === 'ready' && roots.length > 0")
        self.absent()

    def server_gate(self):
        """The gate of the server of this class (the View is closed over by its handler)."""
        import gc
        for obj in gc.get_objects():
            if isinstance(obj, view.View) and os.path.abspath(obj.root) == os.path.abspath(self.root) and not obj._closed:
                return obj.gate
        raise AssertionError("no view")


class Phone(PageCase):
    """390 px wide, a touch screen: the level chip and the bar are in reach of a finger."""
    DISCLOSE = 4
    SIZE = (390, 844)
    TOUCH = True

    def test_a_finger_slides_the_bar(self):
        tab = self.open()
        chip = tab.box("#disclose")
        self.assertTrue(0 <= chip["x"] and chip["x"] + chip["w"] <= 390, "the chip is on the screen: %r" % chip)
        tab.tap(chip["cx"], chip["cy"])
        tab.wait("!$('disclose-panel').hidden && !$('disclose-four').hidden")
        b = tab.box("#disclose-slide")
        self.assertTrue(b["x"] >= 0 and b["x"] + b["w"] <= 390 and b["y"] + b["h"] <= 844, "the bar is on the screen: %r" % b)
        self.assertGreaterEqual(b["h"], 44)
        for row in tab.js("[...document.querySelectorAll('#disclose-levels .dl-row')].map(e => e.getBoundingClientRect().height)"):
            self.assertGreaterEqual(row, 44, "a level is a touch target")
        self.shot("phone-drag-bar")
        tab.swipe(b["x"] + 24, b["cy"], b["x"] + b["w"] - 6, b["cy"])
        tab.wait("DISCLOSE.level === 4")
        self.assertEqual(self.log()[-1]["how"], "drag")
        self.assertFalse(tab.js("$('disclose-banner').hidden"))
        lock = tab.box("#disclose-banner-lock")
        self.assertTrue(lock and lock["x"] + lock["w"] <= 390, "Lock now is on the screen: %r" % lock)
        self.shot("phone-level4")
        self.no_errors()


if __name__ == "__main__":
    unittest.main()
