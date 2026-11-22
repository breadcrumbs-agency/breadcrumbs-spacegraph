"""Disclosure in the viewers (breadcrumbs_spacegraph/disclosure.py, view.View, the server): a view shows level 2 until it is
asked for more (handles and titles, no body text); level 4 needs the launch flag and a confirmation by hand, and each ask
writes one line to <home>/disclosure.log. Run with breadcrumbs-common and this folder on PYTHONPATH."""
import json
import os
import stat
import tempfile
import threading
import unittest
import urllib.error
import urllib.request

from breadcrumbs.crumbs import Crumb, put_crumb
from breadcrumbs.history import Refs, _ref_name
from breadcrumbs.index import Index
from breadcrumbs.ingest import put_tree, put_turn
from breadcrumbs.lines import put_text, split_lines
from breadcrumbs.reader import Reader
from breadcrumbs.store import Store
from breadcrumbs_spacegraph import disclosure, view

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Words that are only in a body (never in a first line, so never in a title), one for each place that a text can come from.
PRIVATE_BODY, PUBLIC_BODY, SUMMARY_BODY, CTX_BODY, QUERY = "ZQPRIVATEBODY", "ZQPUBLICBODY", "ZQSUMMARYBODY", "ZQCTXBODY", "ZQQUERYWORD"
MIXED_PRIVATE, MIXED_PUBLIC = "ZQMIXEDPRIVATE", "ZQMIXEDPUBLIC"
BODIES = (PRIVATE_BODY, PUBLIC_BODY, SUMMARY_BODY, CTX_BODY, QUERY, MIXED_PRIVATE, MIXED_PUBLIC)
# put_turn cuts a title at 60 characters ("assistant: " + the first line); level 2 cuts it at 40. The mark sits between.
TITLE_TAIL = "ZQTITLETAIL"
LONG_TITLE = "a first line with a long beginning " + TITLE_TAIL + " and then the rest of the line"


def absent(case, words, text, what=""):
    """None of the words is in the text (the message names the words, not the text)."""
    found = [w for w in words if w in text]
    case.assertEqual(found, [], "%s shows %s near %r" % (what, found, text[max(0, text.find(found[0]) - 80):text.find(found[0]) + 60] if found else ""))


def seed_levels(root):
    """A store with a private chat (s1: three turns, a summary, a receipt of a recall with its query and what the model
    saw), a public chat (pub1) and a chat that is public with one private turn (mix1). Returns the ids by name."""
    store, index, refs = Store(os.path.join(root, "store")), Index(os.path.join(root, "index.db")), Refs(os.path.join(root, "refs"))
    ids = {}
    ids["t1"] = put_turn(store, index, "user", "brew plan\n%s the kettle code is quarry\n" % PRIVATE_BODY, "s1", ts=10)
    ids["t2"] = put_turn(store, index, "assistant", "%s\nmore %s lines here\n" % (LONG_TITLE, PRIVATE_BODY), "s1", ts=11, prev=ids["t1"])
    ids["t3"] = put_turn(store, index, "user", "second question\n%s again\n" % PRIVATE_BODY, "s1", ts=12, prev=ids["t2"])
    ids["s1"] = put_tree(store, "sess", "user: brew plan", [ids["t1"], ids["t2"], ids["t3"]], meta={"sess": "s1", "ts": 10})
    refs.set(_ref_name("sess-", "s1"), ids["s1"])
    data = ("the summary says %s\n" % SUMMARY_BODY).encode("utf-8")
    doc = put_text(store, data)
    ids["sum"] = put_crumb(store, Crumb("sum", "summary of the brew plan", ranges=[[doc, 0, len(split_lines(data))]],
                                        heritage=[ids["t1"]], meta={"sess": "s1", "ts": 13}))
    index.set_summary(ids["t1"], ids["sum"])
    seen = ("what the model saw: %s\n" % CTX_BODY).encode("utf-8")
    ctx = put_text(store, seen)
    ids["rcpt"] = put_crumb(store, Crumb("rcpt", "recall", kids=[ids["t1"]], ranges=[[ctx, 0, len(split_lines(seen))]], meta={
        "sess": "s1", "ev": "recall", "ts": 14, "turn": 1, "items": [ids["t1"]], "lv": [0.5], "b": [40], "bytes": 40,
        "budget": 4000, "ovf": False, "rate": 0.5, "band": [0.1, 0.9], "prev": "", "q": "what is the %s kettle code" % QUERY,
        "ctx": ctx}))
    refs.set(_ref_name("rcpt-", "s1"), ids["rcpt"])
    ids["p1"] = put_turn(store, index, "user", "shared tea notes\n%s for everyone\n" % PUBLIC_BODY, "pub1", ts=20, public=True)
    ids["p2"] = put_turn(store, index, "assistant", "shared answer\n%s too\n" % PUBLIC_BODY, "pub1", ts=21, public=True, prev=ids["p1"])
    ids["pub1"] = put_tree(store, "sess", "user: shared tea notes", [ids["p1"], ids["p2"]], public=True, meta={"sess": "pub1", "ts": 20})
    refs.set(_ref_name("sess-", "pub1"), ids["pub1"])
    ids["m1"] = put_turn(store, index, "user", "mixed public turn\n%s open\n" % MIXED_PUBLIC, "mix1", ts=30, public=True)
    ids["m2"] = put_turn(store, index, "assistant", "mixed private turn\n%s closed\n" % MIXED_PRIVATE, "mix1", ts=31, prev=ids["m1"])
    ids["mix1"] = put_tree(store, "sess", "user: mixed public turn", [ids["m1"], ids["m2"]], public=True, meta={"sess": "mix1", "ts": 30})
    refs.set(_ref_name("sess-", "mix1"), ids["mix1"])
    index.commit()
    index.close()
    store.close()
    with open(os.path.join(root, "trail.json"), "w", encoding="utf-8") as f:
        json.dump({"session": "s1", "dropped": 0, "stack": [{"id": ids["s1"], "level": 0.25, "label": "sess kettle quarry brew"}],
                   "line": "sess kettle quarry brew"}, f)
    return ids


class Store3(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = os.path.join(self.tmp.name, "profile", "breadcrumbs")
        os.makedirs(self.root)
        self.ids = seed_levels(self.root)
        self.saved = {k: os.environ.pop(k, None) for k in (disclosure.ENV, disclosure.ENV_TTL)}
        self.addCleanup(self.restore_env)

    def restore_env(self):
        for k, v in self.saved.items():
            os.environ.pop(k, None)
            if v is not None:
                os.environ[k] = v

    def view(self, **kw):
        v = view.View(self.root, **kw)
        self.addCleanup(v.close)
        return v

    def log(self):
        return disclosure.read_log(self.root, 50)

    def everything(self, v):
        """Every answer of a View that can hold a word of a crumb, as one text."""
        ids, out = self.ids, []
        for ref in ids.values():
            for zoom in (0.0, 0.25, 0.5, 1.0):
                out.append(v.crumb(ref, zoom))
            out.append(v.path(ref))
        for sid in ("s1", "pub1", "mix1"):
            out += [v.session(sid), v.context(sid), v.venn(sid)]
        out += [v.graph(), v.graph(root=ids["s1"]), v.graph(root=ids["t1"]), v.graph(root=ids["rcpt"]), v.trail(), v.calendar(),
                v.find("kettle"), v.find(PRIVATE_BODY), v.labels(list(ids.values())), v.where(), v.evidence(),
                v.measures(), v.measures(names=True), v.measures(m="all", names=True),
                v.measures(root="s:s1", m="all", names=True), v.measures(unit="turn", sid="s1", m="all", lines=True, names=True),
                v.measures(unit="event", sid="s1", m="all", names=True), v.ace(), v.changes()]
        return json.dumps(out, ensure_ascii=False)


class Levels(unittest.TestCase):
    def test_a_level_is_a_number_or_a_name_and_the_ceiling_is_3_with_no_flag(self):
        self.assertEqual([disclosure.level_of(x) for x in ("0", 2, " 4 ", "private", "Handles", "9", "", None, "x")],
                         [0, 2, 4, 4, 2, None, None, None, None])
        self.assertEqual(disclosure.ceiling(env={}), 3)
        self.assertEqual(disclosure.ceiling(env={disclosure.ENV: "4"}), 4)
        self.assertEqual(disclosure.ceiling(4, env={}), 4)
        self.assertEqual(disclosure.ceiling(2, env={disclosure.ENV: "4"}), 2, "the flag beats the environment")
        self.assertEqual(disclosure.ceiling(env={disclosure.ENV: "nonsense"}), 3)
        self.assertEqual(disclosure.ceiling(0, env={}), 1, "0 is the opt-out of a profile, not a ceiling")

    def test_a_title_is_cut_at_2_whole_at_4_and_gone_at_1(self):
        long = "x" * 100
        self.assertEqual(disclosure.title(long, 1), "")
        self.assertEqual(len(disclosure.title(long, 2)), disclosure.TITLE)
        self.assertTrue(disclosure.title(long, 2).endswith("\u2026"))
        self.assertEqual(disclosure.title(long, 3), disclosure.title(long, 2), "a private crumb at 3 is as at 2")
        self.assertEqual(disclosure.title(long, 3, public=True), long)
        self.assertEqual(disclosure.title(long, 4), long)
        self.assertEqual(disclosure.title("short", 2), "short")

    def test_an_outline_keeps_handles_and_gaps_and_drops_every_line_of_text(self):
        text = ('\u25b8 aaaa1111 sess "user: a title" \u00b7120t | kettle tea brew\n'
                'a line of body text\n'
                '  \u25b8 bbbb2222 turn "%s" \u00b740t\n'
                '    more body \u25b8 not a handle\n'
                '  \u22ef cccc3333:4-9 these words are body text\n'
                '\u25b8 dddd4444 doc 12 lines \u00b730t\n' % ("t" * 60))
        two = disclosure.outline(text, 2)
        self.assertEqual(two.splitlines(), [
            '\u25b8 aaaa1111 sess "user: a title" \u00b7120t',  # the cue words are words of the body: gone
            '  \u25b8 bbbb2222 turn "%s\u2026" \u00b740t' % ("t" * 39),
            '  \u22ef cccc3333:4-9',
            '\u25b8 dddd4444 doc 12 lines \u00b730t'])
        one = disclosure.outline(text, 1)
        self.assertEqual(one.splitlines(), ['\u25b8 aaaa1111 sess \u00b7120t', '  \u25b8 bbbb2222 turn \u00b740t', '  \u22ef cccc3333:4-9',
                                            '\u25b8 dddd4444 doc 12 lines \u00b730t'])
        self.assertEqual(disclosure.outline("only text\nand more\n", 2), "")
        self.assertEqual(disclosure.outline('\u25b8 aaaa1111 turn \u00b712t \u21d0 bafyabc\n', 2), '\u25b8 aaaa1111 turn \u00b712t \u21d0 bafyabc\n',
                         "the CID that a crumb came from is an id")
        self.assertEqual(disclosure.outline('\u25b8 aaaa1111 turn "a \\"quoted\\" title | not cues" \u00b712t\n', 2),
                         '\u25b8 aaaa1111 turn "a \\"quoted\\" title | not cues" \u00b712t\n', "a bar inside a title is of the title")

    def test_cue_words_are_body_words(self):
        self.assertEqual(disclosure.words(["kettle", "tea"], 2), [])
        self.assertEqual(disclosure.words(["kettle", "tea"], 3), [])
        self.assertEqual(disclosure.words(["kettle", "tea"], 3, public=True), ["kettle", "tea"])
        self.assertEqual(disclosure.words(["kettle", "tea"], 4), ["kettle", "tea"])
        self.assertEqual(disclosure.label("sess", "user: brew plan for the long afternoon of tea", 2), "sess user: brew plan for the lon\u2026")
        self.assertEqual(disclosure.label("sess", "user: brew plan", 1), "sess")
        self.assertEqual(disclosure.label("turn", "", 2, "aaaa1111"), "turn aaaa1111")

    def test_meta_keeps_numbers_and_ids_and_empties_the_words(self):
        m = {"sess": "s1", "role": "user", "ts": 5, "q": "a private query", "brief": "do the thing", "items": ["aaaa1111"],
             "lv": [0.5], "ovf": False, "model": "claude", "note": {"x": "y"}, "words": ["a", "b"]}
        got = disclosure.meta(m, 2)
        self.assertEqual(got, {"sess": "s1", "role": "user", "ts": 5, "q": "", "brief": "", "items": ["aaaa1111"], "lv": [0.5],
                               "ovf": False, "model": "claude", "note": {}, "words": []})
        self.assertIs(disclosure.meta(m, 4), m)
        self.assertIs(disclosure.meta(m, 3, public=True), m)
        self.assertEqual(disclosure.meta(m, 3)["q"], "")


class DefaultIsLevel2(Store3):
    """TODO section 0: level 2 hides body text in every View method."""

    def test_a_view_starts_at_2_under_a_ceiling_of_3(self):
        v = self.view()
        self.assertEqual((v.level, v.gate.default, v.gate.ceiling), (2, 2, 3))

    def test_no_method_gives_a_word_of_a_body_at_level_2(self):
        text = self.everything(self.view())
        absent(self, BODIES + (TITLE_TAIL, "quarry", "kettle"), text, "level 2")
        self.assertIn("brew plan", text, "a title shows")
        self.assertIn("assistant: a first line with a long", text, "and the start of a long title")

    def test_a_crumb_at_level_2_is_its_handle_and_the_handles_of_its_parts(self):
        v = self.view()
        got = v.crumb(self.ids["s1"], 1.0)
        self.assertEqual(got["shown"], 2)
        lines = got["text"].splitlines()
        self.assertEqual(len(lines), 4, lines)
        self.assertTrue(all(line.lstrip().startswith("\u25b8 ") for line in lines), lines)
        self.assertIn('"user: brew plan"', lines[0])
        turn = v.crumb(self.ids["t1"], 1.0)["text"].splitlines()
        self.assertTrue(turn[0].startswith("\u25b8 %s turn" % self.ids["t1"]))
        self.assertTrue(all(line.lstrip()[:1] in "\u25b8\u22ef" for line in turn), turn)
        self.assertEqual(v.crumb(self.ids["s1"], 0.25)["tokens"], Reader(self.root).crumb(self.ids["s1"], 0.25)["tokens"],
                         "the size of a crumb is a number: it shows at every level")

    def test_the_zoom_of_a_text_never_raises_the_disclosure(self):
        v = self.view()
        for zoom in (0.0, 0.01, 0.25, 0.5, 0.99, 1.0):
            self.assertNotIn(PRIVATE_BODY, v.crumb(self.ids["t1"], zoom)["text"])
        for ref in ("%s:0-2" % Crumb.decode(Store(os.path.join(self.root, "store"), readonly=True).get(self.ids["t1"])).ranges[0][0],):
            self.assertNotIn(PRIVATE_BODY, v.crumb(ref, 1.0)["text"], "a text asked by its lines is a text too")

    def test_a_public_crumb_is_handles_only_at_level_2_too(self):
        v = self.view()
        self.assertNotIn(PUBLIC_BODY, v.crumb(self.ids["p1"], 1.0)["text"])
        self.assertEqual(v.crumb(self.ids["p1"], 1.0)["shown"], 2)

    def test_the_query_of_a_context_event_and_the_meta_words_are_empty(self):
        v = self.view()
        ev = v.context("s1")["events"]
        self.assertEqual([e["q"] for e in ev], [""])
        nodes = {n["id"]: n for n in v.session("s1")["nodes"]}
        self.assertEqual(nodes[self.ids["t1"]]["meta"]["role"], "user")
        self.assertEqual(nodes[self.ids["t2"]]["title"], disclosure.cut("assistant: " + LONG_TITLE))
        self.assertLessEqual(len(nodes[self.ids["t2"]]["title"]), disclosure.TITLE)


class Level1(Store3):
    def test_structure_only_has_no_title_no_cue_word_and_no_search(self):
        v = self.view()
        with v.shown(1):
            text = self.everything(v)
            self.assertEqual(v.find("kettle"), [])
            got = v.graph()
            self.assertEqual({n["title"] for n in got["nodes"]}, {""})
            self.assertEqual({tuple(n["cues"]) for n in got["nodes"]}, {()})
            self.assertEqual(got["edges"], [], "no cue word, so no shared word")
            self.assertEqual(v.trail()["stack"][0]["label"], "sess")
            self.assertEqual(v.trail()["line"], "sess")
            self.assertEqual({r["label"] for r in v.context("s1")["rows"]}, {"turn"})
            self.assertEqual(v.crumb(self.ids["s1"], 1.0)["shown"], 1)
            self.assertNotIn('"', v.crumb(self.ids["s1"], 1.0)["text"])
        absent(self, BODIES + ("brew plan", "kettle", "quarry", TITLE_TAIL, "shared tea", "summary of", "first line"), text, "level 1")
        self.assertIn(self.ids["s1"], text, "ids, kinds and numbers stay")


class Level3(Store3):
    def test_public_text_shows_and_private_text_does_not(self):
        v = self.view()
        with v.shown(3):
            text = self.everything(v)
            pub = v.crumb(self.ids["p1"], 1.0)
            self.assertEqual(pub["shown"], 3)
            self.assertEqual(pub["text"], Reader(self.root).crumb(self.ids["p1"], 1.0)["text"], "a public crumb, as stored")
            self.assertEqual(v.crumb(self.ids["pub1"], 1.0)["text"], Reader(self.root).crumb(self.ids["pub1"], 1.0)["text"])
            self.assertEqual(v.crumb(self.ids["t1"], 1.0)["shown"], 2, "a private crumb says that it shows handles only")
        self.assertIn(PUBLIC_BODY, text)
        absent(self, (PRIVATE_BODY, SUMMARY_BODY, CTX_BODY, QUERY, MIXED_PRIVATE, TITLE_TAIL, "quarry"), text, "level 3")

    def test_a_public_crumb_with_a_private_part_shows_the_public_part_only(self):
        v = self.view()
        with v.shown(3):
            got = v.crumb(self.ids["mix1"], 1.0)
        self.assertIn(MIXED_PUBLIC, got["text"])
        self.assertNotIn(MIXED_PRIVATE, got["text"])
        self.assertIn(self.ids["m2"], got["text"], "the private part keeps its handle")
        self.assertEqual(got["shown"], 2, "not all of it is shown")

    def test_a_private_summary_of_a_public_crumb_is_not_shown(self):
        store, index = Store(os.path.join(self.root, "store")), Index(os.path.join(self.root, "index.db"))
        data = b"a summary with ZQSUMOFPUBLIC in it\n"
        doc = put_text(store, data)
        index.set_summary(self.ids["p1"], put_crumb(store, Crumb("sum", "sum", ranges=[[doc, 0, 1]], heritage=[self.ids["p1"]], meta={"sess": "pub1"})))
        index.commit(); index.close(); store.close()
        v = self.view()
        with v.shown(3):
            got = v.crumb(self.ids["p1"], 1.0)
        self.assertNotIn("ZQSUMOFPUBLIC", got["text"])
        self.assertIn(PUBLIC_BODY, got["text"], "its own text is public")


class Level4(Store3):
    def test_private_text_is_as_stored(self):
        v = self.view(disclose=4)
        reader = Reader(self.root)
        self.addCleanup(reader.close)
        self.assertEqual(v.level, 2, "the flag is the ceiling, not the level: a view still starts at 2")
        with v.shown(4):
            for ref in self.ids.values():
                for zoom in (0.0, 0.25, 1.0):
                    self.assertEqual(v.crumb(ref, zoom)["text"], reader.crumb(ref, zoom)["text"])
            text = self.everything(v)
            self.assertEqual(v.context("s1")["events"][0]["q"], "what is the %s kettle code" % QUERY)
        self.assertEqual([w for w in BODIES + (TITLE_TAIL,) if w not in text], [])

    def test_shown_is_never_over_the_ceiling(self):
        v = self.view()  # ceiling 3
        with v.shown(4):
            self.assertEqual(v.level, 3)
            self.assertNotIn(PRIVATE_BODY, v.crumb(self.ids["t1"], 1.0)["text"])
        low = self.view(disclose=1)
        self.assertEqual((low.level, low.gate.default), (1, 1), "a ceiling under 2 lowers the default")
        with low.shown(4):
            self.assertEqual(low.level, 1)

    def test_the_level_of_one_thread_is_not_the_level_of_another(self):
        v, seen = self.view(disclose=4), {}
        with v.shown(4):
            t = threading.Thread(target=lambda: seen.setdefault("other", v.level))
            t.start(); t.join()
            seen["mine"] = v.level
        self.assertEqual(seen, {"other": 2, "mine": 4})


class Gate(Store3):
    """TODO section 0: level 4 needs the launch flag and the drag, and writes an audit line."""

    def gate(self, top=None, **kw):
        return disclosure.Gate(self.root, os.path.join(self.root, "store-x"), top=top, **kw)

    def test_level_4_with_no_launch_flag_is_refused_whatever_the_page_sends(self):
        g = self.gate()
        for how in ("drag", "keys", "", "phrase"):
            with self.assertRaises(disclosure.Refused) as e:
                g.ask(4, how)
            self.assertIn("--disclose 4", str(e.exception))
        self.assertEqual(g.level(), 2)
        lines = self.log()
        self.assertEqual(len(lines), 4)
        self.assertEqual({(l["ok"], l["why"], l["level"], l["ceiling"]) for l in lines}, {("0", "ceiling", "4", "3")})

    def test_level_4_with_the_flag_and_no_drag_is_refused(self):
        g = self.gate(4)
        for how in ("", "click", "typed", "phrase", "DRAG"):
            with self.assertRaises(disclosure.Refused):
                g.ask(4, how)
        self.assertEqual({(l["ok"], l["why"]) for l in self.log()}, {("0", "unconfirmed")})

    def test_level_4_with_the_flag_and_the_drag_is_granted_and_logged(self):
        now = [1_800_000_000.0]
        g = self.gate(4, clock=lambda: now[0])
        got = g.ask(4, "drag", peer="127.0.0.1:5555")
        self.assertEqual((got["level"], got["name"], got["ceiling"]), (4, "private", 4))
        self.assertTrue(got["grant"])
        self.assertEqual(g.level(got["grant"]), 4)
        self.assertEqual(g.level(""), 2, "another page of the same server is still at 2")
        self.assertEqual(g.level("made-up"), 2)
        line = self.log()[-1]
        self.assertEqual((line["ok"], line["from"], line["level"], line["how"], line["peer"], line["via"]),
                         ("1", "2", "4", "drag", "127.0.0.1:5555", "page"))
        self.assertEqual(line["store"], os.path.join(self.root, "store-x"))
        self.assertTrue(line["user"] and line["pid"].isdigit())
        self.assertRegex(line["ts"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d$")
        self.assertEqual(int(line["until"]), int(now[0] + disclosure.TTL))

    def test_a_grant_runs_out_and_the_view_is_at_2_again(self):
        now = [1000.0]
        g = self.gate(4, clock=lambda: now[0], env={disclosure.ENV_TTL: "60"})
        grant = g.ask(4, "keys", via="term")["grant"]
        now[0] += 59
        self.assertEqual(g.level(grant), 4)
        self.assertEqual(g.state(grant)["until"], 1060.0)
        now[0] += 2
        self.assertEqual(g.level(grant), 2)
        self.assertIsNone(g.state(grant)["until"])

    def test_lock_ends_a_grant_at_once_and_writes_a_line(self):
        g = self.gate(4)
        grant = g.ask(4, "drag")["grant"]
        got = g.lock(grant)
        self.assertEqual((got["level"], got["grant"]), (2, ""))
        self.assertEqual(g.level(grant), 2)
        self.assertEqual([(l["from"], l["level"], l["ok"]) for l in self.log()], [("2", "4", "1"), ("4", "2", "1")])

    def test_level_3_needs_no_drag_and_level_1_is_a_grant_with_no_end(self):
        g = self.gate()
        three = g.ask(3)
        self.assertEqual((three["level"], g.level(three["grant"])), (3, 3))
        self.assertIsNotNone(three["until"])
        one = g.ask(1, grant=three["grant"])
        self.assertEqual((one["level"], g.level(one["grant"]), one["until"]), (1, 1, None))
        self.assertEqual(g.level(three["grant"]), 2, "the grant that it replaced is over")

    def test_a_log_that_cannot_be_written_refuses_a_level_above_the_default(self):
        if hasattr(os, "geteuid") and os.geteuid() == 0:
            self.skipTest("root writes to any folder")
        locked = os.path.join(self.tmp.name, "locked")
        os.makedirs(locked)
        os.chmod(locked, stat.S_IRUSR | stat.S_IXUSR)
        self.addCleanup(os.chmod, locked, stat.S_IRWXU)
        g = disclosure.Gate(locked, top=4)
        with self.assertRaises(disclosure.Refused) as e:
            g.ask(4, "drag")
        self.assertIn("disclosure.log", str(e.exception))
        self.assertEqual(g.level(), 2)
        self.assertEqual(g.ask(1)["level"], 1, "a level under the default shows less: it needs no line")

    def test_the_log_file_is_private_to_the_user_and_a_value_holds_no_space(self):
        g = disclosure.Gate(self.root, "/a store/with spaces", top=4)
        g.ask(4, "drag")
        path = os.path.join(self.root, disclosure.LOG)
        self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)
        with open(path, encoding="utf-8") as f:
            raw = f.read()
        self.assertEqual(raw.count("\n"), 1)
        self.assertIn("store=/a%20store/with%20spaces", raw)
        self.assertEqual(self.log()[0]["store"], "/a store/with spaces")

    def test_the_state_says_which_levels_this_process_may_show(self):
        st = self.gate().state()
        self.assertEqual([(l["level"], l["allowed"], l["confirm"]) for l in st["levels"]],
                         [(1, True, False), (2, True, False), (3, True, False), (4, False, True)])
        self.assertEqual(self.gate(4).state()["levels"][-1]["allowed"], True)


class Server(Store3):
    def serve(self, **kw):
        server = view.make_server(self.root, port=0, **kw)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        self.port, self.key = server.server_address[1], view.token(self.root)
        return server

    def call(self, path, body=None, **q):
        url = "http://127.0.0.1:%d/api/%s?%s" % (self.port, path, "&".join("%s=%s" % kv for kv in dict(q, k=self.key).items()))
        req = urllib.request.Request(url, data=None if body is None else json.dumps(body).encode(), method="GET" if body is None else "POST",
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            with e:
                return e.code, json.loads(e.read() or b"{}")

    def test_a_page_reads_level_2_until_it_holds_a_grant(self):
        self.serve()
        code, got = self.call("crumb", id=self.ids["t1"], level="1")
        self.assertEqual((code, got["shown"]), (200, 2))
        self.assertNotIn(PRIVATE_BODY, got["text"])
        code, st = self.call("disclosure")
        self.assertEqual((code, st["level"], st["ceiling"], st["default"]), (200, 2, 3, 2))

    def test_a_server_with_no_flag_refuses_level_4_and_says_how_to_allow_it(self):
        self.serve()
        code, got = self.call("disclosure", {"level": 4, "how": "drag"})
        self.assertEqual(code, 403)
        self.assertIn("--disclose 4", got["error"])
        self.assertEqual((got["level"], got["ceiling"]), (2, 3))
        self.assertEqual(self.log()[-1]["why"], "ceiling")
        code, got = self.call("disclosure", {"level": 4, "how": "drag", "ceiling": 4, "top": 4, "disclose": 4})
        self.assertEqual(code, 403, "no request raises the ceiling")

    def test_the_flag_and_the_drag_unlock_one_page_and_a_line_is_written(self):
        self.serve(disclose=4)
        code, got = self.call("disclosure", {"level": 4, "how": ""})
        self.assertEqual(code, 403, "the flag alone is not enough")
        code, got = self.call("disclosure", {"level": 4, "how": "drag"})
        self.assertEqual((code, got["level"]), (200, 4))
        grant = got["grant"]
        code, mine = self.call("crumb", id=self.ids["t1"], level="1", d=grant)
        self.assertEqual(mine["shown"], 4)
        self.assertIn(PRIVATE_BODY, mine["text"])
        code, other = self.call("crumb", id=self.ids["t1"], level="1")
        self.assertNotIn(PRIVATE_BODY, other["text"], "a second page of the same server has no grant")
        code, forged = self.call("crumb", id=self.ids["t1"], level="1", d="0" * 24)
        self.assertNotIn(PRIVATE_BODY, forged["text"])
        line = self.log()[-1]
        self.assertEqual((line["ok"], line["level"], line["how"], line["via"]), ("1", "4", "drag", "page"))
        self.assertTrue(line["peer"].startswith("127.0.0.1:"))
        code, back = self.call("disclosure", {"lock": True, "d": grant})
        self.assertEqual((code, back["level"], back["grant"]), (200, 2, ""))
        code, after = self.call("crumb", id=self.ids["t1"], level="1", d=grant)
        self.assertNotIn(PRIVATE_BODY, after["text"])

    def test_the_ask_needs_the_token_and_only_post_asks(self):
        self.serve(disclose=4)
        url = "http://127.0.0.1:%d/api/disclosure?k=wrong" % self.port
        req = urllib.request.Request(url, data=b'{"level": 4, "how": "drag"}', method="POST")
        with self.assertRaises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(req, timeout=10)
        self.assertEqual(e.exception.code, 403)
        e.exception.close()
        self.assertEqual(self.log(), [], "no line for a request that had no token")
        for method in ("PUT", "DELETE"):
            req = urllib.request.Request("http://127.0.0.1:%d/api/disclosure?k=%s" % (self.port, self.key), data=b"{}", method=method)
            with self.assertRaises(urllib.error.HTTPError) as e:
                urllib.request.urlopen(req, timeout=10)
            self.assertEqual(e.exception.code, 404)
            e.exception.close()

    def test_every_route_with_words_follows_the_grant(self):
        self.serve(disclose=4)
        grant = self.call("disclosure", {"level": 4, "how": "drag"})[1]["grant"]
        for path, q, word in (("session", {"sid": "s1"}, "ZQTITLETAIL"), ("context", {"sid": "s1"}, QUERY),
                              ("graph", {"root": self.ids["s1"]}, "ZQTITLETAIL"), ("calendar", {}, "brew plan")):
            closed = json.dumps(self.call(path, **q)[1])
            opened = json.dumps(self.call(path, d=grant, **q)[1])
            if word != "brew plan":
                self.assertNotIn(word, closed, path)
            self.assertIn(word, opened, path)


class Page(unittest.TestCase):
    """The page is read, not run (tests/test_page.py runs it in a browser where one is installed)."""

    def setUp(self):
        with open(os.path.join(HERE, "breadcrumbs_spacegraph", "view.html"), encoding="utf-8") as f:
            self.html = f.read()

    def test_the_page_has_the_level_chip_the_drag_bar_and_the_banner(self):
        for needle in ('id="disclose"', 'id="disclose-panel"', 'id="disclose-banner"', 'id="disclose-slide"', 'type="range"',
                       'api("disclosure"', '"drag"', "Slide to show private text"):
            self.assertIn(needle, self.html)

    def test_level_4_is_never_asked_by_a_typed_word(self):
        a, b = self.html.index("// -- disclosure"), self.html.index("// -- end of disclosure")
        block = self.html[a:b]
        for typed in ("prompt(", "confirm(", 'type="text"', 'type="password"'):
            self.assertNotIn(typed, block)

    def test_every_api_call_carries_the_grant(self):
        self.assertIn("if (GRANT) p.d = GRANT;", self.html)


if __name__ == "__main__":
    unittest.main()
