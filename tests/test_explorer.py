"""The explorer of `viz` (launch.explorer_url, the port "explorer"), and the token of a memory with no folder yet."""
import os
import tempfile
import unittest
from unittest import mock

from breadcrumbs_spacegraph import launch, view


class Server(unittest.TestCase):
    """explorer_url: the port of another home's server is not taken for this home's."""

    def url(self, answers, listening):
        started = []
        with mock.patch.object(launch.os.path, "isfile", return_value=True), \
                mock.patch.object(launch, "answers", return_value=answers), \
                mock.patch.object(view, "token", return_value="x"), \
                mock.patch.object(launch, "listening", side_effect=listening), \
                mock.patch.object(launch, "free_port", return_value=4321), \
                mock.patch.object(launch.subprocess, "Popen", lambda argv, **kw: started.append(argv)), \
                mock.patch.object(launch.time, "sleep"):
            return launch.explorer_url("home"), started

    def test_a_server_of_this_home_is_used_again(self):
        self.assertEqual(self.url(True, [AssertionError]), ("http://127.0.0.1:8765/#k=x", []))

    def test_a_port_that_another_server_holds_gives_a_free_port(self):
        url, started = self.url(False, [True, True])
        self.assertEqual(url, "http://127.0.0.1:4321/#k=x")
        self.assertEqual(started[0][-4:], ["home", "serve", "--port", "4321"])

    def test_a_free_port_gets_the_server(self):
        url, started = self.url(False, [False, True])
        self.assertEqual(url, "http://127.0.0.1:8765/#k=x")
        self.assertEqual(started[0][-2:], ["--port", "8765"])


class NoSessions(unittest.TestCase):
    """A memory with no index.db (a project with no session yet): no server starts, and no token is made."""

    def test_it_refuses_before_any_server_starts(self):
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(launch.subprocess, "Popen") as popen:
            with self.assertRaises(LookupError):
                launch.explorer_url(os.path.join(tmp, "scopes", "new-1234abcd"))
            popen.assert_not_called()
            self.assertFalse(os.path.exists(os.path.join(tmp, "scopes")))


if __name__ == "__main__":
    unittest.main()
