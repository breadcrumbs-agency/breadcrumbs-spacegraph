"""The adapter of the package breadcrumbs_spacegraph: it gives the commands that draw to `python -m breadcrumbs`
(serve, open, term, spacegraph). `viz` is in common (breadcrumbs/viz.py): this layer gives it the ports "explorer",
"term" and "term_argv". The reader that common needs is breadcrumbs/reader.py, and the viewers build on it. ports.load() imports this module by the name of its package (breadcrumbs_*); no module
imports it.

Each function imports its module when it is called, not before: a command that draws nothing loads no viewer, and a
patched name of a module (a test) is read at the call. The part of spacegraph imports no module of a harness; it asks
the ports, as view.py does.
"""
import os
import sys

from breadcrumbs import ports


# -- the commands that draw ---------------------------------------------------------------------------------------
def _draws(run):
    """run for a command that draws: a profile that opted out of visualization (the port "blocked") gets one line."""
    def guarded(args, home):
        blocked = ports.get("blocked")
        why = blocked(os.path.dirname(os.path.abspath(home))) if blocked and not getattr(args, "url", None) else ""
        if why:
            print("breadcrumbs: %s" % why, file=sys.stderr)
            return 1
        return run(args, home)
    return guarded


DISCLOSE = ("how much a view may show: 1 structure, 2 handles and titles, 3 the text of public crumbs (the most with no "
            "flag), 4 private text too. A view starts at 2 whatever this says; 4 also needs the drag bar of the view. "
            "Or set BREADCRUMBS_DISCLOSE")


def _add_disclose(p):
    p.add_argument("--disclose", type=int, choices=(1, 2, 3, 4), default=None, metavar="LEVEL", help=DISCLOSE)


def _add_serve(sub):
    p = sub.add_parser("serve", help="the explorer page on 127.0.0.1 (read-only)")
    p.add_argument("--port", type=int, default=8765); p.add_argument("--open", action="store_true")
    _add_disclose(p)


def _serve(args, home):
    from .view import make_server, token
    server = make_server(home, port=args.port, disclose=args.disclose)
    url = "http://127.0.0.1:%d/#k=%s" % (server.server_address[1], token(home))
    print("breadcrumbs explorer (read-only):", url, flush=True)
    if args.open:
        import webbrowser
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


def _add_open(sub):
    p = sub.add_parser("open", help="the explorer in the default browser, or in a new Kitty or Ghostty window")
    p.add_argument("where", nargs="?", choices=("web", "kitty", "ghostty"), default="web")
    p.add_argument("--print", dest="show", action="store_true", help="show the command line that would run; run nothing")
    p.add_argument("--port", type=int, default=None, help="web: the port of the server (default 8765, else a free port)")
    _add_disclose(p)


def _open(args, home):
    from . import launch
    return launch.run(args.where, home, port=args.port, show=args.show, disclose=args.disclose)


def _explorer(home, port=8765, harness=None):
    """The port "explorer" of common's viz: the URL of this memory's explorer, its server started or reused."""
    from .launch import explorer_url
    return explorer_url(home, port, harness)


def _term_port(url):
    """The port "term" of viz: the page drawn in this terminal, or None when it is no Kitty or Ghostty."""
    from . import kitty, term
    return term.run(url) if kitty.supported() else None


def _term_argv(home, harness=None):
    """The port "term_argv" of viz: the command line of `term` for this memory (a tmux pane runs it)."""
    from .launch import term_argv
    return term_argv(home, harness)


def _add_term(sub):
    p = sub.add_parser("term", help="the explorer page in Kitty or Ghostty (Kitty graphics protocol)")
    p.add_argument("--url", default=None, help="default: serve --home on a free port")
    p.add_argument("--every", type=float, default=10.0, help="seconds per frame")
    p.add_argument("--width", type=int, default=1280)
    p.add_argument("--height", type=int, default=800)
    p.add_argument("--wait-ms", type=int, default=8000, help="page time for its API calls (Chrome virtual time)")
    p.add_argument("--frames", type=int, default=0, help="stop after N frames (0: until Ctrl-C)")
    p.add_argument("--disclose", type=int, choices=(1, 2, 3), default=None, metavar="LEVEL",
                   help="the level of the picture: 1 structure, 2 handles and titles (the default), 3 the text of public "
                        "crumbs. Not 4: term takes no input, so no hand can confirm it (use `spacegraph` or the page)")


def _term(args, home):
    from . import term
    server = None
    if args.url is None:
        import threading
        from .view import make_server, token
        server = make_server(home, port=0, disclose=args.disclose)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        args.url = "http://127.0.0.1:%d/#k=%s&motion=0" % (server.server_address[1], token(home))  # one picture: the camera of before
        if args.disclose and args.disclose != 2:
            args.url += "&dl=%d" % args.disclose  # the page asks for this level when it loads (1 or 3: no hand is needed)
    try:
        return term.run(args.url, every=args.every, width=args.width, height=args.height,
                        budget_ms=args.wait_ms, frames=args.frames)
    finally:
        if server:
            server.shutdown()
            server.server_close()


def _add_spacegraph(sub):
    p = sub.add_parser("spacegraph", help="native SpaceGraph viewer in Kitty/Ghostty (no Chrome)")
    p.add_argument("--port", type=int, default=8765, help="port for the view API server"); p.add_argument("--motion", choices=("0", "1"), default=None, help="0: the camera of the old page (no momentum); or BREADCRUMBS_MOTION=0")
    _add_disclose(p)


def _spacegraph(args, home):
    from .spacegraph_kitty import SpaceGraphKitty
    app = SpaceGraphKitty(home, port=args.port, motion=args.motion, disclose=args.disclose)
    app.run()
    return 0


if ports.layer("spacegraph", __file__):
    ports.command("serve", _add_serve, _draws(_serve), before="core")
    ports.command("open", _add_open, _draws(_open), before="core")
    ports.give("explorer", _explorer)  # the explorer of `viz`, which is in common (breadcrumbs/viz.py)
    ports.give("term", _term_port)
    ports.give("term_argv", _term_argv)
    ports.command("term", _add_term, _draws(_term), after="help")
    ports.command("spacegraph", _add_spacegraph, _draws(_spacegraph), after="help")
