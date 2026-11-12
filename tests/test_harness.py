"""The harness icon beside the brand (breadcrumbs_spacegraph/harness.py): which program a view shows, the route that says it,
the address that names it, and the page function that draws it. Run with breadcrumbs-common and this folder on PYTHONPATH:
the table is then the one that these tests make, and no harness package is there."""
import json
import os
import shutil
import subprocess
import tempfile
import unittest
import urllib.error
import urllib.request
from unittest import mock

from breadcrumbs import ports
from breadcrumbs.history import Refs, commit
from breadcrumbs.index import Index
from breadcrumbs.ingest import put_tree, put_turn
from breadcrumbs.store import Store
from breadcrumbs import viz
from breadcrumbs_spacegraph import harness, launch, view

PNG = "data:image/png;base64,iVBORw0KGgo="


def seed(root):
    store, index, refs = Store(os.path.join(root, "store")), Index(os.path.join(root, "index.db")), Refs(os.path.join(root, "refs"))
    turns = [put_turn(store, index, "user", "the kettle boils too fast for green tea %d" % i, "s1", ts=i) for i in range(3)]
    commit(store, refs, "s1", [put_tree(store, "sess", "tea", turns)], "s1", ts=3)
    index.commit()
    index.close()
    store.close()


class Table(unittest.TestCase):
    """Each test runs with the harness table that it sets: the adapters that this environment has are loaded first and put aside."""

    def setUp(self):
        ports.load()
        self.saved = dict(ports._harnesses)
        ports._harnesses.clear()
        self.addCleanup(self.restore)

    def restore(self):
        ports._harnesses.clear()
        ports._harnesses.update(self.saved)

    def give(self, key, label, match=None, icon=None, order=50):
        ports.harness(key, label, match or (key,), lambda cmd: "", order=order, icon=icon)


class Monogram(unittest.TestCase):
    def svg(self, uri):
        import base64
        head, _, body = uri.partition(",")
        self.assertEqual(head, "data:image/svg+xml;base64")
        return base64.b64decode(body).decode("utf-8")

    def test_it_is_the_first_letter_of_the_label_on_a_tile_and_the_same_every_time(self):
        a = harness.monogram("codex", "Codex")
        self.assertEqual(a, harness.monogram("codex", "Codex"))
        self.assertIn(">C</text>", self.svg(a))

    def test_the_tile_has_a_hue_of_its_own_for_each_key(self):
        tiles = {k: self.svg(harness.monogram(k, k.title())).split('fill="')[1].split('"')[0] for k in ("codex", "opencode", "grok", "hermes")}
        self.assertEqual(len(set(tiles.values())), 4, tiles)
        for fill in tiles.values():
            self.assertRegex(fill, r"^#[0-9a-f]{6}$")

    def test_a_letter_that_is_markup_is_escaped_and_an_empty_label_falls_back_to_the_key(self):
        self.assertIn(">&lt;</text>", self.svg(harness.monogram("x", "<b>")))
        self.assertIn(">K</text>", self.svg(harness.monogram("key", "")))


class Resolve(Table):
    def test_every_entry_has_an_icon_the_given_one_or_a_monogram(self):
        self.give("hermes", "Hermes", icon=PNG, order=10)
        self.give("codex", "Codex", order=30)
        rows = {r["key"]: r for r in harness.table()}
        self.assertEqual(rows["hermes"]["icon"], PNG)
        self.assertEqual(rows["codex"]["icon"], harness.monogram("codex", "Codex"))
        self.assertEqual([r["key"] for r in harness.table()], ["hermes", "codex"], "the order of the harness table")

    def test_one_harness_loaded_is_the_harness_of_the_view(self):
        self.give("claude-code", "Claude Code", ("claude",), icon=PNG)
        got = harness.info(env={})
        self.assertEqual(got["here"], "claude-code")
        self.assertEqual([r["label"] for r in got["harnesses"]], ["Claude Code"])

    def test_two_loaded_and_no_name_is_none_and_so_is_none_loaded(self):
        self.assertEqual(harness.info(env={}), {"here": None, "harnesses": []})
        self.give("hermes", "Hermes", order=10)
        self.give("claude-code", "Claude Code", ("claude",), order=20)
        self.assertIsNone(harness.info(env={})["here"], "a view that cannot tell does not guess")

    def test_a_name_is_a_key_a_word_or_a_label_in_any_case(self):
        self.give("hermes", "Hermes", order=10)
        self.give("claude-code", "Claude Code", ("claude",), order=20)
        for name in ("claude-code", "claude", "Claude", "CLAUDE CODE", " claude-code "):
            self.assertEqual(harness.info(name, env={})["here"], "claude-code", name)
        self.assertEqual(harness.info("Hermes", env={})["here"], "hermes")

    def test_a_name_that_the_table_does_not_have_is_none_even_with_one_harness_loaded(self):
        self.give("hermes", "Hermes")
        self.assertIsNone(harness.info("claude-code", env={})["here"], "another one's mark would be wrong")
        self.assertIsNone(harness.info(env={harness.ENV: "nonesuch"})["here"])

    def test_the_address_beats_the_environment_and_the_environment_names_it_when_the_address_does_not(self):
        self.give("hermes", "Hermes", order=10)
        self.give("claude-code", "Claude Code", ("claude",), order=20)
        env = {harness.ENV: "hermes"}
        self.assertEqual(harness.info(env=env)["here"], "hermes")
        self.assertEqual(harness.info("claude", env=env)["here"], "claude-code")


class Address(unittest.TestCase):
    def test_the_fragment_names_the_harness_or_is_empty(self):
        self.assertEqual(harness.fragment("claude-code", env={}), "&h=claude-code")
        self.assertEqual(harness.fragment(None, env={harness.ENV: "hermes"}), "&h=hermes")
        self.assertEqual(harness.fragment("a b&c", env={}), "&h=a%20b%26c", "a name never breaks the address")
        for none in (None, "", "  "):
            self.assertEqual(harness.fragment(none, env={}), "")

    def test_explorer_url_puts_the_harness_after_the_token_and_changes_nothing_without_one(self):
        with tempfile.TemporaryDirectory() as home, mock.patch.object(launch, "answers", return_value=True), \
                mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop(harness.ENV, None)
            open(os.path.join(home, "index.db"), "w").close()
            key = view.token(home)
            self.assertEqual(launch.explorer_url(home), "http://127.0.0.1:%d/#k=%s" % (launch.PORT, key))
            self.assertEqual(launch.explorer_url(home, harness="hermes"), "http://127.0.0.1:%d/#k=%s&h=hermes" % (launch.PORT, key))
            with mock.patch.dict(os.environ, {harness.ENV: "claude-code"}):
                self.assertTrue(launch.explorer_url(home).endswith("#k=%s&h=claude-code" % key))

    def test_run_hands_the_harness_to_the_explorer_port(self):
        explorer = mock.Mock(return_value="http://127.0.0.1:1/#k=x")
        with mock.patch.object(viz, "_explorer", return_value=explorer), \
                mock.patch.object(viz.shutil, "which", return_value=None), mock.patch.object(viz.webbrowser, "open"):
            viz.run("home", harness="hermes", terminal=False, err=open(os.devnull, "w"))
        explorer.assert_called_once_with("home", viz.PORT, "hermes")

    def test_the_window_that_runs_term_gets_the_harness_as_an_environment_variable(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop(harness.ENV, None)
            self.assertNotIn("BREADCRUMBS_HARNESS=hermes", launch.term_argv("/h"))
            argv = launch.term_argv("/h", "hermes")
        self.assertEqual(argv[0], "env")
        self.assertIn("BREADCRUMBS_HARNESS=hermes", argv)
        self.assertLess(argv.index("BREADCRUMBS_HARNESS=hermes"), argv.index("-m"), "the variable is for the new process, not an argument of it")


class Route(Table):
    """/api/harness through the server: the token guards it as it guards every route of the explorer."""

    def get(self, server, path):
        port = server.server_address[1]
        try:
            with urllib.request.urlopen("http://127.0.0.1:%d%s" % (port, path), timeout=10) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            body = json.loads(e.read())
            e.close()
            return e.code, body

    def test_the_route_answers_with_the_icons_and_asks_for_the_token(self):
        import threading
        self.give("hermes", "Hermes", icon=PNG)
        with tempfile.TemporaryDirectory() as root:
            seed(root)
            server = view.make_server(root, port=0)
            threading.Thread(target=server.serve_forever, daemon=True).start()
            self.addCleanup(server.server_close)
            self.addCleanup(server.shutdown)
            key = view.token(root)
            code, body = self.get(server, "/api/harness?k=" + key)
            self.assertEqual(code, 200)
            self.assertEqual(body, {"here": "hermes", "harnesses": [{"key": "hermes", "label": "Hermes", "icon": PNG}]})
            self.assertEqual(self.get(server, "/api/harness?k=wrong")[0], 403)
            self.assertEqual(self.get(server, "/api/harness?k=%s&h=claude" % key)[1]["here"], None)

    def test_the_route_is_the_explorers_and_not_the_dashboards(self):
        self.assertIn("/api/harness", view.EXPLORER_API)
        self.assertNotIn("/api/harness", view.API)


@unittest.skipUnless(shutil.which("node"), "node is not installed")
class Page(unittest.TestCase):
    """view.html: the function that draws the harness runs in node, on a page of a few fake elements."""

    @classmethod
    def setUpClass(cls):
        with open(view.PAGE, encoding="utf-8") as f:
            cls.page = f.read()
        cls.fn = "function showHarness(r) {" + cls.page.split("function showHarness(r) {")[1].split("\n}\n")[0] + "\n}\n"

    def node(self, answer):
        script = """
          const els = {};
          const el = id => els[id] || (els[id] = {id, hidden: true, textContent: "", title: "", src: ""});
          const $ = id => el(id);
          const links = [];
          const document = {title: "breadcrumbs", head: {append: n => links.push(n)}, querySelector: () => links[0] || null,
                            createElement: () => ({})};
          const HARNESS = {key: "", label: "", icon: ""};
          %s
          const ok = showHarness(%s);
          console.log(JSON.stringify({ok, hidden: el("harness").hidden, name: el("hname").textContent, src: el("hicon").src,
            title: el("harness").title, doc: document.title, tab: links.length ? {rel: links[0].rel, href: links[0].href} : null, HARNESS}));
        """ % (self.fn, json.dumps(answer))
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "harness.js")
            with open(path, "w", encoding="utf-8") as f:
                f.write(script)
            r = subprocess.run(["node", path], capture_output=True, text=True, timeout=60)
        self.assertEqual((r.returncode, r.stderr), (0, ""))
        return json.loads(r.stdout)

    def test_the_icon_the_name_the_title_and_the_tab_come_from_the_answer(self):
        got = self.node({"here": "hermes", "harnesses": [{"key": "claude-code", "label": "Claude Code", "icon": "C"},
                                                         {"key": "hermes", "label": "Hermes", "icon": PNG}]})
        self.assertTrue(got["ok"])
        self.assertEqual((got["hidden"], got["name"], got["src"]), (False, "Hermes", PNG))
        self.assertEqual(got["doc"], "breadcrumbs \u00b7 Hermes")
        self.assertIn("Hermes", got["title"])
        self.assertEqual(got["tab"], {"rel": "icon", "href": PNG})
        self.assertEqual(got["HARNESS"]["key"], "hermes")

    def test_with_no_harness_the_brand_stands_alone(self):
        for answer in ({"here": None, "harnesses": [{"key": "hermes", "label": "Hermes", "icon": PNG}]}, {"here": "x", "harnesses": []}, {}):
            got = self.node(answer)
            self.assertFalse(got["ok"])
            self.assertEqual((got["hidden"], got["doc"], got["tab"]), (True, "breadcrumbs", None), answer)

    def test_the_page_asks_for_the_harness_with_the_h_of_its_address_and_the_markup_is_there(self):
        self.assertIn('api("harness", {h: HASH.get("h") || ARG.get("h") || ""})', self.page)
        for part in ('id="harness" hidden', 'id="hicon"', 'id="hname"'):
            self.assertIn(part, self.page)
        self.assertLess(self.page.index('id="brand"'), self.page.index('id="harness"'), "the icon sits in the brand")


if __name__ == "__main__":
    unittest.main()
