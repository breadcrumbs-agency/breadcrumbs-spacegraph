"""The page's poll interval: the dial update_ms reaches the explorer page (view.py page()), and the page reads it."""
import os
import tempfile
import unittest

from breadcrumbs_spacegraph import view


class UpdateDial(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = tmp.name

    def test_the_page_carries_the_default_interval_and_no_placeholder(self):
        html = view.page(None).decode("utf-8")
        self.assertIn('Number("2000") || 2000', html)
        self.assertNotIn("__UPDATE_MS__", html)

    def test_the_dial_of_the_store_is_in_the_page(self):
        with open(os.path.join(self.root, "config.json"), "w") as f:
            f.write('{"update_ms": 750}')
        html = view.page(self.root).decode("utf-8")
        self.assertIn('Number("750") || 2000', html)

    def test_the_page_polls_from_the_constant_and_not_from_numbers(self):
        with open(view.PAGE, encoding="utf-8") as f:
            html = f.read()
        self.assertIn("setInterval(follow, UPDATE_MS); setInterval(context, UPDATE_MS); setInterval(trk, UPDATE_MS);", html)
        self.assertIn("}, 3 * UPDATE_MS);", html)
        self.assertNotIn("setInterval(follow, 2000)", html)
        self.assertLess(html.index("const UPDATE_MS"), html.index("}, 3 * UPDATE_MS);"))  # defined before first use


if __name__ == "__main__":
    unittest.main()
