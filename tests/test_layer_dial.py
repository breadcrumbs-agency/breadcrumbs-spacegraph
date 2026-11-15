"""The live dials of a namespace: the explorer page reads update_ms and live through the scope's config chain.

A namespace (scope) config.json beats its profile's config.json at each dial; the profile's file is the base.
"""
import json
import os
import tempfile
import unittest

from breadcrumbs_spacegraph import view


def _write(folder, cfg):
    os.makedirs(folder, exist_ok=True)
    with open(os.path.join(folder, "config.json"), "w", encoding="utf-8") as f:
        json.dump(cfg, f)


class LayerDial(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.profile = os.path.join(tmp.name, "profile")
        self.scope = os.path.join(tmp.name, "profile", "scopes", "repo-1")
        os.makedirs(self.profile)
        os.makedirs(self.scope)

    def test_no_layers_reads_the_profile_top_level(self):
        _write(self.profile, {"update_ms": 3000, "live": True})
        html = view.page(self.profile).decode("utf-8")
        self.assertIn('Number("3000") || 2000', html)
        self.assertIn('const LIVE_ON = "1" !== "0"', html)

    def test_scope_value_beats_profile_value(self):
        _write(self.profile, {"update_ms": 3000, "live": True})
        _write(self.scope, {"update_ms": 750})
        html = view.page(self.profile, [self.profile, self.scope]).decode("utf-8")
        self.assertIn('Number("750") || 2000', html)

    def test_profile_value_stands_where_scope_is_silent(self):
        _write(self.profile, {"update_ms": 3000, "live": False})
        _write(self.scope, {"update_ms": 750})
        html = view.page(self.profile, [self.profile, self.scope]).decode("utf-8")
        self.assertIn('Number("750") || 2000', html)
        self.assertIn('const LIVE_ON = "0" !== "0"', html)

    def test_scope_can_switch_live_off(self):
        _write(self.profile, {"live": True})
        _write(self.scope, {"live": False})
        html = view.page(self.profile, [self.profile, self.scope]).decode("utf-8")
        self.assertIn('const LIVE_ON = "0" !== "0"', html)


if __name__ == "__main__":
    unittest.main()
