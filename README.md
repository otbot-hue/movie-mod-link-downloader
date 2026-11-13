# Movie Mod Link Downloader

A small Windows desktop tool (Python + Tkinter + Selenium) that scans a movie
download page, lists the available download options in a table, and then
automatically follows the G-Drive "Fast Server" chain until Chrome starts
downloading the file.

There are two versions in this folder:

| File | Browser mode | Notes |
|---|---|---|
| `download in background/video_link_selector.py` | Headless (background) by default | **Recommended.** Adds logging, Save-As handling, a Settings menu, and a Downloads-folder shortcut. |
| `mod_link_download.py` | Visible Incognito Chrome | Original, simpler version. Same scan/download logic without the extras. |

## Features

- Paste a URL, click **Scan Page**, and get the available options in a table.
- Columns: Title, Language, Quality, Codec, Size, Download Type.
- Filters for Quality, Codec, and Type (Episode Links / Batch-Zip / Download Links).
- Double-click a row (or press **Ctrl+D**) to download it.
- Ranks server buttons so **G-Drive "Fast Server"** wins over other servers.
- Clicks through the intermediate verify / generate-link / "go to download" pages.
- Removes common ad overlays and closes ad pop-up tabs automatically.
- Clicks **Instant Download** first, then **Resume Cloud** if needed. It never
  clicks Telegram or Login buttons.
- Saves files to your normal Downloads folder and reports the file name.
- On failure, writes a screenshot (`stuck_<timestamp>.png`) for debugging.
- The background version also writes a log to `run_log.txt`.

## Requirements

- Windows (the automatic native "Save As" handling is Windows-only)
- Python 3.10+ (tested with Python 3.11.9)
- Google Chrome installed
- Selenium 4.6+ (it downloads the matching driver automatically)
- Internet access

Tkinter is included with the standard python.org Windows installer. If it is
missing, reinstall Python and enable **tcl/tk and IDLE**.

## Setup

Open a terminal in this folder:

```powershell
python -m pip install -U selenium
```

## Run

Recommended (background Chrome):

```powershell
python "download in background\video_link_selector.py"
```

Original (visible Incognito Chrome):

```powershell
python mod_link_download.py
```

## How to use

1. Paste the movie/episode page URL and click **Scan Page**.
2. Wait for the scan tab to close and the table to fill in.
3. Optionally narrow the list with the Quality / Codec / Type filters.
4. Select a row and click **Download Selected** (or double-click it / Ctrl+D).
5. Wait for the status line. A successful run reports
   `Download started: <file> (saving to ...\Downloads)`.
6. Keep the app (and its Chrome session) open until the download finishes.
   Closing the app cancels in-progress downloads and asks for confirmation.

In the background version:

- **Download > Open Downloads Folder** opens the download folder.
- **Settings > Run browser in background (headless)** toggles headless mode.
  Changing it restarts Chrome, so do it when no download is running.

## Where things are saved

| Item | Default location |
|---|---|
| Download folder | `%USERPROFILE%\Downloads` |
| Log (background version) | `download in background\run_log.txt` |
| Failure screenshot | Next to the script, `stuck_<timestamp>.png` |

To change the download folder, edit `DOWNLOAD_DIR` near the top of the script.

## Configuration

All tuning values are constants at the top of both scripts:

| Constant | Default | Meaning |
|---|---|---|
| `SERVER_WAIT_SECONDS` | `25` | How long to wait for a Fast Server button on the server page. |
| `GATE_WAIT_SECONDS` | `180` | How long to keep following the link chain before giving up. |
| `FINAL_HOSTS` | Google Drive hosts | Hosts that count as "reached the file". |
| `DOWNLOAD_BUTTON_TEXTS` | `instant download`, `resume cloud` | Buttons clicked on the download page, in priority order. |
| `CONTINUE_TEXTS` | `go to download`, `generate link`, ... | Buttons clicked on intermediate pages, best first. |
| `DOWNLOAD_DIR` | `~/Downloads` | Where Chrome saves files. |
| `LANGUAGE_WORDS` | English, Hindi, ... | Languages detected in the description line. |

Server button ranking is in `server_score()`: Fast Server 1 (G-Drive) = 100,
other Fast Server + G-Drive = 90, other Fast Server = 50, plain Google Drive = 30.

## Troubleshooting

| Problem | What to try |
|---|---|
| `ModuleNotFoundError: selenium` | Run `python -m pip install -U selenium`. |
| Chrome does not start / driver error | Update Chrome and Selenium. The first run needs internet so Selenium Manager can fetch the driver. |
| Scan finds 0 options | The URL may not be a download-list page anymore. Some links now open a "Please Select a Specific Download Link" server chooser; that page has no rows to scan. Open the movie page, not the server page. |
| "Could not find a G-Drive Fast Server button" | The site changed its labels or is showing a CAPTCHA/login. Run in visible mode, increase `SERVER_WAIT_SECONDS`, or click the server manually. |
| "Download did not start in time" | Check `run_log.txt` and the `stuck_*.png` screenshot. Try the visible mode to see which page is blocking. Some pages require a manual click or verification. |
| A wrong/duplicate row appears | The scanner associates a button with the nearest quality line. A menu/footer "Download Links" link placed right after a real section can inherit that section's details. Filters or the URL usually make it obvious. |
| Downloads stop when closing the app | Expected: the tool closes its Chrome session. Keep the app open until downloads finish. |
| Scan is slow | Each scan loads the page in Chrome, scrolls it, and inspects every link/button. Pages with many ads take longer. |

## Verification status

Checked on 2026-11-13:

- ✅ Both scripts compile: `python -m py_compile` passes.
- ✅ Selenium 4.52.0 + headless Chrome starts successfully.
- ✅ Helper functions pass 23 checks: URL cleanup, quality/size/codec/language
  detection, title cleanup, server ranking, host detection.
- ✅ Scraping a local test page correctly found all 4 intended download sections
  with their quality, codec, size, language, type, and URL.
- ✅ The Tkinter window constructs successfully, and the table plus Quality/Type
  filters work.
- ⚠️ The live sample URL from `run_log.txt` (`links.modpro.blog/archives/...`)
  now opens the site's server-chooser page ("Please Select a Specific Download
  Link to Proceed"), so that run returned 0 rows. This is a site/page change,
  not a crash.
- ⚠️ The full download chain (server → verify/generate pages → file saved) was
  not re-run end-to-end during this check, because it downloads a real file.
  The existing `run_log.txt` shows it reaching the final Instant Download page.

One known scan edge case was reproduced: an unrelated menu "Download Links" link
that follows a real section can inherit that section's quality/size and appear
as an extra row.

## Notes

- This tool automates a browser. It does not bypass CAPTCHAs, logins, or
  paywalls; pages that require them must be handled manually.
- Use it only for content you are allowed to download. The bundled site is a
  third-party index and is not affiliated with this tool.