# Tailscale Funnel — exposing local n8n to the public internet

> **Why this exists:** the Telegram approval loop (see `HANDOFF.md` →
> "TELEGRAM APPROVAL LOOP") uses n8n's **Telegram Trigger**, which is a
> *webhook* — Telegram's servers call *out* to n8n when you reply "yes"/"no".
> n8n runs only on `localhost:5678` on this Mac, which Telegram can't reach.
> **Tailscale Funnel** gives that local port a stable public HTTPS hostname
> (`https://<machine>.<tailnet>.ts.net`) with an auto-provisioned TLS cert, so
> the webhook can fire. No domain, no port-forwarding, no ngrok-style URL that
> changes on every restart.

Funnel proxies **public 443 → local 5678**. n8n keeps listening on localhost;
Tailscale is the only thing exposed, and only the one port you choose.

---

## Status: LIVE (this machine, 2026-07-03)

- **Tailscale installed and logged in** — the standalone macOS app
  (`io.tailscale.ipn.macsys`, *not* the sandboxed Mac App Store build), device
  `<device>` on tailnet `<tailnet>.ts.net`. CLI on PATH at
  `/usr/local/bin/tailscale`.
- **Funnel enabled and running**: `tailscale funnel --bg 5678` is active.
  `tailscale funnel status` shows `https://<device>.<tailnet>.ts.net`
  proxying to `http://127.0.0.1:5678`.
- **n8n plist wired**: `ops/launchd/com.jramirez.avatar.n8n.plist`'s
  `WEBHOOK_URL` is set to `https://<device>.<tailnet>.ts.net/`, deployed to
  `~/Library/LaunchAgents/`, n8n restarted and picked it up.
- **Verified end-to-end**: `curl https://<device>.<tailnet>.ts.net/` returns
  a real `HTTP/2 200` serving the n8n editor HTML — the public tunnel works.
  (Real hostname redacted here — see your locally-deployed
  `~/Library/LaunchAgents/com.jramirez.avatar.n8n.plist` for the actual value.)

Persists across reboots as long as the Mac stays logged into Tailscale
(check with `tailscale status` — should show the device, not "Logged out").

The steps below are kept for reference (e.g. re-running on a new machine, or
if this ever needs to be redone) — not a to-do list anymore for *this* setup.

### 1. Log in to Tailscale

```bash
tailscale up
```

This opens (or prints) a `https://login.tailscale.com/a/...` URL — authenticate
in the browser with your Tailscale account (Google/GitHub/Microsoft/email all
work; free "Personal" plan is enough for Funnel). Verify:

```bash
tailscale status          # should now show this machine + your tailnet, not "Logged out."
```

### 2. Enable MagicDNS + HTTPS certificates (admin console, one-time per tailnet)

Funnel requires HTTPS, which requires MagicDNS. In
<https://login.tailscale.com/admin/dns>:

- Turn on **MagicDNS**.
- Turn on **HTTPS Certificates**.

(These are tailnet-wide switches — do them once and every device benefits.)

### 3. Enable Funnel + expose n8n

Funnel is gated behind a node attribute in your tailnet policy. The easiest
path: just run the command — if your tailnet hasn't granted Funnel yet, the CLI
prints a one-click URL to enable it, then you re-run.

```bash
# Serve local n8n (port 5678) publicly. --bg runs it in the background so it
# survives the terminal closing; Tailscale re-establishes it on reboot.
tailscale funnel --bg 5678
```

If it complains that Funnel isn't enabled, open the URL it prints (adds the
`funnel` attribute to your ACL policy for this node), then re-run the command.

Confirm and grab your public hostname:

```bash
tailscale funnel status
```

You'll see something like:

```
# Funnel on:
#     - https://your-macbook.tailXXXX.ts.net

https://your-macbook.tailXXXX.ts.net (Funnel on)
|-- / proxy http://127.0.0.1:5678
```

That `https://your-macbook.tailXXXX.ts.net` is your public URL. **Paste it back
to me** and I'll finish step 4, or do it yourself below.

### 4. Point n8n's WEBHOOK_URL at the Funnel hostname

Edit `ops/launchd/com.jramirez.avatar.n8n.plist` — replace the placeholder
`WEBHOOK_URL` value with your real hostname **(keep the trailing slash)**:

```xml
<key>WEBHOOK_URL</key>
<string>https://your-macbook.tailXXXX.ts.net/</string>
```

Then redeploy the plist (the pattern used for all `com.jramirez.avatar.*`
services — the live copy lives in `~/Library/LaunchAgents/`):

```bash
cp ops/launchd/com.jramirez.avatar.n8n.plist ~/Library/LaunchAgents/
launchctl kickstart -k gui/$(id -u)/com.jramirez.avatar.n8n
```

Verify n8n now believes it's public:

```bash
# From another network/device (or your phone off wifi), this should load n8n:
curl -sI https://your-macbook.tailXXXX.ts.net/ | head -1   # HTTP/2 200
```

### 5. Bind the Telegram credential + activate the reply workflow

Now the webhook can actually be reached. Finish the Telegram side (see
`HANDOFF.md` → "What's actually left"):

1. n8n UI → **Settings → Credentials → New → Telegram API**, paste your bot
   token (n8n encrypts it in its own DB — never goes in a repo file).
2. Open the **`AvatarPipeTelegramReply1`** workflow → the **Telegram Trigger**
   node → re-select your credential from the dropdown (the imported JSON only
   carries a placeholder credential id).
3. **Activate** the workflow. On activation, n8n calls Telegram's `setWebhook`
   using `WEBHOOK_URL` — so it registers `https://…ts.net/webhook/…`, which
   Telegram can now reach.
4. Reply to the bot in Telegram and watch `AvatarPipeTelegramReply1`'s
   executions light up.

---

## Operating notes

- **Is Funnel up?** `tailscale funnel status`. **Turn it off:**
  `tailscale funnel --bg off` (or `tailscale funnel reset` to clear all serve
  config).
- **Survives reboot?** Yes — `--bg` config is persisted by tailscaled and
  re-applied on login/reboot, as long as you stay logged in to Tailscale.
- **Security surface:** only the single port you funneled (5678 → n8n) is
  public; everything else on the Mac stays private to your tailnet. n8n's own
  login still gates the editor UI. The webhook path itself is an unguessable
  n8n-generated URL. If you want to *also* reach the n8n editor privately
  (without exposing it via Funnel), that's plain `tailscale serve` /
  MagicDNS — you're already on the tailnet.
- **Cost:** Funnel is included on Tailscale's free Personal plan. No card.
- **The `WEBHOOK_URL` must match the Funnel host exactly.** If they drift
  (e.g. you rename the machine, which changes the ts.net host), Telegram will
  have a stale webhook — re-run steps 3–4 and re-activate the workflow.

---

## Replicating this for a future project (generic recipe)

Any time you need "a webhook/callback from the public internet to hit a service
running on my local machine," this is the whole pattern:

1. **Install Tailscale** (once per machine): the standalone app from
   <https://tailscale.com/download/mac> (get the *non*-App-Store build for full
   CLI + system daemon), or `brew install tailscale` for a fully headless
   box + `sudo tailscaled install-system-daemon`. Don't run both variants'
   daemons at once — pick one. `tailscale up` to log in.
2. **Enable MagicDNS + HTTPS Certificates** once per tailnet
   (<https://login.tailscale.com/admin/dns>). Enable Funnel once per node
   (the CLI hands you the URL the first time).
3. **Expose the port:** `tailscale funnel --bg <LOCAL_PORT>`. Public URL comes
   from `tailscale funnel status` → `https://<machine>.<tailnet>.ts.net`.
   Public side is always 443/HTTPS with an auto TLS cert; it proxies to your
   local plaintext port.
4. **Tell the app its public base URL** if it self-registers webhooks or builds
   absolute links (n8n: `WEBHOOK_URL`; most frameworks have an equivalent
   `PUBLIC_URL`/`BASE_URL`). Otherwise you just hand the `…ts.net` URL to
   whoever needs to call you (Telegram, Stripe, GitHub, a partner API, etc.).
5. **Register the URL** with the external service as its webhook/callback
   target.

Funnel is the right tool when you specifically need the *public* internet to
reach you. If instead you only need *your own* devices to reach the service
(a private dashboard, an internal API), use `tailscale serve` (tailnet-only,
no public exposure) — same mechanics, drop the "funnel" and it's not internet-
facing.

### Funnel vs. the alternatives (why Tailscale here)

| Option | Domain needed? | Stable URL free? | Runs as bg service | Notes |
|---|---|---|---|---|
| **Tailscale Funnel** | no | **yes** (`.ts.net`) | yes | auto HTTPS; free Personal plan; already a mesh VPN too |
| Cloudflare Tunnel | yes (domain in CF) | yes | yes (`cloudflared`) | most robust but needs a domain on Cloudflare |
| ngrok (free) | no | **no** (URL rotates) | awkward | reserved domain is paid |

For this project Tailscale won: no domain required (unlike Cloudflare) and a
stable hostname on the free tier (unlike ngrok).
