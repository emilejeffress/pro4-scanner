# PRO4 Forex Scanner (cloud)

Runs your scanner in the cloud, so your PC can be off. GitHub runs it every few minutes during NY hours. It sends
phone alerts through ntfy, paper-trades every signal into a journal, and publishes a phone-friendly dashboard.

* **Connors RSI-2 (H1):** EUR/USD, GBP/USD, USD/CHF, USD/JPY, EUR/JPY
* **TEST (paper only until they pass):** NZD/USD, EUR/CHF
* Signals only. It never places trades. Free: GitHub + Twelve Data free plan (about 100 of 800 credits a day) + ntfy.

## Setup (about 10 minutes, on a PC)

1. **GitHub account:** sign up free at github.com.
2. **New repository:** **+ (top right) → New repository** → name `pro4-scanner` → **Public** → **Create repository**.
3. **Upload the files:** on the new repo page, click **uploading an existing file**. Open the unzipped `pro4-cloud` folder,
   select everything inside it (`config.json`, `README.md`, `scanner`, `docs`, `.github`), drag it all in, then **Commit changes**.
   * Check that a `.github` folder now shows in the repo. If it doesn't: **Add file → Create new file**, name it
     `.github/workflows/scan.yml`, paste in the contents of that file from the zip, and **Commit**.
4. **Secrets:** **Settings → Secrets and variables → Actions → New repository secret**, twice:
   * Name `TWELVE_DATA_KEY`, value = your Twelve Data API key
   * Name `NTFY_TOPIC`, value = your ntfy topic (the same one your phone's ntfy app is subscribed to)
5. **Dashboard:** **Settings → Pages** → Source **Deploy from a branch** → Branch **main**, folder **/docs** → **Save**.
   After 1–2 minutes the page shows your link: `https://YOUR-USERNAME.github.io/pro4-scanner/`
6. **Turn it on:** **Actions** tab → **I understand my workflows, go ahead and enable them** → click **PRO4 scanner** →
   **Run workflow** → **Run workflow**. Wait for the green check (about 1 minute), then open your dashboard link.
7. **On your phone:** open the dashboard link in Chrome → **⋮ → Add to Home screen**, so it opens like an app.
8. *(Optional)* In the repo, open `config.json` → pencil icon → paste your dashboard link into `"dashboard_url"` → **Commit**.
   Tapping a phone alert then opens the dashboard.

## Settings (`config.json`, edit on GitHub with the pencil icon)

* `fixed_lot`: lot size shown in alerts and used for the journal's $ figures (0.03).
* `pairs → "on": true/false`: switch a pair on or off. `spread_pips`: match Hugo's Way.
* `commission_usd_per_lot_round_trip`: 10.
* `strategy`: the tested Connors values. Changing them means trading something untested.

## Good to know

* Scheduled runs can start a few minutes late on GitHub. Entries more than 45 minutes after the hourly close are skipped.
* The dashboard can lag up to ~10 minutes behind (GitHub Pages caching). Phone alerts arrive right away.
* If GitHub ever emails that the scheduled workflow was disabled: **Actions → PRO4 scanner → Enable workflow**.
* The journal is `docs/data/journal.json`. Upload it to Claude any time for a review.
