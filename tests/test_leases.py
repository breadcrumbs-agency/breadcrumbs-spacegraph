"""The cache of a chat for the Size view: View.leases_of (the "leases" of /api/ace) gives the leases and the requests of
usage.log, and names the requests that a second agent sent under the chat's session id (view.side_of). Run with
breadcrumbs-common and this folder on PYTHONPATH. The numbers and the prices are made up."""
import json
import os
import shutil
import subprocess
import tempfile
import unittest

from breadcrumbs import ports
from breadcrumbs_spacegraph import view
from test_harness import seed

S = "local-aaaa1111bbbb2222cccc3333dddd4444"
OTHER = "0f0e0d0c-1111-2222-3333-444455556666"  # the task of a second agent: Hermes names a turn <session>:<task>:<n>


def usage_line(stamp, cr, cw, n=1, task=S, turn="t1", inp=4, dur="2.000", sess=S):
    turn_id = "%s:%s:%s" % (sess, task, turn)
    return ("2026-01-05T%s sess=%s turn=%s req=%s:api:%d n=%d task=main model=m1 resp=m1 prov=p in=%d cr=%d cw=%d out=10 rsn=0 "
            "prompt=%d est=0 msgs=2 tools=0 dur=%s fin=stop\n") % (stamp, sess, turn_id, turn_id, n, n, inp, cr, cw, inp + cr + cw, dur)


def ace_line(stamp, sess=S[:24]):
    return ("2026-01-05T%s pid=1 sess=%s act=keep in=5000 out=1500 last=1400 base=500 tpb=0.5 r=0.5 folds=0 layer=0 "
            "target=2000 gain=1.0 lay=0 gear=- think=0 calls=0 work=0 room=0\n") % (stamp, sess)


class SideOf(unittest.TestCase):
    def test_the_chat_itself_has_no_side(self):
        self.assertEqual(view.side_of({"sess": S, "turn": "%s:%s:c4aa4619" % (S, S)}), "")

    def test_a_turn_of_another_task_on_the_session_id_is_a_second_agent(self):
        self.assertEqual(view.side_of({"sess": S, "turn": "%s:%s:79428658" % (S, OTHER)}), "0f0e0d0c")

    def test_a_turn_id_of_another_form_has_no_side(self):
        for turn in (None, "", "-", "turn-7", S, S + ":only", "x:" + S + ":y"):
            self.assertEqual(view.side_of({"sess": S, "turn": turn}), "", turn)
        self.assertEqual(view.side_of({"sess": "", "turn": "a:b:c"}), "")


class Leases(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = os.path.join(self.tmp.name, "breadcrumbs")
        os.makedirs(self.base)
        seed(self.base)
        self.addCleanup(ports.give, "price", ports.get("price"))
        ports.give("price", lambda model, provider: [(1000, (1.0, 0.1, 1.25)), (None, (5.0, 0.5, 6.25))])  # steps up over 1000

    def logs(self, usage, ace=None):
        with open(os.path.join(self.base, "usage.log"), "w", encoding="utf-8") as f:
            f.write("".join(usage))
        with open(os.path.join(self.base, "ace.log"), "w", encoding="utf-8") as f:
            f.write("".join(ace or [ace_line("10:00:00")]))
        v = view.View(self.base)
        self.addCleanup(v.close)
        return v

    def chat(self):
        """A chat of three requests, then a second agent on its session id that reads the lowest lease and writes 1200."""
        return self.logs([usage_line("10:00:02", 0, 500, n=1), usage_line("10:00:12", 500, 300, n=2),
                          usage_line("10:00:22", 800, 0, n=3),
                          usage_line("10:00:32", 500, 1200, n=1, task=OTHER, turn="t9")])

    def test_each_request_has_its_hit_and_its_write_and_each_write_is_a_lease(self):
        got = self.chat().leases_of(S[:24])
        self.assertEqual([(m["cr"], m["cw"], m["kind"], m["n"]) for m in got["marks"]],
                         [(0, 500, "miss", 1), (500, 300, "hit", 2), (800, 0, "hit", 3), (500, 1200, "part", 1)])
        self.assertEqual([(ls["lo"], ls["hi"], ls["up"], ls["reads"]) for ls in got["items"]],
                         [(0, 500, None, 3), (500, 800, 0, 1), (500, 1700, 0, 0)])
        by = {ls["id"]: ls for ls in got["items"]}
        for m in got["marks"]:  # a hit is as tall as the leases that it read
            height, at = 0, m["read"]
            while at is not None:
                height, at = height + by[at]["hi"] - by[at]["lo"], by[at]["up"]
            self.assertEqual(height, m["cr"])
        self.assertEqual((got["ttl"], got["totals"]["requests"], got["totals"]["write_tok"]), (300, 4, 2000))
        self.assertGreater(got["now"], got["items"][-1]["end"])

    def test_a_second_agent_on_the_session_id_is_named(self):
        got = self.chat().leases_of(S[:24])
        self.assertEqual([m["side"] for m in got["marks"]], ["", "", "", "0f0e0d0c"])
        self.assertEqual([ls["side"] for ls in got["items"]], ["", "", "0f0e0d0c"])
        self.assertEqual(got["sides"]["0f0e0d0c"]["requests"], 1)
        self.assertEqual(got["sides"]["0f0e0d0c"]["write_tok"], 1200)

    def test_a_request_over_the_price_step_pays_the_higher_rate(self):
        got = self.chat().leases_of(S[:24])
        self.assertEqual(got["steps"], [(1000, 5.0)])
        self.assertEqual([m["tier"] for m in got["marks"]], [0, 0, 0, 1])  # 500 + 1200 + 4 is over 1000
        self.assertAlmostEqual(got["marks"][3]["store_usd"], 1200 * 6.25 / 1e6)
        self.assertAlmostEqual(got["sides"]["0f0e0d0c"]["store_usd"], 1200 * 6.25 / 1e6)
        self.assertAlmostEqual(got["marks"][1]["store_usd"], 300 * 1.25 / 1e6)

    def test_a_log_with_turn_ids_of_another_form_names_no_second_agent(self):
        lines = [usage_line("10:00:02", 0, 500).replace("turn=%s:%s:t1" % (S, S), "turn=a"),
                 usage_line("10:00:12", 500, 300, n=2).replace("turn=%s:%s:t1" % (S, S), "turn=b")]
        got = self.logs(lines).leases_of(S[:24])
        self.assertEqual(([m["side"] for m in got["marks"]], got["sides"]), (["", ""], {}))

    def test_a_price_with_no_step_and_no_price_at_all(self):
        ports.give("price", lambda model, provider: (1.0, 0.1, 1.25))
        self.assertEqual(self.chat().leases_of(S[:24])["steps"], [])
        ports.give("price", None)
        got = self.chat().leases_of(S[:24])
        self.assertEqual((got["steps"], got["marks"][0]["store_usd"], got["items"][0]["store_usd"]), ([], None, 0.0))

    def test_no_session_and_a_session_with_no_request(self):
        v = self.chat()
        for hit in ("", "local-none"):
            got = v.leases_of(hit)
            self.assertEqual((got["items"], got["marks"], got["sides"], got["steps"]), ([], [], {}, []))

    def test_the_answer_of_ace_carries_the_leases_of_its_session(self):
        got = self.chat().ace(S[:24])
        self.assertEqual(got["session"], S[:24])
        self.assertEqual((len(got["leases"]["items"]), len(got["leases"]["marks"])), (3, 4))


class Page(unittest.TestCase):
    def test_the_page_draws_the_hits_the_writes_and_the_leases_on_a_chart_that_zooms(self):
        with open(view.PAGE, encoding="utf-8") as f:
            html = f.read()
        for name in ("function sizeChart(d)", "function czModel(d)", 'grid.append(sizeChart(d)', '"wheel"', "L.marks", 'HASH.get("pin")',
                     "time, idle cut"):
            self.assertIn(name, html)


@unittest.skipUnless(shutil.which("node"), "node is not installed")
class Momentum(unittest.TestCase):
    """view.html: the window across the chart has weight. The two functions of a glide that need no canvas (czThrow: the speed
    that a release gives; czGlide: one frame) run in node with the page's own spring and MOVE table."""

    @classmethod
    def setUpClass(cls):
        with open(view.PAGE, encoding="utf-8") as f:
            cls.page = f.read()

    def cut(self, head):
        self.assertEqual(self.page.count(head), 1, head)
        return head + self.page.split(head)[1].split("\n}\n")[0] + "\n}\n"

    def node(self, body):
        parts = [self.page.split("const clamp = ")[0] and "const clamp = " + self.page.split("const clamp = ")[1].split("\n")[0],
                 "const MOVE = " + self.page.split("const MOVE = ")[1].split("\n")[0],
                 self.cut("function damp(x, v, goal, omega, dt) {"), self.cut("function spring(x, v, goal, omega, dt) {"),
                 self.cut("function czThrow(pts, now, span, pw) {"), self.cut("function czGlide(a, v, goal, om, dt, px1, top) {")]
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "glide.js")
            with open(path, "w", encoding="utf-8") as f:
                f.write("\n".join(parts) + "\nconsole.log(JSON.stringify((() => {" + body + "})()));\n")
            r = subprocess.run(["node", path], capture_output=True, text=True, timeout=60)
        self.assertEqual((r.returncode, r.stderr), (0, ""))
        return json.loads(r.stdout)

    def test_a_release_gives_the_speed_of_the_hand_against_the_hand(self):
        # the hand moved 30 px left every 16 ms (1875 px/s); the window shows 200 units on 1000 px: 375 units a second, to the right
        got = self.node("const pts = []; for (let i = 0; i < 6; i++) pts.push([900 - 30 * i, 1000 + 16 * i]);"
                        "return [czThrow(pts, 1080, 200, 1000), czThrow(pts.map(p => [1800 - p[0], p[1]]), 1080, 200, 1000)];")
        self.assertAlmostEqual(got[0], 375.0, places=6)
        self.assertAlmostEqual(got[1], -375.0, places=6)

    def test_a_hand_that_stopped_or_went_slowly_or_too_fast(self):
        got = self.node("const mk = (step, gap) => { const p = []; for (let i = 0; i < 6; i++) p.push([900 - step * i, 1000 + gap * i]); return p; };"
                        "return {stopped: czThrow(mk(30, 16), 1080 + 150, 200, 1000), slow: czThrow(mk(1, 16), 1080, 200, 1000),"
                        " one: czThrow([[900, 1000]], 1000, 200, 1000), fast: czThrow(mk(200, 16), 1080, 200, 1000), most: MOVE.fast};")
        self.assertEqual((got["stopped"], got["slow"], got["one"]), (0, 0, 0))
        self.assertAlmostEqual(got["fast"], got["most"] / 1000 * 200, places=6)  # no faster than MOVE.fast px/s

    def test_a_glide_starts_at_the_speed_given_slows_and_stops_on_its_goal(self):
        got = self.node("let a = 100, v = 375, n = 0, there = false; const goal = 100 + 375 * MOVE.throw, steps = [];"
                        "while (!there && n < 2000) { const was = a; [a, v, there] = czGlide(a, v, goal, MOVE.focus, 1 / 60, 0.2, 9000); steps.push(a - was); n++; }"
                        "return {a, v, goal, n, first: steps[0], past: steps.some(s => s < 0), slower: steps.slice(16, -1).every((s, i) => s <= steps[15 + i] + 1e-9)};")
        self.assertEqual((got["a"], got["v"]), (got["goal"], 0))
        self.assertAlmostEqual(got["first"], 375 / 60, delta=0.5)  # the first frame goes on at the speed of the hand
        self.assertFalse(got["past"])
        self.assertTrue(got["slower"])  # the map's spring: it pulls for the first quarter second, then every frame is shorter
        self.assertLess(got["n"], 200)  # under 3.4 s at 60 frames a second

    def test_an_end_of_the_session_stops_a_window_that_runs_away_from_its_goal(self):
        got = self.node("const out = []; let a = 5, v = -900, there = false, n = 0;"
                        "while (!there && n < 500) { [a, v, there] = czGlide(a, v, 40, MOVE.wheel, 1 / 60, 0.2, 9000); out.push(a); n++; }"
                        "let b = 8990, w = 900, m = 0, hi = 0; there = false;"
                        "while (!there && m < 500) { [b, w, there] = czGlide(b, w, 8900, MOVE.wheel, 1 / 60, 0.2, 9000); hi = Math.max(hi, b); m++; }"
                        "return {low: Math.min(...out), end: a, hi, endB: b};")
        self.assertGreaterEqual(got["low"], 0)
        self.assertEqual((got["end"], got["endB"]), (40, 8900))
        self.assertLessEqual(got["hi"], 9000)

    def test_the_chart_uses_the_spring_and_the_numbers_of_the_map(self):
        mine = self.page.split("// -- The context and the cache of a chat on one chart")[1].split("function drawTrk(")[0]
        for name in ("spring(", "MOVE.focus", "MOVE.wheel", "MOVE.throw", "MOVE.flick", "MOVE.fast", "MOVE.hand", "MOTION", "czHalt()",
                     "throwX(d.pts)", "slideX(dx)"):
            self.assertIn(name, mine, name)
        self.assertEqual(self.page.count("function spring("), 1)
        self.assertEqual(self.page.count("const MOVE = {"), 1)


if __name__ == "__main__":
    unittest.main()
