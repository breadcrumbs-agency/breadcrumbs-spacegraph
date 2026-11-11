"""The adapter of breadcrumbs_spacegraph, and a viewer on a store that no harness wrote. Run with breadcrumbs-common
and this folder on PYTHONPATH: the viewers then work on the reader of common, and no harness package is there."""
import contextlib
import io
import os
import sys
import tempfile
import unittest

from breadcrumbs import cli, ports
from breadcrumbs.history import Refs, commit
from breadcrumbs.index import Index
from breadcrumbs.ingest import put_tree, put_turn
from breadcrumbs.store import Store
from breadcrumbs_spacegraph import view


def seed(root):
    """A store with one session of three turns, written with the verbs of common only."""
    store, index, refs = Store(os.path.join(root, "store")), Index(os.path.join(root, "index.db")), Refs(os.path.join(root, "refs"))
    turns = [put_turn(store, index, "user", "the kettle boils too fast for green tea %d" % i, "s1", ts=i) for i in range(3)]
    commit(store, refs, "s1", [put_tree(store, "sess", "tea", turns)], "s1", ts=3)
    index.commit()
    index.close()
    store.close()
    return turns


class Adapter(unittest.TestCase):
    def test_it_gives_the_five_viewer_commands_and_no_port(self):
        self.assertEqual(sorted(c["name"] for c in ports.commands()), ["open", "serve", "spacegraph", "term", "viz"])
        self.assertIsNone(ports.get("hide"))  # a harness gives the ports; this layer gives none
        self.assertEqual(ports.layer("spacegraph", __file__), False)  # it announced its layer once

    def test_the_commands_are_in_the_help_of_common(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), self.assertRaises(SystemExit):
            cli.main(["--help"])
        for name in ("serve", "open", "viz", "term", "spacegraph"):
            self.assertIn(name, out.getvalue())

    def test_the_package_holds_the_page_beside_the_view(self):
        self.assertTrue(os.path.isfile(view.PAGE))
        self.assertEqual(os.path.dirname(view.PAGE), os.path.dirname(os.path.abspath(view.__file__)))

    def test_a_folder_on_the_path_for_a_child_process_holds_both_packages(self):
        folders = ports.folders()
        self.assertIn(os.path.dirname(os.path.dirname(os.path.abspath(view.__file__))), folders)
        self.assertEqual(folders[0], os.path.dirname(os.path.dirname(os.path.abspath(cli.__file__))))


class ViewOnACommonStore(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.turns = seed(self.tmp.name)
        self.view = view.View(self.tmp.name)
        self.addCleanup(self.view.close)

    def test_the_view_finds_and_opens_what_common_wrote(self):
        hits = self.view.find("kettle")
        self.assertTrue(hits)
        self.assertIn("kettle", self.view.crumb(self.turns[0], 1.0)["text"])

    def test_the_graph_has_the_session(self):
        self.assertTrue(self.view.graph())

    def test_no_harness_is_loaded(self):
        self.assertFalse([m for m in sys.modules if m.startswith("breadcrumbs_") and m != "breadcrumbs_spacegraph"
                          and not m.startswith("breadcrumbs_spacegraph.")])


if __name__ == "__main__":
    unittest.main()
