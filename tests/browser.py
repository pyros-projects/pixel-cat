"""A browser for the page tests: pointer and key input that arrives where a hand's would.

A click sent from a script goes straight to its button. A real press goes to whatever lies under the pointer,
and that is the difference these tests are for. So the input is sent through the browser's own debugging
connection, the way its developer tools do it.

Nothing is installed for this. The tests take the program named in PIXEL_CAT_BROWSER, or Chrome, Edge or Brave
where one is found, start it without a window on a profile of its own, and are skipped when none of them
answers.
"""

import base64
import json
import os
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.request
from pathlib import Path

PROGRAMS = ("google-chrome", "google-chrome-stable", "chrome", "microsoft-edge", "microsoft-edge-stable", "brave-browser", "brave")
PLACES = ("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
          "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
          "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
          r"C:\Program Files\Google\Chrome\Application\chrome.exe",
          r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe")
KEYS = {"Enter": ("Enter", "\r", 13), "Escape": ("Escape", "", 27)}


def program():
    """The browser to start, or None."""
    named = os.environ.get("PIXEL_CAT_BROWSER")
    if named:
        return shutil.which(named) or (named if Path(named).is_file() else None)
    for name in PROGRAMS:
        if shutil.which(name):
            return shutil.which(name)
    return next((place for place in PLACES if Path(place).is_file()), None)


class Line:
    """One WebSocket connection, as much of it as the debugging connection needs: text messages both ways."""

    def __init__(self, url):
        address, _, path = url.removeprefix("ws://").partition("/")
        host, _, port = address.partition(":")
        self.sock = socket.create_connection((host, int(port)), timeout=30)
        key = base64.b64encode(os.urandom(16)).decode()
        self.sock.sendall((f"GET /{path} HTTP/1.1\r\nHost: {address}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                           f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n").encode())
        self.held = b""
        while b"\r\n\r\n" not in self.held:
            self.held += self._more()
        head, _, self.held = self.held.partition(b"\r\n\r\n")
        if b" 101 " not in head.split(b"\r\n")[0]:
            raise ConnectionError("The browser did not open its debugging connection")

    def _more(self):
        data = self.sock.recv(65536)
        if not data:
            raise ConnectionError("The browser closed the connection")
        return data

    def _take(self, count):
        while len(self.held) < count:
            self.held += self._more()
        data, self.held = self.held[:count], self.held[count:]
        return data

    def _frame(self, kind, data):
        mask = os.urandom(4)
        size = len(data)
        head = bytes([0x80 | kind]) + (bytes([0x80 | size]) if size < 126 else bytes([0x80 | 126]) + struct.pack(">H", size) if size < 65536
                                       else bytes([0x80 | 127]) + struct.pack(">Q", size))
        self.sock.sendall(head + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(data)))

    def send(self, text):
        self._frame(1, text.encode())

    def receive(self):
        message = b""
        while True:
            first, second = self._take(2)
            size = second & 0x7f
            if size == 126:
                size = struct.unpack(">H", self._take(2))[0]
            elif size == 127:
                size = struct.unpack(">Q", self._take(8))[0]
            mask = self._take(4) if second & 0x80 else None
            data = self._take(size)
            if mask:
                data = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
            kind = first & 0x0f
            if kind == 8:
                raise ConnectionError("The browser closed the connection")
            if kind == 9:                                             # a ping wants its pong
                self._frame(10, data)
                continue
            if kind == 10:
                continue
            message += data
            if first & 0x80:
                return message.decode()

    def close(self):
        self.sock.close()


class Browser:
    """A browser without a window, with one page, driven like a person would drive it."""

    def __init__(self, width=1400, height=900):
        path = program()
        if not path:
            raise unittest.SkipTest("No browser to press the page's controls with. Name one in PIXEL_CAT_BROWSER (Chrome, Edge, Brave or a relative).")
        self.profile = tempfile.mkdtemp(prefix="pixel-cat-page-")
        flags = ["--headless=new", "--remote-debugging-port=0", f"--user-data-dir={self.profile}", "--no-first-run", "--no-default-browser-check",
                 "--disable-extensions", f"--window-size={width},{height}"]
        if hasattr(os, "geteuid") and os.geteuid() == 0:
            flags.append("--no-sandbox")
        self.process = subprocess.Popen([path, *flags, "about:blank"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.line, self.count = None, 0
        try:
            door = Path(self.profile) / "DevToolsActivePort"           # the browser writes its port here once it listens
            for _ in range(300):
                if door.is_file() and door.read_text().strip() or self.process.poll() is not None:
                    break
                time.sleep(0.05)
            if not door.is_file() or self.process.poll() is not None:
                raise OSError("it did not start")
            port = int(door.read_text().split()[0])
            for _ in range(100):
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/list", timeout=5) as answer:
                    pages = [target for target in json.loads(answer.read()) if target.get("type") == "page"]
                if pages:
                    break
                time.sleep(0.05)
            self.line = Line(pages[0]["webSocketDebuggerUrl"])
        except (OSError, ValueError, IndexError, KeyError) as error:
            self.close()
            raise unittest.SkipTest(f"{path} could not be driven ({error}). The page tests need Chrome, Edge, Brave or a relative.") from None

    def ask(self, method, **settings):
        self.count += 1
        self.line.send(json.dumps({"id": self.count, "method": method, "params": settings}))
        while True:
            answer = json.loads(self.line.receive())
            if answer.get("id") == self.count:
                if "error" in answer:
                    raise RuntimeError(f"{method}: {answer['error'].get('message')}")
                return answer.get("result", {})

    def open(self, url):
        self.ask("Page.navigate", url=url)

    def run(self, script):
        """Run JavaScript in the page and return what it gives back. A promise is waited for."""
        result = self.ask("Runtime.evaluate", expression=script, returnByValue=True, awaitPromise=True)
        if "exceptionDetails" in result:
            raise RuntimeError(result["exceptionDetails"].get("exception", {}).get("description") or result["exceptionDetails"].get("text"))
        return result["result"].get("value")

    def until(self, script, seconds=8):
        """Wait until the script gives back something true, and return it."""
        end = time.time() + seconds
        while True:
            value = self.run(script)
            if value:
                return value
            if time.time() > end:
                raise AssertionError(f"Waited {seconds} s in vain for: {script}")
            time.sleep(0.05)

    def _mouse(self, kind, x, y, down=False):
        self.ask("Input.dispatchMouseEvent", type=kind, x=x, y=y, button="left" if kind != "mouseMoved" else "none", buttons=1 if down else 0,
                 clickCount=0 if kind == "mouseMoved" else 1, pointerType="mouse")

    def press(self, x, y):
        """Move the pointer there, button down, button up."""
        self._mouse("mouseMoved", x, y)
        self._mouse("mousePressed", x, y, True)
        self._mouse("mouseReleased", x, y)

    def drag(self, start, end, steps=6):
        self._mouse("mouseMoved", *start)
        self._mouse("mousePressed", *start, True)
        for step in range(1, steps + 1):
            self.ask("Input.dispatchMouseEvent", type="mouseMoved", x=start[0] + (end[0] - start[0]) * step / steps,
                     y=start[1] + (end[1] - start[1]) * step / steps, button="left", buttons=1, pointerType="mouse")
        self._mouse("mouseReleased", *end)

    def wheel(self, x, y, by):
        self._mouse("mouseMoved", x, y)
        self.ask("Input.dispatchMouseEvent", type="mouseWheel", x=x, y=y, deltaX=0, deltaY=by, pointerType="mouse")

    def key(self, name):
        """One key, down and up: a letter, or Enter or Escape."""
        key, text, number = KEYS[name] if name in KEYS else (name, name, ord(name.upper()))
        code = name if name in KEYS else "Key" + name.upper()
        self.ask("Input.dispatchKeyEvent", type="keyDown", key=key, code=code, text=text, windowsVirtualKeyCode=number)
        self.ask("Input.dispatchKeyEvent", type="keyUp", key=key, code=code, windowsVirtualKeyCode=number)

    def close(self):
        if self.line:
            self.line.close()
        self.process.terminate()
        try:
            self.process.wait(5)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait(5)
        shutil.rmtree(self.profile, ignore_errors=True)


if __name__ == "__main__":
    print(program() or "No browser found. Name one in PIXEL_CAT_BROWSER.", file=sys.stderr if not program() else sys.stdout)
