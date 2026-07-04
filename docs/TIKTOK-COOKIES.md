# TikTok cookies for login-gated posts

## Why this exists

Some TikTok posts are flagged by TikTok as "sensitive" and return this error
from `yt-dlp` even though the post is public and viewable in a browser:

```
ERROR: [TikTok] <video-id>: This post may not be comfortable for some
audiences. Log in for access. Use --cookies-from-browser or --cookies for
the authentication.
```

This happened for real with `ZP8GDatEa` (2026-07-03). It's TikTok's own
classifier being conservative, not necessarily a signal anything's wrong with
the content — it can happen even for the operator's own posts.

**Why not `--cookies-from-browser` (live browser cookie access)?** Tried it
first (Safari) and it failed with `Operation not permitted` — macOS's Full
Disk Access protection blocks reading Safari's cookie container from a
background process. The actual chain that would need Full Disk Access here
is `launchd → n8n (node) → worker.py (python3) → yt-dlp`, and macOS's TCC
permission system doesn't cleanly attribute through that many process layers
— there's no single app to grant permission to and be done with it. A static
exported `cookies.txt` file sidesteps this entirely: it's just a plain text
file `yt-dlp --cookies <file>` reads directly, no live browser access needed.

## There are actually TWO separate things needed here — both real, both hit

Getting `ZP8GDatEa` to download for real surfaced two independent gaps, not
one. Fixing only one still leaves the exact same error message on screen —
they look identical from the error text alone.

### 1. `curl_cffi` (browser-impersonation) — DONE, fixed 2026-07-03

Every attempt (with or without cookies) showed this warning first:
```
WARNING: [TikTok] The extractor is attempting impersonation, but no
impersonate target is available.
```
TikTok's anti-bot detection checks the TLS/HTTP fingerprint of the request,
not just cookies — without `curl_cffi`, yt-dlp can't mimic a real browser's
fingerprint and TikTok may reject the request regardless of cookie validity.
**yt-dlp runs its own isolated Python environment** (Homebrew formula,
`/opt/homebrew/Cellar/yt-dlp/<version>/libexec/bin/python` — NOT this repo's
`venv/`, NOT system `python3`), so the fix has to go into that specific
interpreter:
```bash
/opt/homebrew/Cellar/yt-dlp/<version>/libexec/bin/python -m pip install curl_cffi
```
Verify: `yt-dlp --list-impersonate-targets` should list real targets
(Chrome/Safari/Firefox/etc.), not "(unavailable)" next to every one.

**Gotcha to remember**: `brew upgrade yt-dlp` may reinstall/replace this
`libexec` venv, silently wiping the pip-installed `curl_cffi` — if the
impersonation warning ever comes back after an upgrade, re-run the pip
install above.

### 2. A cookies.txt with the REAL session cookie, not just anonymous ones

**RESOLVED 2026-07-03**, verified with a real download of the actual
failing video (`ZP8GDatEa`, 2.25MB, zero errors). The first export (from
Safari) only captured `ttwid`, `tt_csrf_token`, `tt_chain_token` — **these
are set even for a logged-out visitor**, none of them are the actual auth
cookie (typically `sessionid`, `sid_tt`, `uid_tt`, or similar). The download
still failed with the same "log in for access" message even with curl_cffi
fixed, because the cookies file didn't actually prove a login.

**Likely cause**: Safari's extension cookie-access API is more restrictive
than Chrome's/Firefox's — many lightweight cookie-export tools can only read
`document.cookie` (JavaScript-visible cookies), and TikTok's real session
cookie is deliberately marked `httpOnly` specifically to hide it from page
JavaScript. **Fix: export from Chrome or Firefox instead** (while logged
into TikTok there) — their cookie-export extensions use the browser's native
cookie API, which can read `httpOnly` cookies Safari's extension API
typically can't reach.

### ⚠️ A second trap: the export can grab EVERY site's cookies, not just TikTok's

When re-exporting from Chrome, "Get cookies.txt LOCALLY" exported the
**entire browser cookie jar** — 819KB covering LinkedIn, Amazon, Shopify,
Stripe, Indeed, and dozens of other logged-in sites, not just `tiktok.com`.
That's a real problem: it would leave a lot of unrelated, sensitive session
tokens sitting in a plain file in this repo's directory, for services that
have nothing to do with this pipeline.

**Always filter the export down to only TikTok's domains before saving it
as `tiktok_cookies.txt`.** One-liner (never prints cookie values, only
processes the file):
```bash
cd /Users/jramirez/Git/avatar-pipeline
{
  echo "# Netscape HTTP Cookie File"
  grep -v "^#" ~/Downloads/tiktok.com_cookies.txt | \
    awk -F'\t' '$1==".tiktok.com" || $1==".www.tiktok.com" || $1=="www.tiktok.com" {print}'
} > tiktok_cookies.txt
```
(Adjust the source filename to whatever the extension actually downloaded.)
Then delete the original raw export from `~/Downloads` — don't leave a copy
of your full cookie jar sitting around either.

## One-time setup

1. Install a cookie-export browser extension in **Chrome or Firefox**
   (**not Safari** — see above). **"Get cookies.txt LOCALLY"** is a common
   one, available for both.
2. Log into TikTok in that browser (the operator's own account) — actually
   log in, don't just browse anonymously.
3. Navigate to `tiktok.com`, click the extension, export cookies in
   **Netscape format** — this is the format `yt-dlp` expects (the same
   format used by curl's `-c`/`-b` flags). **Check whether the extension is
   exporting only the current tab's site or your whole cookie jar** — if
   there's no way to scope it, export everything and filter it down
   yourself (see the trap above) before it ever becomes `tiktok_cookies.txt`.
4. Save the **filtered** (TikTok-only) file to:
   ```
   /Users/jramirez/Git/avatar-pipeline/tiktok_cookies.txt
   ```
   (this exact path is already wired into `config.yaml`'s
   `video.cookies_file` — no config change needed once the file exists here)
5. **Add it to `.gitignore`** if it isn't already covered — this file
   contains live session cookies, treat it like a credential. (This repo
   isn't a git repo currently, but if that changes, don't commit this file.)

## Verifying it worked

**Sanity-check the file actually has the real session cookie** (names only,
never print the values):
```bash
grep -v "^#" tiktok_cookies.txt | grep -v "^$" | awk -F'\t' '{print $6}'
```
Should include something like `sessionid` or `sid_tt` — if you only see
`ttwid`/`tt_csrf_token`/`tt_chain_token` again, the export didn't actually
capture a logged-in session; double check step 2.

```bash
cd /Users/jramirez/Git/avatar-pipeline
./venv/bin/python scripts/worker.py --config config.yaml --dry-run --url "https://www.tiktok.com/@example/video/1"
```
Check the `warnings` list in the output — if `video.cookies_file is set but
missing` appears, the file isn't at the expected path yet. No warning just
means the file exists, not that the cookies inside are actually valid.

To confirm the cookies actually work against a real gated video:

```bash
yt-dlp --no-playlist -f mp4/best --no-part --cookies /Users/jramirez/Git/avatar-pipeline/tiktok_cookies.txt \
  -o /tmp/test_dl.mp4 "https://www.tiktok.com/t/ZP8GDatEa"
```

A successful download means the cookies are valid; a login/auth error again
means the exported session doesn't actually contain a real login (see
section 2 above) or has expired — repeat steps 2-4.

**Confirmed working 2026-07-03**: this exact command, against the exact
video that originally failed (`ZP8GDatEa`), succeeded — 2.25MB downloaded,
zero errors. Both fixes (curl_cffi + a properly-scoped, real-login
cookies.txt) are what got it there.

## Maintenance

TikTok session cookies expire periodically (weeks to months, not predictable
in advance). When downloads that used to work start failing with the same
"log in for access" error again, re-export following steps 2-4 above — no
code changes needed, just overwrite the same file path.

Separately: if the impersonation warning (`"attempting impersonation, but no
impersonate target is available"`) ever reappears, `curl_cffi` got wiped —
almost certainly by `brew upgrade yt-dlp` recreating its `libexec` venv.
Re-run the pip install from section 1 above. Check
`yt-dlp --list-impersonate-targets` any time both fixes need re-verifying.

## Retry semantics

A download failure (including this cookie-gate error) does **not**
permanently flag the URL — see `worker.py`'s `_clear_without_consuming()`.
The link stays retryable; once you've re-exported fresh cookies, the exact
same TikTok link can be pasted into Telegram again or picked up by the next
scheduled run without any manual un-flagging step.
