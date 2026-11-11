# breadcrumbs-spacegraph

Every viewer of breadcrumbs: the explorer page and its read-only server, the terminal viewers for Kitty and Ghostty, and the commands `serve`, `open`, `viz`, `term` and `spacegraph`.

- Needs: breadcrumbs-common. A viewer for a window needs Kitty or Ghostty, or a browser.
- It imports no harness. A harness gives it data through the ports of breadcrumbs-common.
- Test: `PYTHONPATH=../breadcrumbs-common:. python -m unittest discover -s tests`.

MIT licence. Part of breadcrumbs-agency.
