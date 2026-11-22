"""Disclosure: how much of a memory a view shows to a person.

A view is read by a person, and a person shares a screen. So a view has a level, and the level is the most that it shows:

    0 hidden      nothing: the profile or the session opted out of views (the ports "blocked" and "hide" of the harness)
    1 structure   kinds, ids, times, counts and token numbers; no word of a title, a cue or a text
    2 handles     and the handles and titles, cut short; no body text                           (the default)
    3 public      and the text of public crumbs
    4 private     and the text of private crumbs, word for word

A title is the first line of a text, cut short: it shows from level 2. The cue words of a handle are not a title: they are
the most distinct words of all the text under a crumb, so they are body text and show only where that text shows (3 for a
crumb that is public with all under it, else 4). That two chats share words is a number, and shows at 2; which words is not.

Two numbers decide the level of an answer, and the server holds both:

  the ceiling   the most that this process may show. It is set where the viewer starts and nowhere else: `--disclose N` of
                serve, open, term and spacegraph, or BREADCRUMBS_DISCLOSE=N for a process that embeds a View (a dashboard).
                With neither it is 3: private text is never one click away.
  the level     what a page (or a terminal viewer) shows now: 2 until it asks for another one. A page asks with `ask`; the
                answer is a grant, a random word that the page sends with each request (`d=`). A grant is of one page: a
                second tab, a reload and a restart of the server all start at 2 again. A grant above the default runs out
                (BREADCRUMBS_DISCLOSE_TTL seconds, 900 unless set), and the view is at 2 again.

Level 4 needs both keys: the ceiling of the launch, and a confirmation by hand in the view: the drag bar of the page (slide
to unlock; a finger can do it) or the arrow keys of the terminal viewer. No phrase is typed. A page cannot raise the
ceiling: there is no request that does.

Each ask writes one line to <home>/disclosure.log, the granted ones and the refused ones: the time, the process, the store,
the level before and the level asked, the ceiling, how it was confirmed, who asked, and whether it was granted. <home> is
the base of the store (where ace.log and usage.log are).

What this module does not do: it masks no secret (the store masks secrets when it writes them), and it does not limit the
tools of the model (they read the store through the reader of common, not through a view).
"""
import getpass
import os
import re
import secrets
import threading
import time
from urllib.parse import quote, unquote

HIDDEN, STRUCTURE, HANDLES, PUBLIC, PRIVATE = 0, 1, 2, 3, 4
NAMES = {HIDDEN: "hidden", STRUCTURE: "structure", HANDLES: "handles", PUBLIC: "public", PRIVATE: "private"}
WHAT = {
    STRUCTURE: "Structure only: kinds, times, counts and token numbers. No word of a title or a text.",
    HANDLES: "Handles and titles, cut short. No body text and no cue words.",
    PUBLIC: "The text of public crumbs. A private crumb keeps its handle.",
    PRIVATE: "The text of private crumbs, word for word.",
}
DEFAULT = HANDLES       # what a view shows until it is asked for another level
CEILING = PUBLIC        # the ceiling of a process that was started with no flag
ENV, ENV_TTL = "BREADCRUMBS_DISCLOSE", "BREADCRUMBS_DISCLOSE_TTL"
TTL = 900.0             # seconds that a grant above the default lasts
LOG = "disclosure.log"
TITLE = 40              # a title at level 2 is cut to this many characters (a handle cuts it to the same)
CONFIRM = ("drag", "keys")  # how a hand confirms level 4: the drag bar of the page, the arrow keys of the terminal viewer
GRANTS = 64             # live grants of one process; the oldest goes first

HANDLE_MARK, GAP_MARK = "\u25b8", "\u22ef"
# a handle line of zoom.py: indent, mark, id, kind, an optional title in quotes, the size, an optional tail (cue words, a CID)
_HANDLE = re.compile(r'^(\s*)\u25b8 (\S+) (\w+)( "(?:[^"\\]|\\.)*")?( [^\u00b7]*?)?( \u00b7\d+t)(.*)$')
_GAP = re.compile(r"^(\s*)\u22ef (\S+)")
_COUNT = re.compile(r"^ \d+ lines$")  # of the handle of a text: "<id> doc 12 lines"
_SOURCE = re.compile(r" \u21d0 \S+")  # the CID that a crumb came from


def level_of(value, default=None):
    """A level from a flag or a variable: "0".."4" or a name of NAMES; `default` for anything else."""
    text = str(value if value is not None else "").strip().lower()
    if text.isdigit() and int(text) in NAMES:
        return int(text)
    for n, name in NAMES.items():
        if text == name:
            return n
    return default


def ceiling(flag=None, env=os.environ):
    """The ceiling of this process: the flag of the command line, else BREADCRUMBS_DISCLOSE, else 3. Never under 1: level 0
    is the opt-out of a profile, and a viewer that may show nothing does not start."""
    top = level_of(flag)
    if top is None:
        top = level_of(env.get(ENV), CEILING)
    return max(STRUCTURE, min(PRIVATE, top))


def ttl(env=os.environ):
    try:
        return max(1.0, float(env.get(ENV_TTL) or TTL))
    except ValueError:
        return TTL


def cut(text, n=TITLE):
    """A title, cut short: at most n characters, with a mark where it was cut."""
    text = " ".join(str(text or "").split())
    return text if len(text) <= n else text[:n - 1].rstrip() + "\u2026"


def title(text, level, public=False):
    """The title of a crumb at a level: none under 2, cut short at 2 (and at 3 for a private crumb), whole above."""
    if level >= PRIVATE or (level >= PUBLIC and public):
        return text or ""
    return "" if level < HANDLES else cut(text)


def body(level, public=False):
    """True when the words of a text may show at this level: the cue words of a crumb, the query of a recall, the text
    itself. public: the crumb is public, with everything that the words come from."""
    return level >= PRIVATE or (level >= PUBLIC and bool(public))


def words(items, level, public=False):
    """Cue words (and the words that two sessions share): they are words of the body, so see body()."""
    return list(items or []) if body(level, public) else []


def label(kind, text, level, ref=""):
    """The label of a crumb where its cue words may not show: the kind, then its title cut short (level 2), else the kind
    alone. ref: what stands for a crumb with no title at level 2 (its id)."""
    if level < HANDLES:
        return kind
    short = cut(text, 28)
    return "%s %s" % (kind, short or ref) if (short or ref) else kind


# the keys of a crumb's meta whose value is an id, a role or a name of a program: no word of a text
META_NAMES = frozenset(("role", "sess", "step", "prev", "cid", "ev", "band", "model", "prov", "provider", "ctx", "task", "src",
                        "kind", "how", "head", "doc", "parent", "harness", "gear"))
META_IDS = frozenset(("items", "tools", "calls"))  # lists of crumb ids, of the names of the tools of a step, of its call ids


def meta(m, level, public=False):
    """The meta of a crumb at a level. A number, a truth value and a list of them say nothing in words: they stay. A string
    stays under a key that holds an id, a role or a name (META_NAMES); any other string (the query of a recall, the brief
    of a delegation) is emptied, and the key stays so that a reader finds it."""
    if body(level, public) or not isinstance(m, dict):
        return m
    out = {}
    for k, v in m.items():
        if isinstance(v, str):
            out[k] = v if k in META_NAMES else ""
        elif isinstance(v, (list, tuple)):
            out[k] = [x for x in v if not isinstance(x, (str, dict, list)) or (isinstance(x, str) and k in META_IDS)]
        elif isinstance(v, dict):
            out[k] = {}
        else:
            out[k] = v
    return out


def handle(line, level):
    """One handle line at a level, or None for a line that is no handle and no gap (a line of text). At 2 the title is cut
    short and the cue words go (they are words of the body); at 1 the title goes too. The count of the lines of a text
    ("12 lines") and the id of a source (after the arrow) are no words of a text: they stay."""
    m = _HANDLE.match(line)
    if m:
        pad, ref, kind, quoted, extra, size, tail = m.groups()
        extra = extra if extra and _COUNT.match(extra) else ""
        source = _SOURCE.search(tail or "")
        tail = source.group(0) if source else ""
        if level < HANDLES:
            return "%s%s %s %s%s%s%s" % (pad, HANDLE_MARK, ref, kind, extra, size, tail)
        if quoted:
            inner = quoted[2:-1]
            quoted = ' "%s"' % (inner if len(inner) <= TITLE else inner[:TITLE - 1].rstrip() + "\u2026")
        return "%s%s %s %s%s%s%s%s" % (pad, HANDLE_MARK, ref, kind, quoted or "", extra, size, tail)
    m = _GAP.match(line)
    if m:
        return "%s%s %s" % (m.group(1), GAP_MARK, m.group(2))
    return None


def outline(text, level=HANDLES):
    """The handles and the gaps of a rendered crumb, and nothing else: every line of text is dropped. This is the last
    step of every answer under level 3, so no body text passes whatever made `text`."""
    kept = [h for h in (handle(line, level) for line in str(text or "").split("\n")) if h is not None]
    return "\n".join(kept) + ("\n" if kept else "")


def who(peer="", via=""):
    """Who asked: the user of this process, where the ask came from (an address, for a page), and by what (page, term)."""
    try:
        user = getpass.getuser()
    except (KeyError, OSError):
        user = str(os.getuid()) if hasattr(os, "getuid") else "?"
    return {"user": user, "peer": peer or "-", "via": via or "-"}


def audit(base, clock=time.time, **fields):
    """One line in <base>/disclosure.log: the time, then key=value for each field (a value holds no space). The file is
    made with mode 600. True when the line was written; a log that cannot be written refuses the ask (see Gate.ask)."""
    stamp = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(clock()))
    line = stamp + "".join(" %s=%s" % (k, quote(str(v), safe="/~.:@+,-_") or "-") for k, v in fields.items()) + "\n"
    try:
        fd = os.open(os.path.join(base, LOG), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        with os.fdopen(fd, "a", encoding="utf-8") as f:
            f.write(line)
        return True
    except OSError:
        return False


def read_log(base, limit=20):
    """The last lines of the log as dicts ({"ts": ..., key: value}), oldest first."""
    try:
        with open(os.path.join(base, LOG), encoding="utf-8", errors="replace") as f:
            lines = f.readlines()[-limit:]
    except OSError:
        return []
    out = []
    for line in lines:
        parts = line.split()
        if parts:
            row = {"ts": parts[0]}
            for part in parts[1:]:
                key, eq, value = part.partition("=")
                if eq:
                    row[key] = unquote(value)
            out.append(row)
    return out


class Refused(PermissionError):
    """An ask that the gate does not grant: over the ceiling, or level 4 with no confirmation by hand."""


class Gate:
    """The gate of one process for one store: its ceiling, and the grants of its pages.

    base: the folder of disclosure.log (the base of the store). store: the store that the view reads (named in each line).
    top: the ceiling from the launch flag (None: BREADCRUMBS_DISCLOSE, else 3)."""

    def __init__(self, base, store="", top=None, clock=time.time, env=os.environ):
        self.base, self.store, self.clock = base, store or base, clock
        self.ceiling = ceiling(top, env)
        self.default = min(DEFAULT, self.ceiling)
        self.ttl = ttl(env)
        self._grants = {}  # grant -> [level, until]; in the order granted
        self._lock = threading.Lock()

    def level(self, grant=""):
        """The level of a request: the level of its grant while the grant lasts, else the default."""
        with self._lock:
            got = self._grants.get(grant or "")
            if got is None:
                return self.default
            if got[1] is not None and self.clock() >= got[1]:
                del self._grants[grant]
                return self.default
            return min(got[0], self.ceiling)

    def state(self, grant=""):
        """What a view shows of the gate: the level now, the default, the ceiling, when the grant runs out (None: it does
        not), and each level with its name, what it shows, and whether this process may show it."""
        level = self.level(grant)
        with self._lock:
            until = (self._grants.get(grant or "") or [0, None])[1]
        return {"level": level, "name": NAMES[level], "default": self.default, "ceiling": self.ceiling, "until": until,
                "now": self.clock(), "ttl": self.ttl, "flag": "--disclose %d" % PRIVATE, "env": ENV,
                "levels": [{"level": n, "name": NAMES[n], "what": WHAT[n], "allowed": n <= self.ceiling,
                            "confirm": n >= PRIVATE} for n in (STRUCTURE, HANDLES, PUBLIC, PRIVATE)]}

    def ask(self, level, how="", grant="", peer="", via="page"):
        """Ask for a level. The answer is state() with the grant to send from now on ("" at the default). Refused (and
        one line in the log says so): a level over the ceiling; level 4 that no hand confirmed (how: one of CONFIRM); a
        log that cannot be written, because a level above the default is never shown with no line of it."""
        want = level_of(level)
        if want is None or want < STRUCTURE:
            raise ValueError("level: 1 to 4")
        fields = {"pid": os.getpid(), "store": self.store, "from": self.level(grant), "level": want, "ceiling": self.ceiling,
                  "how": how or "-", **who(peer, via)}

        def refuse(why, text):
            audit(self.base, self.clock, **fields, ok=0, why=why)
            raise Refused(text)
        if want > self.ceiling:
            refuse("ceiling", "this viewer was started with a ceiling of %d (%s): start it with --disclose %d (or %s=%d) "
                   "to allow level %d" % (self.ceiling, NAMES[self.ceiling], want, ENV, want, want))
        if want >= PRIVATE and how not in CONFIRM:
            refuse("unconfirmed", "level %d needs the drag bar of the page (or the arrow keys of the terminal viewer)" % want)
        until = self.clock() + self.ttl if want > self.default else None
        if not audit(self.base, self.clock, **fields, ok=1, until=int(until) if until else "-") and want > self.default:
            raise Refused("cannot write %s: no level above %d without its line in the log" % (os.path.join(self.base, LOG), self.default))
        with self._lock:
            self._grants.pop(grant or "", None)
            new = ""
            if want != self.default:
                new = secrets.token_hex(12)
                self._grants[new] = [want, until]
                while len(self._grants) > GRANTS:
                    del self._grants[next(iter(self._grants))]
        return dict(self.state(new), grant=new)

    def lock(self, grant="", peer="", via="page"):
        """Back to the default: the grant ends now."""
        return self.ask(self.default, grant=grant, peer=peer, via=via)
