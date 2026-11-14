"""The evidence QR of the harness box: /api/where says where a view reads from, /api/qr gives it as codes, and the page draws
them on a click of the harness icon. Run with breadcrumbs-common and this folder on PYTHONPATH."""
import json
import os
import tempfile
import threading
import unittest
import urllib.error
import urllib.request

from breadcrumbs_spacegraph import qr, view
from test_harness import seed

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def ace_line(ts, sess, pid=4242):
    return ("%s pid=%d sess=%s act=keep in=851293 out=201605 last=198049 base=16210 tpb=0.5 r=0.5 folds=12 layer=826 "
            "target=206611 gain=0.89 lay=90822 gear=- think=0 calls=4 work=0 room=0\n") % (ts, pid, sess)


class Evidence(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.profile = os.path.join(self.tmp.name, "profiles", "alpha")
        self.base = os.path.join(self.profile, "breadcrumbs")
        self.scope = os.path.join(self.base, "scopes", "app-1234abcd")
        os.makedirs(self.scope)
        seed(self.scope)
        with open(os.path.join(self.scope, "scope.json"), "w", encoding="utf-8") as f:
            json.dump({"id": "app-1234abcd", "root": "/work/app", "kind": "git"}, f)
        with open(os.path.join(self.base, "ace.log"), "w", encoding="utf-8") as f:  # the logs belong to the base, not the scope
            f.write(ace_line("2026-11-13T20:27:55", "s1"))
        self.view = view.View(self.scope)

    def test_where_names_the_store_the_scope_the_folder_the_profile_the_writer_and_the_last_request(self):
        w = self.view.where()
        self.assertEqual((w["kind"], w["scope"], w["pwd"], w["profile"]), ("scope", "app-1234abcd", "/work/app", "alpha"))
        self.assertEqual(w["sess"], "s1")
        self.assertEqual(w["writer"], 4242)
        self.assertEqual((w["ace"]["raw"], w["ace"]["sent"], w["ace"]["target"], w["ace"]["folds"]), (851293, 201605, 206611, 12))

    def test_a_viewer_of_a_scope_reads_the_logs_of_its_base(self):
        self.assertEqual(len(self.view.ace()["points"]), 1)

    def test_the_line_never_holds_the_token_or_crumb_text(self):
        key = view.token(self.scope)
        r = self.view.evidence(harness="hermes")
        self.assertNotIn(key, r["text"])
        self.assertNotIn("kettle", r["text"])
        head, _, body = r["text"].partition(" ")
        self.assertEqual(head, "breadcrumbs-where/1")
        self.assertEqual(json.loads(body)["harness"], "hermes")

    def test_the_parts_are_codes_and_a_long_line_is_chained(self):
        r = self.view.evidence()
        self.assertTrue(r["parts"])
        for rows in r["parts"]:
            self.assertEqual(len(rows), len(rows[0]))
            self.assertTrue(set("".join(rows)) <= {"0", "1"})
        self.assertEqual(len(r["parts"]), len(qr.chain(r["text"], "M")))
        with open(os.path.join(self.scope, "scope.json"), "w", encoding="utf-8") as f:  # a deep folder makes a long line
            json.dump({"id": "app-1234abcd", "root": "/work/" + "/".join(["folder%03d" % i for i in range(40)]), "kind": "git"}, f)
        longer = view.View(self.scope).evidence()
        self.assertGreater(len(longer["parts"]), 1)
        self.assertEqual("".join(p.split("|", 1)[1] for p in qr.chain(longer["text"], "M")), longer["text"])

    def test_the_route_needs_the_token(self):
        server = view.make_server(self.scope, port=0)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        port = server.server_address[1]

        def get(path):
            try:
                with urllib.request.urlopen("http://127.0.0.1:%d%s" % (port, path), timeout=10) as r:
                    return r.status, json.loads(r.read())
            except urllib.error.HTTPError as e:
                e.close()
                return e.code, None
        code, body = get("/api/qr?k=" + view.token(self.scope))
        self.assertEqual(code, 200)
        self.assertTrue(body["parts"])
        self.assertEqual(get("/api/qr?k=wrong")[0], 403)
        self.assertEqual(get("/api/where?k=wrong")[0], 403)


class Page(unittest.TestCase):
    """The page: a click on the harness box draws the codes (the JS is read, not run: the explorer has no browser test)."""

    def test_the_harness_box_opens_the_evidence_and_escape_closes_it(self):
        with open(os.path.join(HERE, "breadcrumbs_spacegraph", "view.html"), encoding="utf-8") as f:
            html = f.read()
        for needle in ('id="evidence"', 'api("qr"', '$("harness").addEventListener("click", showEvidence)', "Escape", "drawCode"):
            self.assertIn(needle, html)
        self.assertNotIn("KEY", html[html.index("function drawCode"):html.index("async function showEvidence")])  # the codes carry no token


if __name__ == "__main__":
    unittest.main()
