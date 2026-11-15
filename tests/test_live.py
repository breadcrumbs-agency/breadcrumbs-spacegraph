"""The live page: api/changes stamps only this store, its profile's logs and this chat's config chain."""
import os
import tempfile
import unittest

from breadcrumbs import ports
from breadcrumbs_spacegraph import view
from test_harness import seed


class Live(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        home = os.path.join(self.tmp.name, "profile", "breadcrumbs")
        self.base = home
        self.scope = os.path.join(home, "scopes", "tea-00000000")
        self.other = os.path.join(home, "scopes", "other-11111111")
        for d in (self.scope, self.other):
            os.makedirs(d)
            seed(d)
        for name in ("ace.log", "usage.log"):
            with open(os.path.join(home, name), "w") as f:
                f.write("x\n")
        self.saved = ports._given.get("config_layers")
        self.addCleanup(self.restore)

    def restore(self):
        if self.saved is None:
            ports._given.pop("config_layers", None)
        else:
            ports._given["config_layers"] = self.saved

    def write(self, path, text):
        with open(path, "w") as f:
            f.write(text)

    def chain(self):
        """A profile file (the base) above each scope's own file: the chain settings resolves, per root of the view."""
        def layers(root):
            return [
                {"id": "profile", "kind": "profile", "label": "profile", "path": self.base, "parent": None,
                 "applies": "every chat", "here": False},
                {"id": "store", "kind": "store", "label": "this memory", "path": root, "parent": "profile",
                 "applies": "this chat", "here": True},
            ]
        ports.give("config_layers", layers)

    def test_stamp_has_this_store_and_its_chain_only(self):
        self.chain()
        v = view.View(self.scope)
        self.addCleanup(v.close)
        got = v.changes()
        self.assertEqual(got["scope"], view.scopes.scope_id(self.scope))
        self.assertEqual(got["profile"], "profile")
        self.assertEqual(set(got["config"]), {"profile", "store"})

    def test_a_change_to_this_chat_moves_its_stamp(self):
        self.chain()
        v = view.View(self.scope)
        self.addCleanup(v.close)
        before = v.changes()
        self.write(os.path.join(self.scope, "config.json"), '{"hive": {"ms": 200}}')
        after = v.changes()
        self.assertNotEqual(before["config"]["store"], after["config"]["store"])
        self.assertEqual(before["config"]["profile"], after["config"]["profile"])
        self.assertEqual(before["store"], after["store"])
        self.assertEqual(before["logs"], after["logs"])

    def test_a_change_to_another_scope_or_profile_is_not_stamped(self):
        self.chain()
        v = view.View(self.scope)
        self.addCleanup(v.close)
        before = v.changes()
        self.write(os.path.join(self.other, "config.json"), '{"hive": {"ms": 300}}')
        other_view = view.View(self.other)
        self.addCleanup(other_view.close)
        self.assertNotEqual(other_view.changes()["config"], before["config"])  # the other scope sees its own file
        after = v.changes()
        self.assertEqual(before["config"], after["config"])
        self.assertEqual(before["store"], after["store"])

    def test_a_log_line_moves_the_logs_stamp_and_a_new_crumb_moves_the_store_stamp(self):
        self.chain()
        v = view.View(self.scope)
        self.addCleanup(v.close)
        before = v.changes()
        with open(os.path.join(self.base, "ace.log"), "a") as f:
            f.write("keep fold\n")
        self.assertNotEqual(before["logs"], v.changes()["logs"])
        self.assertEqual(before["store"], v.changes()["store"])
        os.utime(os.path.join(self.scope, "index.db"), (1, 1))
        self.assertNotEqual(before["store"], v.changes()["store"])


if __name__ == "__main__":
    unittest.main()


class LiveDial(unittest.TestCase):
    """The dial live reaches the page as __LIVE__, read from the top of config.json, as update_ms is."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = os.path.join(self.tmp.name, "scope")
        os.makedirs(self.root)
        seed(self.root)
        self.env = os.environ.pop("BREADCRUMBS_LIVE", None)
        self.addCleanup(self.restore_env)

    def restore_env(self):
        if self.env is not None:
            os.environ["BREADCRUMBS_LIVE"] = self.env

    def test_default_is_on(self):
        html = view.page(self.root).decode("utf-8")
        self.assertIn('const LIVE_ON = "1" !== "0";', html)

    def test_off_in_config_reaches_the_page(self):
        with open(os.path.join(self.root, "config.json"), "w") as f:
            f.write('{"live": false}')
        html = view.page(self.root).decode("utf-8")
        self.assertIn('const LIVE_ON = "0" !== "0";', html)
