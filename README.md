# Avito → Telegram tracker

Get a Telegram message for every new ad on your Avito.ru saved searches.

**How it works:** Avito emails you when a saved search has new ads. Every 5 minutes this tracker
reads those emails (read-only, over IMAP), pulls out each ad's title, price, photo and link, and
sends them to your Telegram bot. It never contacts Avito itself, so Avito's bot protection
doesn't affect it.

```
Avito saved search ──email──▶ your Gmail ──IMAP──▶ tracker (GitHub Actions) ──▶ Telegram bot ──▶ you
```

## Setup

### 1. Avito: save your searches with email alerts
1. On avito.ru, set up a search (category, city, keywords, price), then click **Сохранить поиск** (Save search).
2. In **Избранное → Поиски** (Favourites → Searches), turn on notifications **by email** and set the
   frequency to as often as possible.
3. Wait until at least one alert email arrives in your Gmail. If Avito only offers app notifications,
   this tracker can't work. Stop here and let me know.

### 2. Gmail: create an app password
1. Turn on 2-Step Verification for your Google account.
2. Go to <https://myaccount.google.com/apppasswords>, create an app password named "Avito tracker",
   and copy the 16-character password. You enter it yourself in step 4 or 5. It isn't your normal
   Gmail password, and you can revoke it anytime from the same page.

The tracker only reads mail. It opens the mailbox read-only and doesn't mark emails as read.
If you use a different email provider, set `IMAP_HOST` (for example `imap.mail.ru` or `imap.yandex.ru`).

### 3. Telegram: find your chat ID
1. Send any message to your bot.
2. Open `https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates` in a browser.
3. The number at `"chat":{"id": ... }` is your `TELEGRAM_CHAT_ID`.

### 4. Try it on your PC (optional, recommended)
```bash
pip install -r requirements-dev.txt
```
Copy `.env.example` to `.env` and fill in the four values, then run one check:
```bash
python -m tracker.main
```
The bot should say "✅ Connected…". Send `/test` to the bot and run the command again. It re-sends the
ads from your latest Avito email, so you can check that titles and prices come out right.

**Stop the local runs before turning on GitHub.** Two copies running at the same time would steal
each other's bot commands.

### 5. Run it on GitHub (free, every 5 minutes)
1. Create a **public** repository on GitHub and push this folder to it. Public repos get unlimited free
   Actions minutes, and every-5-minutes uses more than a private repo's free allowance.
2. In the repo, go to **Settings → Secrets and variables → Actions → New repository secret** and add:
   `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `EMAIL_ADDRESS`, `EMAIL_APP_PASSWORD`.
3. Open **Actions → Avito email tracker → Run workflow** to run it once by hand. After that, it runs on
   its own every 5 minutes. GitHub often starts these runs late, so in practice it checks every 5–15 minutes.

What other people can see in a public repo:
- **Never visible:** your secrets.
- **Visible in the run logs:** counts only, such as "OK: 0 command(s), 1 new Avito email(s)".
- **Visible in `state.json`:** IDs of ads you were sent.

## Bot commands
Commands are answered on the next run, so it can take up to about 15 minutes on GitHub.

| Command | What it does |
|---|---|
| `/status` | Running or paused, and how many ads have been forwarded |
| `/pause` / `/resume` | Stop or restart forwarding. Emails that arrive while paused are skipped. |
| `/test` | Re-send the ads from the latest Avito email |
| `/help` | List the commands |

The bot ignores messages from anyone except your own chat ID.

## Optional settings
Add these as Repository **variables** (not secrets) on GitHub, or in `.env` locally.

| Name | Default | Meaning |
|---|---|---|
| `IMAP_HOST` | `imap.gmail.com` | Your provider's IMAP server |
| `IMAP_FOLDER` | `INBOX` | Folder to watch. Use `[Gmail]/All Mail` if a Gmail filter archives Avito emails. |
| `SENDER_FILTER` | `avito.ru` | Comma-separated text that the From address must contain |
| `MAX_ADS_PER_EMAIL` | `10` | Ads sent per email. The rest are summarized as "…and N more". |
| `FORWARD_UNPARSED` | `true` | Tell you about Avito emails with no recognizable ads |

## Run on your PC instead of GitHub
Fill in `.env` as in step 4, then schedule `run_tracker.bat` every 5 minutes:
```bash
schtasks /Create /SC MINUTE /MO 5 /TN "AvitoTracker" /TR "\"D:\Avito Tracking\run_tracker.bat\""
```
Output goes to `tracker.log`. To remove the scheduled task: `schtasks /Delete /TN "AvitoTracker" /F`.

## Troubleshooting
- **"⚠️ Can't read your mailbox"**: the app password or email address is wrong, or 2-Step Verification is off.
- **"📩 New Avito email (no ads detected)"**: Avito's email layout didn't match what the parser expects.
  Run `/test`, and if it's still wrong, share the email's HTML (with personal details removed) so the parser can be adjusted.
- **No messages at all**: open the Actions tab and check the latest run. GitHub turns off scheduled workflows
  after 60 days without repository activity. The tracker's own state commits normally keep the repo active.

## Development
```bash
python -m pytest
```
The tests use a hand-made email shaped like an Avito alert, not a real one.
