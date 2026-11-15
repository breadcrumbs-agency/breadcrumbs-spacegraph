"""The explorer settings endpoint keeps common and harness settings separate."""
import json
import os
import tempfile
import threading
import unittest
import urllib.request

from breadcrumbs import ports
from breadcrumbs_spacegraph import view
from test_harness import seed


class Settings(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = self.tmp.name
        seed(self.root)
        self.saved = dict(ports._harnesses)
        ports._harnesses.clear()
        self.addCleanup(self.restore_ports)

    def restore_ports(self):
        ports._harnesses.clear()
        ports._harnesses.update(self.saved)

    def test_common_crud_and_harness_crud_are_separate(self):
        seen = []

        def custom(root, action, payload):
            seen.append((action, payload.get("key")))
            return {"key": "fake", "label": "Fake", "fields": [], "enabled": False}

        ports.harness("fake", "Fake", ("fake",), lambda cmd: "", order=1, settings=custom)
        current = view.View(self.root)
        self.addCleanup(current.close)

        got = current.settings("get", {"harness": "fake"})
        self.assertEqual(got["harness"]["key"], "fake")
        self.assertEqual(seen, [("get", None)])

        current.settings("set", {"target": "common", "key": "update_ms", "value": 750, "harness": "fake"})
        with open(os.path.join(self.root, "config.json"), encoding="utf-8") as stream:
            self.assertEqual(json.load(stream)["update_ms"], 750)
        self.assertEqual(seen, [("get", None), ("get", "update_ms")])

        current.settings("set", {"target": "harness", "key": "custom", "value": True, "harness": "fake"})
        self.assertEqual(seen[-1], ("set", "custom"))

    def test_authenticated_http_mutation_changes_only_the_common_dial(self):
        server = view.make_server(self.root, port=0)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        url = "http://127.0.0.1:%d/api/settings?k=%s" % (server.server_address[1], view.token(self.root))
        request = urllib.request.Request(url, data=json.dumps({"action": "set", "target": "common",
                                                                "key": "update_ms", "value": 900}).encode(),
                                         headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(request, timeout=10) as response:
            body = json.loads(response.read())
        self.assertEqual(body["common"]["model"], "")
        with open(os.path.join(self.root, "config.json"), encoding="utf-8") as stream:
            self.assertEqual(json.load(stream)["update_ms"], 900)

    def test_the_page_draws_bounded_dials_as_sliders_and_switches(self):
        page = open(os.path.join(os.path.dirname(view.__file__), "view.html"), encoding="utf-8").read()
        for needle in ('slider.type = "range"', 'classList.add("switch")', "control._wrap || control", "input.switch"):
            self.assertIn(needle, page)

    def test_the_harness_console_comes_before_the_common_dials(self):
        page = open(os.path.join(os.path.dirname(view.__file__), "view.html"), encoding="utf-8").read()
        self.assertLess(page.index('id="settings-harness"'), page.index('id="settings-common"'))

    def test_grow_and_fold_dials_come_in_their_own_groups_in_order(self):
        current = view.View(self.root)
        self.addCleanup(current.close)
        groups = {g["name"]: [r["key"] for r in g["rows"]] for g in current.settings("get", {})["common"]["groups"]}
        names = list(groups)
        self.assertEqual(names[:4], ["ACE thresholds (per model)", "ACE grow (per model)",
                                     "ACE fold (per model)", "ACE gears (per model)"])
        self.assertIn("ace_ratchet", groups["ACE grow (per model)"])
        self.assertIn("ace_fold_to", groups["ACE fold (per model)"])
        self.assertIn("ace_tokens", groups["ACE thresholds (per model)"])

    def test_the_config_file_is_chosen_by_its_id_and_a_scope_reads_through_its_profile(self):
        profile, scoped = os.path.join(self.root, "prof"), os.path.join(self.root, "prof", "scopes", "repo-1")
        os.makedirs(scoped)
        with open(os.path.join(profile, "config.json"), "w", encoding="utf-8") as f:
            json.dump({"neighbor": 0.3, "ace_tokens": 250000}, f)
        before = ports._given.get("config_layers")
        self.addCleanup(lambda: ports._given.__setitem__("config_layers", before) if before else
                        ports._given.pop("config_layers", None))
        ports.give("config_layers", lambda root: [
            {"id": "profile:p", "kind": "profile", "label": "profile p", "path": profile, "parent": None,
             "applies": "every chat of p", "profile": "p", "here": False},
            {"id": "scope:p:repo-1", "kind": "scope", "label": "p / ~/repo", "path": scoped, "parent": "profile:p",
             "applies": "the chats of p in ~/repo", "profile": "p", "here": False}])
        current = view.View(self.root)
        self.addCleanup(current.close)

        got = current.settings("get", {})
        self.assertEqual(got["layer"], "store")  # no file of the list is this view's: its own config.json comes first
        self.assertEqual([l["id"] for l in got["layers"]], ["store", "profile:p", "scope:p:repo-1"])

        got = current.settings("set", {"target": "common", "key": "ace_tokens", "value": 120000, "layer": "scope:p:repo-1"})
        rows = {r["key"]: r for g in got["common"]["groups"] for r in g["rows"]}
        self.assertEqual((rows["ace_tokens"]["value"], rows["ace_tokens"]["source"]), (120000, "scope"))
        self.assertEqual((rows["neighbor"]["value"], rows["neighbor"]["source"]), (0.3, "profile"))
        with open(os.path.join(scoped, "config.json"), encoding="utf-8") as f:
            self.assertEqual(json.load(f), {"ace_tokens": 120000})
        with open(os.path.join(profile, "config.json"), encoding="utf-8") as f:
            self.assertEqual(json.load(f)["ace_tokens"], 250000)  # the profile's file is untouched
        self.assertFalse(os.path.exists(os.path.join(self.root, "config.json")))

        with self.assertRaisesRegex(ValueError, "unknown config file"):
            current.settings("set", {"target": "common", "key": "ace_tokens", "value": 1, "layer": "/etc"})

    def test_the_page_offers_the_config_files_and_sends_the_choice(self):
        page = open(os.path.join(os.path.dirname(view.__file__), "view.html"), encoding="utf-8").read()
        for needle in ('id="settings-layer"', "layer: settingsLayer", 'id="settings-applies"', "optgroup"):
            self.assertIn(needle, page)

if __name__ == "__main__":
    unittest.main()
