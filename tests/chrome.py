"""A headless Chrome for the tests of the page, driven over the DevTools protocol with the standard library only.

    with Chrome(1280, 800) as tab:
        tab.goto(url)
        tab.wait("document.title.length > 0")
        tab.click(100, 20)
        tab.screenshot(path)

It starts Chrome (term.find_chrome: BREADCRUMBS_CHROME, /Applications, PATH) with a scratch profile and no window, reads the
address of its debugger from the file DevToolsActivePort, and talks to one tab over a WebSocket (RFC 6455: the few frames
that the protocol needs). available() says whether a Chrome is there: the tests of the page skip when it is not.

The pointer, the finger and the keys are real input events of the browser (Input.dispatchMouseEvent, dispatchTouchEvent,
dispatchKeyEvent), not calls of the page's functions: a test that drags the bar proves that a hand can.
"""
import base64
import json
import os
import shutil
import socket
import struct
import subprocess
import tempfile
import time
import urllib.request

from breadcrumbs_spacegraph import term

KEYS = {  # key -> (code, the Windows virtual key code, the text that it types)
    "Escape": ("Escape", 27, ""), "Enter": ("Enter", 13, "\r"), "Backspace": ("Backspace", 8, ""), "Tab": ("Tab", 9, ""),
    "ArrowLeft": ("ArrowLeft", 37, ""), "ArrowUp": ("ArrowUp", 38, ""), "ArrowRight": ("ArrowRight", 39, ""),
    "ArrowDown": ("ArrowDown", 40, ""), "Home": ("Home", 36, ""), "End": ("End", 35, ""), " ": ("Space", 32, " "),
    "?": ("Slash", 191, "?"), "/": ("Slash", 191, "/"), "+": ("Equal", 187, "+"), "-": ("Minus", 189, "-"), "0": ("Digit0", 48, "0"),
    "ContextMenu": ("ContextMenu", 93, ""), "F10": ("F10", 121, ""),
}
SHIFT, CTRL, ALT, META = 8, 2, 1, 4


def available():
    return term.find_chrome() is not None


class ChromeError(RuntimeError):
    pass


class Chrome:
    def __init__(self, width=1280, height=800, scale=1, touch=False, timeout=20.0):
        chrome = term.find_chrome()
        if not chrome:
            raise ChromeError("no Chrome or Chromium (set BREADCRUMBS_CHROME)")
        self.timeout, self.events, self.sock, self.buf, self.n = timeout, [], None, b"", 0
        self.profile = tempfile.mkdtemp(prefix="breadcrumbs-test-chrome-")
        self.proc = subprocess.Popen(
            [chrome, "--headless=new", "--disable-gpu", "--hide-scrollbars", "--no-first-run", "--no-default-browser-check",
             "--disable-extensions", "--disable-background-networking", "--disable-sync", "--mute-audio",
             # a window that no one sees must still draw: without these the page turns "hidden" after a few loads, its
             # requestAnimationFrame stops, and the camera of the map never moves
             "--disable-backgrounding-occluded-windows", "--disable-renderer-backgrounding", "--disable-background-timer-throttling",
             "--remote-debugging-port=0", "--user-data-dir=" + self.profile, "--window-size=%d,%d" % (width, height),
             "about:blank"], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            port = self._port()
            with urllib.request.urlopen("http://127.0.0.1:%d/json/list" % port, timeout=timeout) as r:
                pages = [t for t in json.loads(r.read()) if t.get("type") == "page"]
            if not pages:
                raise ChromeError("Chrome gave no tab")
            self._connect(port, pages[0]["webSocketDebuggerUrl"])
            for domain in ("Page", "Runtime"):
                self.send(domain + ".enable")
            self.send("Emulation.setFocusEmulationEnabled", enabled=True)  # the page is the one in front, whatever the window is
            self.size(width, height, scale, touch)
        except BaseException:
            self.close()
            raise

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    # -- the connection --------------------------------------------------------------------------------------------
    def _port(self):
        path, end = os.path.join(self.profile, "DevToolsActivePort"), time.monotonic() + self.timeout
        while time.monotonic() < end:
            if self.proc.poll() is not None:
                raise ChromeError("Chrome exited with status %s" % self.proc.returncode)
            try:
                with open(path, encoding="ascii") as f:
                    first = f.readline().strip()
                if first.isdigit():
                    return int(first)
            except OSError:
                pass
            time.sleep(0.05)
        raise ChromeError("Chrome did not open its debugger port")

    def _connect(self, port, url):
        path = "/" + url.split("/", 3)[3]
        self.sock = socket.create_connection(("127.0.0.1", port), timeout=self.timeout)
        key = base64.b64encode(os.urandom(16)).decode("ascii")
        self.sock.sendall(("GET %s HTTP/1.1\r\nHost: 127.0.0.1:%d\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                           "Sec-WebSocket-Key: %s\r\nSec-WebSocket-Version: 13\r\n\r\n" % (path, port, key)).encode("ascii"))
        head = b""
        while b"\r\n\r\n" not in head:
            got = self.sock.recv(4096)
            if not got:
                raise ChromeError("the debugger closed the connection")
            head += got
        head, _, self.buf = head.partition(b"\r\n\r\n")
        if b" 101 " not in head.split(b"\r\n", 1)[0]:
            raise ChromeError("no WebSocket: %r" % head[:80])

    def _read(self, n):
        while len(self.buf) < n:
            got = self.sock.recv(65536)
            if not got:
                raise ChromeError("the debugger closed the connection")
            self.buf += got
        out, self.buf = self.buf[:n], self.buf[n:]
        return out

    def _frame(self):
        """One message: its frames put together. A ping is answered; a close ends the connection."""
        data = b""
        while True:
            b0, b1 = self._read(2)
            op, n = b0 & 15, b1 & 127
            if n == 126:
                n = struct.unpack(">H", self._read(2))[0]
            elif n == 127:
                n = struct.unpack(">Q", self._read(8))[0]
            mask = self._read(4) if b1 & 128 else None
            body = self._read(n)
            if mask:
                body = bytes(c ^ mask[i % 4] for i, c in enumerate(body))
            if op == 9:
                self._write(body, 10)
                continue
            if op == 8:
                raise ChromeError("the debugger closed the connection")
            if op in (0, 1, 2):
                data += body
                if b0 & 128:
                    return data

    def _write(self, body, op=1):
        n, mask = len(body), os.urandom(4)
        head = bytes([128 | op]) + (bytes([128 | n]) if n < 126 else bytes([128 | 126]) + struct.pack(">H", n) if n < 65536
                                    else bytes([128 | 127]) + struct.pack(">Q", n))
        key = (mask * (n // 4 + 1))[:n]
        self.sock.sendall(head + mask + (int.from_bytes(body, "big") ^ int.from_bytes(key, "big")).to_bytes(n, "big") if n else head + mask)

    def send(self, method, **params):
        """One command and its result. Events that come first are kept in self.events."""
        self.n += 1
        mine = self.n
        self._write(json.dumps({"id": mine, "method": method, "params": params}).encode("utf-8"))
        while True:
            try:
                msg = json.loads(self._frame())
            except socket.timeout:
                raise ChromeError("no answer to %s in %d s: %s" % (method, self.timeout, json.dumps(params)[:200])) from None
            if msg.get("id") == mine:
                if "error" in msg:
                    raise ChromeError("%s: %s" % (method, msg["error"].get("message")))
                return msg.get("result", {})
            if "method" in msg:
                self.events.append(msg)
                del self.events[:-400]

    # -- the page --------------------------------------------------------------------------------------------------
    def size(self, width, height, scale=1, touch=False):
        """The size of the window (CSS pixels), its pixel ratio, and whether it is a touch screen (a phone)."""
        self.width, self.height = width, height
        self.send("Emulation.setDeviceMetricsOverride", width=width, height=height, deviceScaleFactor=scale, mobile=bool(touch))
        self.send("Emulation.setTouchEmulationEnabled", enabled=bool(touch), maxTouchPoints=5 if touch else 1)

    def goto(self, url):
        self.send("Page.navigate", url=url)
        self.wait("document.readyState === 'complete'")
        self.send("Page.bringToFront")

    def visible(self):
        """True when the page draws (it is not hidden, and a frame comes within two seconds)."""
        return self.js("document.visibilityState === 'visible' && Promise.race([new Promise(r => requestAnimationFrame(() => r(true))), "
                       "new Promise(r => setTimeout(() => r(false), 2000))])")

    def js(self, expr):
        """The value of a JavaScript expression (a promise is awaited). An exception of the page is a ChromeError."""
        r = self.send("Runtime.evaluate", expression=expr, returnByValue=True, awaitPromise=True, userGesture=True)
        if "exceptionDetails" in r:
            d = r["exceptionDetails"]
            raise ChromeError("page: %s" % ((d.get("exception") or {}).get("description") or d.get("text")))
        return r["result"].get("value")

    def wait(self, expr, timeout=None, every=0.05):
        """Wait until the expression is true; its value. ChromeError with the expression when it never is."""
        end = time.monotonic() + (timeout or self.timeout)
        while True:
            got = self.js(expr)
            if got:
                return got
            if time.monotonic() > end:
                raise ChromeError("never true: %s%s" % (expr, "".join("\n  page error: " + e[:300] for e in self.errors()[-3:])))
            time.sleep(every)

    def errors(self):
        """The uncaught exceptions and console errors of the page so far."""
        out = []
        for e in self.events:
            if e["method"] == "Runtime.exceptionThrown":
                d = e["params"]["exceptionDetails"]
                out.append((d.get("exception") or {}).get("description") or d.get("text") or "")
            elif e["method"] == "Runtime.consoleAPICalled" and e["params"]["type"] == "error":
                out.append(" ".join(str(a.get("value", a.get("description", ""))) for a in e["params"]["args"]))
        return out

    def box(self, selector):
        """{x, y, w, h, cx, cy} of the first element that matches, in CSS pixels of the window; None when there is none
        or it is not drawn."""
        return self.js("""(() => { const e = document.querySelector(%s); if (!e) return null; const r = e.getBoundingClientRect();
            if (!r.width && !r.height) return null;
            return {x: r.left, y: r.top, w: r.width, h: r.height, cx: r.left + r.width / 2, cy: r.top + r.height / 2}; })()""" % json.dumps(selector))

    # -- input: a mouse, a finger, keys ------------------------------------------------------------------------------
    def mouse(self, kind, x, y, button="none", buttons=0, clicks=0, mods=0, **more):
        self.send("Input.dispatchMouseEvent", type=kind, x=x, y=y, button=button, buttons=buttons, clickCount=clicks,
                  modifiers=mods, **more)

    def move(self, x, y):
        self.mouse("mouseMoved", x, y)

    def click(self, x, y, button="left", mods=0, clicks=1):
        held = {"left": 1, "right": 2, "middle": 4}[button]
        self.mouse("mouseMoved", x, y)
        self.mouse("mousePressed", x, y, button, held, clicks, mods)
        self.mouse("mouseReleased", x, y, button, 0, clicks, mods)

    def click_on(self, selector, **kw):
        b = self.box(selector)
        if not b:
            raise ChromeError("nothing to click: %s" % selector)
        self.click(b["cx"], b["cy"], **kw)

    def drag(self, x0, y0, x1, y1, steps=12, pause=0.01, release=True):
        """Press at one place, move to another in steps, and let go (release False: keep the button down)."""
        self.mouse("mouseMoved", x0, y0)
        self.mouse("mousePressed", x0, y0, "left", 1, 1)
        for i in range(1, steps + 1):
            self.mouse("mouseMoved", x0 + (x1 - x0) * i / steps, y0 + (y1 - y0) * i / steps, "left", 1)
            time.sleep(pause)
        if release:
            self.mouse("mouseReleased", x1, y1, "left", 0, 1)

    def wheel(self, x, y, dy, dx=0, mods=0):
        self.mouse("mouseWheel", x, y, deltaX=dx, deltaY=dy, mods=mods)

    def touch(self, kind, points):
        self.send("Input.dispatchTouchEvent", type=kind, touchPoints=[{"x": x, "y": y, "id": i} for i, (x, y) in enumerate(points)])

    def tap(self, x, y):
        self.touch("touchStart", [(x, y)])
        self.touch("touchEnd", [])

    def swipe(self, x0, y0, x1, y1, steps=12, pause=0.01, release=True):
        """A finger down at one place, across to another, and up."""
        self.touch("touchStart", [(x0, y0)])
        for i in range(1, steps + 1):
            self.touch("touchMove", [(x0 + (x1 - x0) * i / steps, y0 + (y1 - y0) * i / steps)])
            time.sleep(pause)
        if release:
            self.touch("touchEnd", [])

    def key(self, key, mods=0):
        """One key, down and up. A printable key types its character."""
        code, vk, text = KEYS.get(key) or ("Key" + key.upper() if key.isalpha() else "", ord(key.upper()) if len(key) == 1 else 0, key if len(key) == 1 else "")
        base = dict(key=key, code=code, windowsVirtualKeyCode=vk, nativeVirtualKeyCode=vk, modifiers=mods)
        if text and not mods & (CTRL | ALT | META):
            self.send("Input.dispatchKeyEvent", type="keyDown", text=text, unmodifiedText=text, **base)
        else:
            self.send("Input.dispatchKeyEvent", type="rawKeyDown", **base)
        self.send("Input.dispatchKeyEvent", type="keyUp", **base)

    def type(self, text):
        self.send("Input.insertText", text=text)

    def screenshot(self, path=None):
        """A PNG of the window: its bytes, written to path when one is given."""
        png = base64.b64decode(self.send("Page.captureScreenshot", format="png")["data"])
        if path:
            with open(path, "wb") as f:
                f.write(png)
        return png

    def close(self):
        if self.sock is not None:
            try:
                self.sock.close()
            except OSError:
                pass
            self.sock = None
        if self.proc is not None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(timeout=5)
            self.proc = None
        shutil.rmtree(self.profile, ignore_errors=True)


def png_size(png):
    """(width, height) of a PNG."""
    return struct.unpack(">II", png[16:24])


def png_colors(png, limit=4096):
    """How many different pixel values a PNG has, counted on its raw scanlines up to `limit` (2 or more: it is not blank).
    Read with zlib only: the filters of the lines are undone for 8-bit RGB and RGBA."""
    import zlib
    w, h = png_size(png)
    depth, kind = png[24], png[25]
    bpp = {2: 3, 6: 4, 0: 1, 4: 2}.get(kind)
    if depth != 8 or bpp is None:
        return limit
    data, at = b"", 8
    while at < len(png):
        n, tag = struct.unpack(">I", png[at:at + 4])[0], png[at + 4:at + 8]
        if tag == b"IDAT":
            data += png[at + 8:at + 8 + n]
        at += 12 + n
    raw, stride, seen, prev = zlib.decompress(data), w * bpp, set(), bytearray(w * bpp)
    for y in range(h):
        f, line = raw[y * (stride + 1)], bytearray(raw[y * (stride + 1) + 1:(y + 1) * (stride + 1)])
        for i in range(stride):
            a = line[i - bpp] if i >= bpp else 0
            b, c = prev[i], prev[i - bpp] if i >= bpp else 0
            if f == 1:
                line[i] = (line[i] + a) & 255
            elif f == 2:
                line[i] = (line[i] + b) & 255
            elif f == 3:
                line[i] = (line[i] + (a + b) // 2) & 255
            elif f == 4:
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                line[i] = (line[i] + (a if pa <= pb and pa <= pc else b if pb <= pc else c)) & 255
        prev = line
        if y % 4 == 0:  # every fourth line is enough to tell a picture from a blank
            for x in range(0, stride, bpp * 4):
                seen.add(bytes(line[x:x + 3]))
            if len(seen) >= limit:
                return limit
    return len(seen)
