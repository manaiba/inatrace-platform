"""The browser parts of the video: Playwright drives the site and records it (its own video
recording); a drawn pointer stands in for the mouse, which a recording leaves out."""

import socket
import subprocess
import time
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

# Where the page sits in the frame: the terminal's window, under a 44 px bar (make.py: WINDOW).
VIEWPORT = {"width": 1880, "height": 886}

POINTER = """
addEventListener("DOMContentLoaded", () => {
  const pointer = document.createElement("div");
  pointer.innerHTML = `<svg width="28" height="28" viewBox="0 0 24 24"><path d="M3 2 L3 19 L7.5 14.8 L10.6 21.6
    L13.6 20.3 L10.6 13.6 L16.8 13.6 Z" fill="#fff" stroke="#111" stroke-width="1.4" stroke-linejoin="round"/></svg>`;
  pointer.style.cssText = "position:fixed;left:0;top:0;z-index:2147483647;pointer-events:none;"
    + "filter:drop-shadow(0 2px 3px rgba(0,0,0,.35));transform:translate(1100px,640px)";
  document.body.appendChild(pointer);
  addEventListener("mousemove", e => pointer.style.transform = `translate(${e.clientX - 3}px,${e.clientY - 2}px)`, true);
  addEventListener("mousedown", e => {
    const ring = document.createElement("div");
    ring.style.cssText = `position:fixed;left:${e.clientX - 26}px;top:${e.clientY - 26}px;width:52px;height:52px;`
      + "border-radius:50%;border:3px solid rgba(122,162,247,.9);z-index:2147483646;pointer-events:none;"
      + "transition:transform .45s ease-out,opacity .45s ease-out;transform:scale(.4)";
    document.body.appendChild(ring);
    requestAnimationFrame(() => { ring.style.transform = "scale(1.3)"; ring.style.opacity = "0"; });
    setTimeout(() => ring.remove(), 600);
  }, true);
});
"""


class Driver:
    """Moves like a person: glides to things, then clicks or types."""

    def __init__(self, page: Page):
        self.page = page
        self.at = (1100, 640)

    def move(self, locator) -> None:
        box = locator.bounding_box()
        target = (box["x"] + box["width"] / 2, box["y"] + box["height"] * 0.55)
        self.page.mouse.move(*target, steps=24)
        self.at = target

    def click(self, locator) -> None:
        self.move(locator)
        self.page.wait_for_timeout(150)
        locator.click()

    def fill(self, locator, text: str) -> None:
        self.click(locator)
        self.page.wait_for_timeout(200)
        locator.press_sequentially(text, delay=80)


def login(page: Page, d: Driver, site: str, user: str, password: str) -> None:
    page.goto(f"{site}/en/login")
    page.get_by_placeholder("Enter your username").wait_for(timeout=60_000)
    page.wait_for_load_state("networkidle")
    yield "ready"
    page.wait_for_timeout(2000)
    cookies = page.get_by_role("button", name="OK")
    if cookies.count():
        d.click(cookies)
    d.fill(page.get_by_placeholder("Enter your username"), user)
    d.fill(page.get_by_placeholder("Enter your password"), password)
    page.wait_for_timeout(300)
    d.click(page.get_by_role("button", name="Login"))
    page.wait_for_url(lambda url: "/login" not in url, timeout=30_000)
    page.wait_for_load_state("networkidle")
    page.wait_for_timeout(2500)
    later = page.get_by_text("Maybe later")
    if later.count():
        d.click(later)
    page.wait_for_timeout(5000)


def beszel(page: Page, d: Driver, cli: str, instance: str) -> None:
    """The monitoring, through `deploy dashboard` (run here, off camera, for as long as it takes)."""
    tunnel = subprocess.Popen([cli, "deploy", "dashboard", instance], stdin=subprocess.DEVNULL,
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        deadline = time.time() + 60
        while not _listening(8090):
            if time.time() > deadline or tunnel.poll() is not None:
                raise RuntimeError("deploy dashboard did not open port 8090")
            time.sleep(0.5)
        page.goto("http://localhost:8090")
        system = page.get_by_role("link", name="127.0.0.1").first
        system.wait_for(timeout=60_000)
        page.wait_for_load_state("networkidle")
        yield "ready"
        page.wait_for_timeout(4000)
        d.click(system)
        page.wait_for_load_state("networkidle")
        page.wait_for_timeout(4000)
        for _ in range(12):
            page.mouse.wheel(0, 90)
            page.wait_for_timeout(120)
        page.wait_for_timeout(3500)
        for _ in range(12):
            page.mouse.wheel(0, 90)
            page.wait_for_timeout(120)
        page.wait_for_timeout(4000)
    finally:
        tunnel.terminate()
        tunnel.wait(timeout=10)


def _listening(port: int) -> bool:
    with socket.socket() as probe:
        return probe.connect_ex(("127.0.0.1", port)) == 0


FLOWS = {"login": login, "beszel": beszel}


def record(flow: str, out: Path, **args) -> float:
    """Records the flow into out (webm); returns where it gets interesting (s): before the
    flow yields "ready", the page is still loading."""
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        context = browser.new_context(viewport=VIEWPORT, ignore_https_errors=True,
                                      record_video_dir=str(out.parent / "video-tmp"), record_video_size=VIEWPORT)
        context.add_init_script(POINTER)
        start = time.time()
        page = context.new_page()
        ready = 0.0
        for _ in FLOWS[flow](page, Driver(page), **args):
            ready = time.time() - start
        video = page.video
        context.close()
        browser.close()
        Path(video.path()).replace(out)
    return ready
