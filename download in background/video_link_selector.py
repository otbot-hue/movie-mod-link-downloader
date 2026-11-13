import os
import re
import sys
import subprocess
import glob
import time
import threading
import tkinter as tk
from tkinter import ttk, messagebox
from pathlib import Path
from urllib.parse import urljoin, urlparse

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options


# ============================================================
# SETTINGS
# ============================================================

QUALITY_PATTERN = re.compile(
    r'\b(144p|240p|360p|480p|576p|720p|1080p|1440p|2160p|4k)\b',
    re.IGNORECASE
)

SIZE_PATTERN = re.compile(
    r'\b(\d+(?:\.\d+)?\s*(?:MB|GB|TB))\b',
    re.IGNORECASE
)

CODEC_PATTERN = re.compile(
    r'\b(x264|x265|hevc|h\.?264|h\.?265|10\s?bit|8\s?bit)\b',
    re.IGNORECASE
)

LANGUAGE_WORDS = [
    "hindi", "english", "arabic", "arabian", "japanese", "korean",
    "tamil", "telugu", "marathi", "spanish", "french", "german",
    "dual audio", "multi audio", "multi-language", "dubbed"
]

COMBINED_LANGUAGES = [
    "hindi-english", "english-hindi",
    "dual audio", "multi audio", "multi-language"
]


# Server buttons are ranked by this function (higher = better).
# G-Drive "Fast Server" buttons always win over other server types.
PREFERRED_SERVER = "G-Drive Fast Server"

# How long (seconds) to wait for that button to appear
SERVER_WAIT_SECONDS = 25

# After clicking the server, the script keeps clicking "continue"-type
# buttons on the intermediate (ad / timer) pages until it lands on one
# of these hosts, or until GATE_WAIT_SECONDS runs out.
FINAL_HOSTS = (
    "drive.google.com",
    "docs.google.com",
    "drive.usercontent.google.com",
)

GATE_WAIT_SECONDS = 180

# Downloaded files are saved here
DOWNLOAD_DIR = str(Path.home() / "Downloads")

# Download-page buttons, best first. "Resume Cloud" is used only when
# "Instant Download" is not offered. "Telegram File" and
# "Login to Download" are never clicked.
DOWNLOAD_BUTTON_TEXTS = [
    "instant download",
    "resume cloud",
    "resumecloud",
]

# Button texts to click on intermediate pages, best match first
CONTINUE_TEXTS = [
    # last step first: if "Go to download" is on the page it must win
    # over a leftover "Generate link" / "Click here to continue"
    "go to download",
    "go to link",
    "get download link",
    "get link",
    "generate link",
    "click here to continue",
    "click to continue",
    "start verification",
    "continue to link",
    "verify",
    "continue",
]

# A new tab is kept as "the next step" if its text contains one of
# these. Any other new tab on a different website is closed as an ad.
GATE_KEYWORDS = [
    "verify that you are human",
    "verify you are human",
    "start verification",
    "generate link",
    "get link",
    "click here to continue",
    "please wait",
    "scroll down",
    "your link is ready",
    "instant download",
    "resume cloud",
    "file is ready",
    "file already generated",
]


# ============================================================
# JAVASCRIPT: find the description line belonging to a button
# ============================================================
# Walks up from the button. At each level it checks:
#   1. Does this element itself contain exactly ONE quality
#      mention (i.e. it is a single section)? -> use its last
#      quality line.
#   2. Otherwise, does a previous sibling contain a quality
#      line? -> use the nearest one.
# This way each button pair gets its OWN heading line instead
# of the first quality found in a big wrapper.

FIND_LINE_JS = r"""
const el = arguments[0];
const qTest = /\b(144p|240p|360p|480p|576p|720p|1080p|1440p|2160p|4k)\b/i;
const qAll  = /\b(144p|240p|360p|480p|576p|720p|1080p|1440p|2160p|4k)\b/gi;

function lastQualityLine(text) {
    const lines = text.split('\n').map(s => s.trim()).filter(Boolean);
    for (let i = lines.length - 1; i >= 0; i--) {
        if (qTest.test(lines[i])) return lines[i];
    }
    return '';
}

let node = el;
for (let depth = 0; depth < 8 && node; depth++) {

    // Case 1: heading is inside the same element as the buttons
    const own = (node.innerText || '').trim();
    const count = (own.match(qAll) || []).length;
    if (count === 1 && own.length < 250) {
        return lastQualityLine(own);
    }

    // Case 2: heading is a previous sibling (nearest first)
    let sib = node.previousElementSibling;
    while (sib) {
        const t = (sib.innerText || '').trim();
        const c = (t.match(qAll) || []).length;
        if (c >= 1 && t.length < 250) {
            return lastQualityLine(t);
        }
        sib = sib.previousElementSibling;
    }

    node = node.parentElement;
}
return '';
"""


# ============================================================
# LOGGING  (console + run_log.txt next to the script)
# ============================================================

LOG_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "run_log.txt"
)


def log(*args):
    msg = " ".join(str(a) for a in args)

    try:
        print(msg)
    except Exception:
        pass

    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(f"[{time.strftime('%H:%M:%S')}] {msg}\n")
    except Exception:
        pass


# Some download pages call showSaveFilePicker(), which opens a native
# "Save As" window that Selenium cannot control. Removing it makes the
# page fall back to a normal download into the Downloads folder.
NO_SAVE_PICKER_JS = (
    "try { delete window.showSaveFilePicker; "
    "Object.defineProperty(window, 'showSaveFilePicker', "
    "{value: undefined, configurable: true}); } catch (e) {}"
)

_prepared_tabs = set()


def prepare_tab(driver):
    """
    Make the current tab look focused/visible (pages pause timers in
    background tabs) and disable the native Save As picker.
    """
    try:
        handle = driver.current_window_handle
    except Exception:
        return

    if handle not in _prepared_tabs:
        try:
            # Applies to every page this tab loads from now on
            driver.execute_cdp_cmd(
                "Page.addScriptToEvaluateOnNewDocument",
                {"source": NO_SAVE_PICKER_JS}
            )
            _prepared_tabs.add(handle)
        except Exception:
            pass

    try:
        # ...and to the page that is already loaded
        driver.execute_script(NO_SAVE_PICKER_JS)
    except Exception:
        pass

    for method, params in (
        ("Emulation.setFocusEmulationEnabled", {"enabled": True}),
        ("Page.bringToFront", {}),
    ):
        try:
            driver.execute_cdp_cmd(method, params)
        except Exception:
            pass


def accept_save_dialog():
    """
    Windows only: if Chrome still shows its native "Save As" window,
    press Save (it already points at the Downloads folder).
    Returns True if a dialog was found and accepted.
    """
    if not sys.platform.startswith("win"):
        return False

    try:
        import ctypes

        user32 = ctypes.windll.user32
        hwnd = user32.FindWindowW("#32770", "Save As")

        if not hwnd:
            return False

        button = user32.GetDlgItem(hwnd, 1)          # 1 = the Save button

        if button:
            user32.SendMessageW(button, 0x00F5, 0, 0)    # BM_CLICK
        else:
            user32.PostMessageW(hwnd, 0x0111, 1, 0)      # WM_COMMAND / IDOK

        return True

    except Exception:
        return False


# ============================================================
# CHROME
# ============================================================

def create_driver(headless=True):
    options = Options()

    if headless:
        # Run Chrome in the background - no window on screen
        options.add_argument("--headless=new")
        options.add_argument("--window-size=1920,1080")
        options.add_argument("--disable-gpu")
    else:
        options.add_argument("--incognito")
        options.add_argument("--start-maximized")

    options.add_argument("--disable-notifications")
    options.add_argument("--disable-popup-blocking")
    options.add_argument("--disable-logging")
    options.add_argument("--mute-audio")

    # Save downloads straight to the Downloads folder (no prompt)
    os.makedirs(DOWNLOAD_DIR, exist_ok=True)
    options.add_experimental_option("prefs", {
        "download.default_directory": DOWNLOAD_DIR,
        "download.prompt_for_download": False,
        "download.directory_upgrade": True,
        "savefile.default_directory": DOWNLOAD_DIR,
        "profile.default_content_setting_values.automatic_downloads": 1,
    })
    driver = webdriver.Chrome(options=options)

    # Make sure downloads are allowed and go to the Downloads folder
    try:
        driver.execute_cdp_cmd("Browser.setDownloadBehavior", {
            "behavior": "allow",
            "downloadPath": DOWNLOAD_DIR,
        })
    except Exception:
        pass

    return driver


def driver_is_alive(driver):
    """True if the Chrome session is still usable."""
    if driver is None:
        return False
    try:
        return len(driver.window_handles) > 0
    except Exception:
        return False


def get_live_driver(old_driver, headless=True):
    """
    Returns a working driver. If Chrome was closed or crashed
    ("invalid session id"), a fresh Incognito Chrome is started.
    """
    if driver_is_alive(old_driver):
        return old_driver

    try:
        if old_driver is not None:
            old_driver.quit()
    except Exception:
        pass

    return create_driver(headless)


# ============================================================
# HELPERS
# ============================================================

def clean_url(url):
    url = url.strip()
    if not url:
        return ""
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    return url


def clean_text(text):
    if not text:
        return ""
    return re.sub(r'\s+', ' ', text).strip()


def find_quality(text):
    match = QUALITY_PATTERN.search(text)
    return match.group(1) if match else ""


def find_size(text):
    match = SIZE_PATTERN.search(text)
    return match.group(1).replace(" ", "") if match else ""


def find_codec(text):
    found = []
    for m in CODEC_PATTERN.findall(text):
        m = re.sub(r'\s+', '', m)
        if m.lower() not in [f.lower() for f in found]:
            found.append(m)
    return " ".join(found)


def find_language(text):
    text_lower = text.lower()

    for language in COMBINED_LANGUAGES:
        if language in text_lower:
            return language.title()

    found = []
    for language in LANGUAGE_WORDS:
        if language in text_lower and language not in found:
            found.append(language)

    return ", ".join(x.title() for x in found)


def clean_title(text):
    text = clean_text(text)

    text = re.sub(r'^\s*Download\s+', '', text, flags=re.IGNORECASE)
    text = re.sub(r'\bSeason\s*\d+\b', '', text, flags=re.IGNORECASE)
    text = re.sub(r'^\s*\d+\.\s*', '', text)
    text = re.sub(QUALITY_PATTERN, '', text)
    text = re.sub(
        r'\bx264\b|\bx265\b|\b10bit\b|\b8bit\b|\bMsubs\b',
        '', text, flags=re.IGNORECASE
    )
    text = re.sub(SIZE_PATTERN, '', text)
    text = re.sub(r'\[[^\]]*\]', '', text)
    text = re.sub(r'\{[^}]*\}', '', text)
    text = text.replace("{", "").replace("}", "")
    text = re.sub(r'\s+', ' ', text)

    return text.strip(" -|:")


def find_page_title(driver):
    """Use the page's main <h1>, falling back to <title>."""
    try:
        for h in driver.find_elements(By.CSS_SELECTOR, "h1"):
            t = clean_title(h.text)
            if t:
                return t
    except Exception:
        pass

    return clean_title(driver.title) or "Unknown Title"


# ============================================================
# EXTRACT LINK
# ============================================================

def extract_link(element, base_url):

    # 1. href on the element
    try:
        href = element.get_attribute("href")
        if href and not href.startswith("javascript:"):
            return urljoin(base_url, href)
    except Exception:
        pass

    # 2. button inside <a>
    try:
        parent = element.find_element(By.XPATH, "./ancestor::a[1]")
        href = parent.get_attribute("href")
        if href and not href.startswith("javascript:"):
            return urljoin(base_url, href)
    except Exception:
        pass

    # 3. onclick
    try:
        onclick = element.get_attribute("onclick")
        if onclick:
            match = re.search(
                r"""['"]((?:https?:)?//[^'"]+)['"]""",
                onclick
            )
            if match:
                return urljoin(base_url, match.group(1))
    except Exception:
        pass

    return ""


# ============================================================
# OPEN LINK IN NEW TAB AND AUTO-CLICK SERVER BUTTON
# ============================================================

def server_score(text):
    """
    Rank a server button by its text. Higher is better, 0 = ignore.

      100  Fast Server 1 (G-Drive)
       90  Fast Server (G-Drive)  /  Fast Server 2 (GDrive)
       50  any other "Fast Server"
       30  Google Drive / G-Drive (non-fast)
    """
    t = re.sub(r'\s+', ' ', text.lower()).strip()

    if not t or len(t) > 60:
        return 0

    has_drive = any(x in t for x in ("g-drive", "gdrive", "google drive"))
    has_fast = "fast server" in t

    if has_fast and has_drive:
        return 100 if re.search(r'fast server\s*1\b', t) else 90

    if has_fast:
        return 50

    if has_drive:
        return 30

    return 0


def find_best_server_button(driver):
    """Return (element, score) of the best visible server button."""
    best_el, best_score = None, 0

    candidates = driver.find_elements(
        By.XPATH,
        "//a | //button | //*[@onclick] | //input[@type='button' or @type='submit']"
    )

    for el in candidates:
        try:
            if not el.is_displayed():
                continue

            label = el.text or el.get_attribute("value") or ""
            score = server_score(label)

            if score > best_score:
                best_el, best_score = el, score

        except Exception:
            pass

    return best_el, best_score


def host_of(url):
    try:
        return (urlparse(url).hostname or "").lower()
    except Exception:
        return ""


def is_final_url(url):
    host = host_of(url)
    return any(host == h or host.endswith("." + h) for h in FINAL_HOSTS)


def wait_loaded(driver, timeout=8):
    """Wait until the current tab finished loading."""
    end_time = time.time() + timeout
    while time.time() < end_time:
        try:
            if driver.execute_script("return document.readyState") == "complete":
                return
        except Exception:
            pass
        time.sleep(0.3)


def looks_like_gate_page(driver):
    """True if the current tab looks like a step of the link chain
    (timer / verify / generate-link page) rather than a random ad."""
    try:
        body = driver.find_element(By.TAG_NAME, "body").text.lower()
    except Exception:
        return False
    return any(k in body for k in GATE_KEYWORDS)


# ------------------------------------------------------------
# AD HANDLING
# ------------------------------------------------------------

AD_CLOSE_XPATH = (
    "//*[@id='dismiss-button' or @aria-label='Close' "
    "or @aria-label='Close ad' or normalize-space(text())='Close']"
)

AD_CLEANUP_JS = r"""
let removed = 0;

// Known ad containers (Google AdSense / Ad Manager, vignette, anchor)
const selectors = [
    'ins.adsbygoogle',
    'ins[data-vignette-loaded]',
    'ins[data-ad-status]',
    'iframe[id^="google_ads_iframe"]',
    'iframe[id^="aswift"]',
    'iframe[src*="googlesyndication"]',
    'iframe[src*="doubleclick"]',
    'iframe[title="Advertisement"]',
    'div[id^="google_ads"]',
    'div[id^="div-gpt-ad"]',
    'div[data-anchor-status]',
    'div[aria-label="Advertisement"]',

    // Outstream / floating video ad players
    '[id*="outstream" i]', '[class*="outstream" i]',
    '[id*="video-ad" i]',  '[class*="video-ad" i]',
    '[id*="ad-video" i]',  '[class*="ad-video" i]',
    '[class*="floating-video" i]', '[id*="floating-video" i]',
    '[id*="primis" i]',    '[class*="primis" i]',
    '[id*="connatix" i]',  '[class*="connatix" i]',
    '[id*="aniview" i]',   '[class*="aniview" i]',
    '[id*="vidazoo" i]',   '[class*="vidazoo" i]',
    '[id*="teads" i]',     '[class*="teads" i]',
    '[class*="jw-ad" i]',  '[class*="vjs-ad" i]'
];
document.querySelectorAll(selectors.join(',')).forEach(e => {
    e.remove(); removed++;
});

// Full-screen overlays with a huge z-index that contain an iframe
document.querySelectorAll('body > *').forEach(e => {
    const cs = getComputedStyle(e);
    if (cs.position === 'fixed' &&
        parseInt(cs.zIndex || '0', 10) > 1000000 &&
        (e.querySelector('iframe') || e.tagName === 'INS')) {
        e.remove(); removed++;
    }
});

// Stop any video that is still playing (leftover video ads)
document.querySelectorAll('video').forEach(v => {
    try { v.pause(); v.muted = true; } catch (e) {}
});

// Undo scroll locks left behind by ad overlays
document.documentElement.style.overflow = 'auto';
if (document.body) document.body.style.overflow = 'auto';

// Strip #goog_rewarded / #google_vignette from the URL
if (/goog|google_vignette/i.test(location.hash)) {
    history.replaceState(null, '', location.pathname + location.search);
}
return removed;
"""


def _click_close_buttons(driver):
    for el in driver.find_elements(By.XPATH, AD_CLOSE_XPATH):
        try:
            if el.is_displayed():
                driver.execute_script("arguments[0].click();", el)
                time.sleep(0.3)
        except Exception:
            pass


def dismiss_ads(driver):
    """
    1. Click "Close" / dismiss buttons offered by ad overlays
       (in the page and inside ad iframes).
    2. Remove leftover ad containers and scroll locks.
    The ads themselves are never clicked.
    """

    try:
        _click_close_buttons(driver)
    except Exception:
        pass

    try:
        frames = driver.find_elements(By.CSS_SELECTOR, "iframe")[:10]
        for frame in frames:
            try:
                if not frame.is_displayed():
                    continue
                driver.switch_to.frame(frame)
                _click_close_buttons(driver)
            except Exception:
                pass
            finally:
                try:
                    driver.switch_to.default_content()
                except Exception:
                    pass
    except Exception:
        pass

    try:
        driver.execute_script(AD_CLEANUP_JS)
    except Exception:
        pass


# ------------------------------------------------------------
# CONTINUE BUTTONS
# ------------------------------------------------------------

def find_continue_button(driver, skip=()):
    """Return the best visible, enabled 'continue'-type button, or None."""
    best_el, best_rank = None, None

    candidates = driver.find_elements(
        By.XPATH,
        "//a | //button | //*[@onclick] | "
        "//input[@type='button' or @type='submit'] | "
        "//div[not(*)] | //span[not(*)]"
    )

    for el in candidates:
        try:
            if not el.is_displayed() or not el.is_enabled():
                continue

            label = (el.text or el.get_attribute("value") or "")
            t = re.sub(r'\s+', ' ', label.lower()).strip()

            if not t or len(t) > 40 or "continue reading" in t or t in skip:
                continue

            for rank, phrase in enumerate(CONTINUE_TEXTS):
                if phrase in t:
                    if best_rank is None or rank < best_rank:
                        best_el, best_rank = el, rank
                    break

        except Exception:
            pass

    return best_el


def click_element(driver, el):
    driver.execute_script(
        "arguments[0].scrollIntoView({block: 'center'});", el
    )
    time.sleep(0.4)
    try:
        el.click()
    except Exception:
        # Overlays can block a normal click - use a JS click
        driver.execute_script("arguments[0].click();", el)


def close_tab(driver, handle):
    """Close one tab, but never the last remaining one."""
    try:
        if handle in driver.window_handles and len(driver.window_handles) > 1:
            driver.switch_to.window(handle)
            driver.close()
    except Exception:
        pass


# ------------------------------------------------------------
# FOLLOW THE LINK CHAIN
# ------------------------------------------------------------

def snapshot_downloads():
    """Names of all files currently in the Downloads folder."""
    try:
        return set(os.listdir(DOWNLOAD_DIR))
    except Exception:
        return set()


def new_downloads(before):
    """Files (including in-progress .crdownload) that appeared since `before`."""
    return sorted(snapshot_downloads() - before)


def find_download_button(driver):
    """
    Best 'Instant Download' / 'Resume Cloud' button on the page.
    'Telegram File' and 'Login to Download' are never chosen.
    """
    best_el, best_rank = None, None

    candidates = driver.find_elements(
        By.XPATH,
        "//a | //button | //*[@onclick] | "
        "//input[@type='button' or @type='submit'] | "
        "//div[not(*)] | //span[not(*)]"
    )

    for el in candidates:
        try:
            if not el.is_displayed() or not el.is_enabled():
                continue

            label = (el.text or el.get_attribute("value") or "")
            t = re.sub(r'\s+', ' ', label.lower()).strip()

            if not t or len(t) > 40:
                continue

            if "telegram" in t or "login" in t:
                continue

            for rank, phrase in enumerate(DOWNLOAD_BUTTON_TEXTS):
                if phrase in t:
                    if best_rank is None or rank < best_rank:
                        best_el, best_rank = el, rank
                    break

        except Exception:
            pass

    return best_el


def button_label(el):
    try:
        label = el.text or el.get_attribute("value") or ""
        return re.sub(r'\s+', ' ', label.lower()).strip()
    except Exception:
        return ""


def list_visible_buttons(driver, limit=15):
    """Texts of visible clickable elements (used for debugging output)."""
    found = []
    try:
        for el in driver.find_elements(
            By.XPATH,
            "//a | //button | //*[@onclick] | //input[@type='button' or @type='submit']"
        ):
            try:
                if el.is_displayed():
                    t = button_label(el)
                    if t and len(t) <= 60 and t not in found:
                        found.append(t)
            except Exception:
                pass
    except Exception:
        pass
    return found[:limit]


def save_debug_screenshot(driver):
    """Save a screenshot next to the script so a stuck page can be inspected."""
    try:
        folder = os.path.dirname(os.path.abspath(__file__))
        path = os.path.join(folder, f"stuck_{int(time.time())}.png")
        driver.save_screenshot(path)
        return path
    except Exception:
        return None


def follow_gate_pages(driver, download_before, timeout=GATE_WAIT_SECONDS):
    """
    Clicks through the intermediate pages (verify / generate link /
    go to download) and then the download pages (Instant Download,
    or Resume Cloud if Instant Download is not offered).
    Old chain tabs and ad pop-ups are closed as it goes.

    Returns (status, info):
      ("download", [file names])  - a new file appeared in Downloads
      ("final", tab_handle)       - reached Google Drive
      (None, debug_screenshot)    - not finished in time
    """

    work = driver.current_window_handle
    end_time = time.time() + timeout

    click_counts = {}       # (url, label) -> times clicked
    idle_loops = 0          # loops in a row without finding a button

    while time.time() < end_time:

        # A native "Save As" window would block the download
        if accept_save_dialog():
            log("Save As window found - pressed Save")
            time.sleep(1)

        # Has a download started?
        files = new_downloads(download_before)
        if files:
            time.sleep(3)                       # let Chrome settle the name
            return "download", new_downloads(download_before)

        # Did any tab reach Google Drive?
        for h in list(driver.window_handles):
            try:
                driver.switch_to.window(h)
                if is_final_url(driver.current_url):
                    return "final", h
            except Exception:
                pass

        if work not in driver.window_handles:
            work = driver.window_handles[-1]

        driver.switch_to.window(work)

        try:
            prepare_tab(driver)
            dismiss_ads(driver)

            current_url = driver.current_url

            # Don't keep re-clicking a button that changes nothing
            skip = {
                label for (u, label), n in click_counts.items()
                if u == current_url and n >= 4
            }

            # Download buttons win over "continue" buttons
            button = find_download_button(driver)
            is_download_click = button is not None

            if button is None:
                button = find_continue_button(driver, skip)

            if button is None:
                idle_loops += 1

                # Scroll down step by step (like a person) and stay near
                # the bottom - some pages start their timer or reveal
                # "Go to download" only after scrolling.
                driver.execute_script(
                    "window.scrollBy(0, Math.max(500, window.innerHeight * 0.8));"
                )
                time.sleep(1)

                if idle_loops % 10 == 0:
                    log("Waiting. Visible buttons:",
                          list_visible_buttons(driver))
                    driver.execute_script("window.scrollTo(0, 0);")

                continue

            idle_loops = 0

            label = button_label(button)
            click_counts[(current_url, label)] = (
                click_counts.get((current_url, label), 0) + 1
            )
            log(f"Clicking: {label!r}  on  {current_url}")

            work_host = host_of(current_url)
            before = set(driver.window_handles)

            click_element(driver, button)
            time.sleep(2)

            # After an Instant Download / Resume Cloud click, give the
            # browser a few seconds to start the file download
            if is_download_click:
                for _ in range(10):
                    if accept_save_dialog():
                        log("Save As window found - pressed Save")
                    if new_downloads(download_before):
                        break
                    time.sleep(0.5)

                if new_downloads(download_before):
                    continue        # handled at the top of the loop

            adopted = None

            for h in [x for x in driver.window_handles if x not in before]:
                driver.switch_to.window(h)
                wait_loaded(driver)

                is_next_step = (
                    is_final_url(driver.current_url)
                    or host_of(driver.current_url) == work_host
                )

                # Content may load a moment after the page itself
                if not is_next_step:
                    for _ in range(12):
                        if looks_like_gate_page(driver):
                            is_next_step = True
                            break
                        time.sleep(0.5)

                if adopted is None and is_next_step:
                    adopted = h
                else:
                    log("Closed ad tab:", driver.current_url)
                    close_tab(driver, h)

            if adopted:
                # Moved to the next step: close the previous one
                close_tab(driver, work)
                work = adopted

            driver.switch_to.window(work)

        except Exception:
            # Page was changing while we looked - just retry
            time.sleep(1)

    # Timed out: write everything needed to see where it got stuck
    log("=== STUCK: timed out waiting for the download to start ===")

    for h in list(driver.window_handles):
        try:
            driver.switch_to.window(h)
            log("Open tab:", driver.current_url, "|", driver.title)
        except Exception:
            pass

    try:
        driver.switch_to.window(work)
        body = driver.find_element(By.TAG_NAME, "body").text
        log("Page text:", re.sub(r'\s+', ' ', body)[:700])
        log("Visible buttons:", list_visible_buttons(driver))
    except Exception:
        pass

    return None, save_debug_screenshot(driver)


def open_and_click_server(driver, link, timeout=SERVER_WAIT_SECONDS):
    """
    Opens `link` in a new tab, clicks the best G-Drive "Fast Server"
    button, follows every page until the file starts downloading into
    the Downloads folder, then closes all tabs it opened.

    Returns (clicked_label, status, info) - see follow_gate_pages.
    """

    handles_at_start = set(driver.window_handles)
    download_before = snapshot_downloads()

    driver.execute_script("window.open(arguments[0], '_blank');", link)

    server_tab = None
    for _ in range(20):
        new_tabs = [
            h for h in driver.window_handles if h not in handles_at_start
        ]
        if new_tabs:
            server_tab = new_tabs[-1]
            break
        time.sleep(0.25)

    if not server_tab:
        return "", None, None

    driver.switch_to.window(server_tab)
    prepare_tab(driver)

    end_time = time.time() + timeout

    while time.time() < end_time:

        try:
            el, score = find_best_server_button(driver)

            if el is None:
                dismiss_ads(driver)
                time.sleep(0.5)
                continue

            # If it's not the top-ranked type, give the page a moment
            # in case the better button is still loading.
            if score < 100:
                time.sleep(2)
                el, score = find_best_server_button(driver)

            label = re.sub(r'\s+', ' ', el.text or "").strip()

            handles_before = set(driver.window_handles)

            click_element(driver, el)
            time.sleep(2)

            # The server click may open the next page in a new tab
            new_tabs = [
                h for h in driver.window_handles if h not in handles_before
            ]

            if new_tabs:
                for extra in new_tabs[:-1]:
                    close_tab(driver, extra)          # ad pop-ups

                close_tab(driver, server_tab)         # done with this tab
                driver.switch_to.window(new_tabs[-1])

            status, info = follow_gate_pages(driver, download_before)

            if status:
                keep = info if status == "final" else None

                # Close every tab opened during this run
                for h in list(driver.window_handles):
                    if h not in handles_at_start and h != keep:
                        close_tab(driver, h)

                if keep and keep in driver.window_handles:
                    driver.switch_to.window(keep)
                else:
                    for h in driver.window_handles:
                        if h in handles_at_start:
                            driver.switch_to.window(h)
                            break

            return (label or "server button"), status, info

        except Exception:
            time.sleep(0.5)

    return "", None, None


# ============================================================
# SCRAPE PAGE
# ============================================================

def scrape_page(url, driver):

    driver.get(url)
    time.sleep(4)

    # Scroll to load lazy content
    driver.execute_script(
        "window.scrollTo(0, document.body.scrollHeight);"
    )
    time.sleep(2)
    driver.execute_script("window.scrollTo(0, 0);")
    time.sleep(1)

    page_title = find_page_title(driver)

    results = []

    elements = driver.find_elements(
        By.XPATH,
        "//a | //button | //*[@onclick]"
    )

    for element in elements:

        try:
            text = clean_text(element.text)

            if not text:
                continue

            text_lower = text.lower()

            # Ignore big wrapper elements; real buttons have short text
            if len(text) > 40:
                continue

            is_episode = "episode links" in text_lower

            is_download = "download links" in text_lower

            is_batch = (
                "batch/zip file" in text_lower
                or "batch / zip file" in text_lower
                or "batch zip file" in text_lower
            )

            if not (is_episode or is_batch or is_download):
                continue

            link = extract_link(element, url)

            # ------------------------------------------------
            # Read the description line belonging to THIS button
            # ------------------------------------------------
            try:
                line_text = clean_text(
                    driver.execute_script(FIND_LINE_JS, element)
                )
            except Exception:
                line_text = ""

            quality = find_quality(line_text)
            size = find_size(line_text)
            language = find_language(line_text)
            codec = find_codec(line_text)

            # Skip unrelated "Download Links" items (menus, etc.)
            if is_download and not (quality or size):
                continue

            # Movie pages have the name in the line itself
            # e.g. "Download The Deputy (2026) {English} 480p ..."
            title = clean_title(line_text) or page_title

            results.append({
                "title": title,
                "language": language,
                "quality": quality,
                "codec": codec,
                "size": size,
                "type": (
                    "Episode Links" if is_episode
                    else "Batch/Zip File" if is_batch
                    else "Download Links"
                ),
                "url": link
            })

        except Exception as e:
            log("Element error:", e)

    # ------------------------------------------------
    # Remove duplicates
    # ------------------------------------------------
    unique = []
    seen = set()

    for item in results:
        key = (
            item["title"],
            item["quality"],
            item["codec"],
            item["size"],
            item["type"],
            item["url"]
        )

        if key not in seen:
            seen.add(key)
            unique.append(item)

    return unique


# ============================================================
# SCAN IN A TEMPORARY TAB (closed automatically when finished)
# ============================================================

def scan_in_tab(url, driver):
    """
    Opens the page in a new tab, scrapes it, then closes that tab
    so you can see the scan is complete.
    """

    original = driver.current_window_handle

    driver.switch_to.new_window("tab")

    try:
        return scrape_page(url, driver)

    finally:
        try:
            if len(driver.window_handles) > 1:
                driver.close()
        except Exception:
            pass

        handles = driver.window_handles
        if handles:
            driver.switch_to.window(
                original if original in handles else handles[0]
            )


# ============================================================
# GUI
# ============================================================

class VideoSelector:

    def __init__(self, root):

        self.root = root
        self.root.title("Video Link Selector - Incognito")
        self.root.geometry("1100x700")
        self.root.minsize(900, 600)

        self.driver = None
        self.results = []

        # Chrome runs in the background by default (Settings menu)
        self.headless = True
        self.headless_var = tk.BooleanVar(value=True)

        # ---------------- MENU BAR ----------------
        menubar = tk.Menu(root)

        file_menu = tk.Menu(menubar, tearoff=0)
        file_menu.add_command(label="Exit", command=self.close)
        menubar.add_cascade(label="File", menu=file_menu)

        download_menu = tk.Menu(menubar, tearoff=0)
        download_menu.add_command(
            label="Download Selected",
            accelerator="Ctrl+D",
            command=self.open_selected
        )
        download_menu.add_separator()
        download_menu.add_command(
            label="Open Downloads Folder",
            command=self.open_downloads_folder
        )
        menubar.add_cascade(label="Download", menu=download_menu)

        settings_menu = tk.Menu(menubar, tearoff=0)
        settings_menu.add_checkbutton(
            label="Run browser in background (headless)",
            variable=self.headless_var,
            command=self.toggle_headless
        )
        menubar.add_cascade(label="Settings", menu=settings_menu)

        root.config(menu=menubar)
        root.bind("<Control-d>", lambda e: self.open_selected())

        # ---------------- TOP ----------------
        top = ttk.Frame(root, padding=15)
        top.pack(fill="x")

        ttk.Label(
            top,
            text="Paste Webpage URL:",
            font=("Arial", 12, "bold")
        ).pack(anchor="w")

        url_frame = ttk.Frame(top)
        url_frame.pack(fill="x", pady=8)

        self.url_entry = ttk.Entry(url_frame, font=("Arial", 11))
        self.url_entry.pack(side="left", fill="x", expand=True)

        self.scan_button = ttk.Button(
            url_frame,
            text="Scan Page",
            command=self.start_scan
        )
        self.scan_button.pack(side="left", padx=(10, 0))

        # ---------------- FILTERS ----------------
        filter_frame = ttk.LabelFrame(root, text="Filters", padding=10)
        filter_frame.pack(fill="x", padx=15, pady=5)

        ttk.Label(filter_frame, text="Quality:").pack(side="left")

        self.quality_var = tk.StringVar(value="All")
        self.quality_box = ttk.Combobox(
            filter_frame,
            textvariable=self.quality_var,
            state="readonly",
            width=12,
            values=["All", "480p", "720p", "1080p", "1440p", "2160p", "4K"]
        )
        self.quality_box.pack(side="left", padx=8)
        self.quality_box.bind(
            "<<ComboboxSelected>>", lambda e: self.refresh()
        )

        ttk.Label(filter_frame, text="Codec:").pack(side="left")

        self.codec_var = tk.StringVar(value="All")
        self.codec_box = ttk.Combobox(
            filter_frame,
            textvariable=self.codec_var,
            state="readonly",
            width=12,
            values=["All", "x264", "x265", "10Bit", "8Bit"]
        )
        self.codec_box.pack(side="left", padx=8)
        self.codec_box.bind(
            "<<ComboboxSelected>>", lambda e: self.refresh()
        )

        ttk.Label(filter_frame, text="Type:").pack(side="left")

        self.type_var = tk.StringVar(value="All")
        self.type_box = ttk.Combobox(
            filter_frame,
            textvariable=self.type_var,
            state="readonly",
            width=18,
            values=["All", "Episode Links", "Batch/Zip File", "Download Links"]
        )
        self.type_box.pack(side="left", padx=8)
        self.type_box.bind(
            "<<ComboboxSelected>>", lambda e: self.refresh()
        )

        # ---------------- RESULTS ----------------
        result_frame = ttk.Frame(root, padding=15)
        result_frame.pack(fill="both", expand=True)

        columns = ("title", "language", "quality", "codec", "size", "type")

        self.tree = ttk.Treeview(
            result_frame,
            columns=columns,
            show="headings",
            selectmode="browse"
        )

        headings = {
            "title": ("Title", 300),
            "language": ("Language", 130),
            "quality": ("Quality", 90),
            "codec": ("Codec", 110),
            "size": ("Size", 90),
            "type": ("Download Type", 170),
        }

        for col, (text, width) in headings.items():
            self.tree.heading(col, text=text)
            self.tree.column(col, width=width)

        scrollbar = ttk.Scrollbar(
            result_frame,
            orient="vertical",
            command=self.tree.yview
        )
        self.tree.configure(yscrollcommand=scrollbar.set)

        self.tree.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        # ---------------- BOTTOM ----------------
        bottom = ttk.Frame(root, padding=15)
        bottom.pack(fill="x")

        self.open_button = ttk.Button(
            bottom,
            text="Download Selected",
            command=self.open_selected
        )
        self.open_button.pack(side="left")

        self.status = ttk.Label(
            bottom,
            text="Paste a webpage URL and click Scan Page."
        )
        self.status.pack(side="left", padx=20)

        self.tree.bind("<Double-1>", lambda e: self.open_selected())
        self.root.protocol("WM_DELETE_WINDOW", self.close)

    # --------------------------------------------------------
    # SCAN
    # --------------------------------------------------------

    def start_scan(self):

        url = clean_url(self.url_entry.get())

        if not url:
            messagebox.showwarning(
                "URL Required",
                "Please paste a webpage URL."
            )
            return

        self.scan_button.config(state="disabled")
        self.status.config(
            text="Opening Incognito Chrome and scanning..."
        )

        threading.Thread(
            target=self.scan,
            args=(url,),
            daemon=True
        ).start()

    def scan(self, url):
        try:
            self.driver = get_live_driver(self.driver, self.headless)

            self.results = scan_in_tab(url, self.driver)

            self.root.after(0, self.scan_finished)

        except Exception as e:
            err = str(e)
            self.root.after(0, lambda: self.scan_error(err))

    def scan_finished(self):
        self.scan_button.config(state="normal")
        self.refresh()
        self.status.config(
            text=(
                f"Scan complete - found {len(self.results)} download "
                f"options. Scan tab closed."
            )
        )

    def scan_error(self, error):
        self.scan_button.config(state="normal")
        self.status.config(text="Scan failed.")
        messagebox.showerror("Error", error)

    # --------------------------------------------------------
    # REFRESH TABLE
    # --------------------------------------------------------

    def refresh(self):

        for row in self.tree.get_children():
            self.tree.delete(row)

        quality_filter = self.quality_var.get()
        codec_filter = self.codec_var.get()
        type_filter = self.type_var.get()

        for index, item in enumerate(self.results):

            if quality_filter != "All":
                if item["quality"].lower() != quality_filter.lower():
                    continue

            if codec_filter != "All":
                if codec_filter.lower() not in item["codec"].lower():
                    continue

            if type_filter != "All":
                if item["type"] != type_filter:
                    continue

            self.tree.insert(
                "",
                "end",
                iid=str(index),
                values=(
                    item["title"],
                    item["language"],
                    item["quality"],
                    item["codec"],
                    item["size"],
                    item["type"]
                )
            )

    # --------------------------------------------------------
    # OPEN SELECTED
    # --------------------------------------------------------

    def open_selected(self):

        selection = self.tree.selection()

        if not selection:
            messagebox.showinfo(
                "Select an option",
                "Please select a download option first."
            )
            return

        item = self.results[int(selection[0])]
        link = item["url"]

        if not link:
            messagebox.showwarning(
                "No Link",
                "This option does not expose a direct URL."
            )
            return

        log("\n==============================")
        log("Opening in Incognito tab:")
        log("Title:   ", item["title"])
        log("Language:", item["language"])
        log("Quality: ", item["quality"])
        log("Codec:   ", item["codec"])
        log("Size:    ", item["size"])
        log("Type:    ", item["type"])
        log("URL:     ", link)
        log("==============================")

        self.open_button.config(state="disabled")
        self.status.config(
            text="Opening link, following pages and starting download..."
        )

        threading.Thread(
            target=self.open_worker,
            args=(link,),
            daemon=True
        ).start()

    def open_worker(self, link):
        try:
            # Restart Chrome automatically if it was closed
            self.driver = get_live_driver(self.driver, self.headless)

            clicked, status, info = open_and_click_server(
                self.driver, link
            )

            if not clicked:
                msg = (
                    "Opened the page, but could not find a G-Drive "
                    "Fast Server button. Click it manually."
                )
            elif status == "download":
                msg = (
                    f"Download started: {', '.join(info)}  "
                    f"(saving to {DOWNLOAD_DIR})"
                )
            elif status == "final":
                msg = f"Clicked: {clicked} - reached Google Drive."
            else:
                msg = (
                    f"Clicked: {clicked}, but the download did not start "
                    f"in time. Continue manually in the browser."
                )
                if info:
                    msg += (
                        f"  Details: run_log.txt + {os.path.basename(info)}"
                    )

            self.root.after(0, lambda: self.open_finished(msg))

        except Exception as e:
            err = str(e)
            self.root.after(0, lambda: self.open_error(err))

    def open_finished(self, msg):
        self.open_button.config(state="normal")
        self.status.config(text=msg)

    def open_error(self, err):
        self.open_button.config(state="normal")
        self.status.config(text="Open link failed.")
        messagebox.showerror("Open Link Error", err)

    # --------------------------------------------------------
    # CLOSE
    # --------------------------------------------------------

    # --------------------------------------------------------
    # MENU ACTIONS
    # --------------------------------------------------------

    def open_downloads_folder(self):
        try:
            if sys.platform.startswith("win"):
                os.startfile(DOWNLOAD_DIR)
            elif sys.platform == "darwin":
                subprocess.Popen(["open", DOWNLOAD_DIR])
            else:
                subprocess.Popen(["xdg-open", DOWNLOAD_DIR])
        except Exception as e:
            messagebox.showerror("Open Folder Error", str(e))

    def toggle_headless(self):
        new_value = self.headless_var.get()

        active = glob.glob(os.path.join(DOWNLOAD_DIR, "*.crdownload"))

        if active and not messagebox.askyesno(
            "Downloads in progress",
            "Changing this restarts the browser and will cancel "
            "running downloads. Continue?"
        ):
            self.headless_var.set(not new_value)      # undo the tick
            return

        self.headless = new_value

        # Restart Chrome so the new mode applies
        if self.driver is not None:
            try:
                self.driver.quit()
            except Exception:
                pass
            self.driver = None

        self.status.config(
            text=(
                "Browser will run in the background."
                if self.headless
                else "Browser window will be shown."
            )
        )

    def close(self):

        # Closing Chrome cancels downloads that are still running
        active = glob.glob(os.path.join(DOWNLOAD_DIR, "*.crdownload"))

        if active and not messagebox.askyesno(
            "Downloads in progress",
            f"{len(active)} download(s) are still running.\n"
            "Closing now will cancel them. Close anyway?"
        ):
            return

        try:
            if self.driver:
                self.driver.quit()
        except Exception:
            pass

        self.root.destroy()


# ============================================================
# MAIN
# ============================================================

if __name__ == "__main__":
    root = tk.Tk()
    app = VideoSelector(root)
    root.mainloop()
