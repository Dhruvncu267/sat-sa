# SAT-SA — Supervisory Analytics Tool for SOC Assessment

Built for **SIH 2026 Problem Statement 26157** (NCIIPC / National Technical
Research Organisation): a tool that reads an organization's security alert
data and tells a supervisor, in plain English, whether that organization is
handling cyber incidents well or badly — and exactly why.

This is the **final, deployable version**: real Supervisor login (Firebase
Authentication), a shared Firestore database so every supervisor sees the
same organizations and history, a calendar to pull up any past assessment,
one-click delete, hover tooltips explaining every term, and a fixed-format
PDF report — all served from a single public web address, so nobody needs
Python, a terminal, or your laptop running to use it.

## What's inside vs. what changed

The actual "brain" of the tool — the rule-based detectors, the peer scoring,
the Isolation Forest ML model, and the pipeline that combines them — is
**completely unchanged** from the earlier prototype. This version only
replaces *where data lives* and *who can see it*:

- **Before:** data lived in local CSV files on one laptop; anyone with the
  URL could open it, no login.
- **Now:** data lives in Firestore (Google's cloud database), every
  organization's raw data and every dated assessment are saved there, and
  only accounts created through the private `/setup` link (see below) can
  sign in and see anything at all. There is no public sign-up page.

## What's in here

```
sat_sa/
├── data/
│   └── generate_data.py       # (dev/demo only) generates realistic fake companies
├── engine/
│   ├── detectors.py           # 9 rule-based checks for weak incident handling — unchanged
│   ├── scoring.py             # peer benchmarking + composite risk scoring — unchanged
│   ├── pipeline.py            # shared brain: runs rules + ML -> one result — unchanged
│   ├── ingest.py              # turns one uploaded CSV into the engine's internal tables
│   └── build.py               # (optional) static dashboard export
├── ml/
│   └── anomaly_model.py       # Isolation Forest (scikit-learn) anomaly-detection layer — unchanged
├── server/
│   ├── app.py                 # Flask REST API + serves the UI; every data route requires login
│   ├── firebase_setup.py      # Firebase Admin SDK init + ID-token verification
│   ├── firestore_store.py     # all reads/writes to Firestore (orgs, assessments, supervisors)
│   ├── report_pdf.py          # builds the fixed-format professional PDF report
│   ├── create_supervisor.py   # (optional, advanced) command-line alternative to the /setup web page
│   └── static/                # the webpage (login.html, index.html, styles.css, app.js, login.js)
├── Procfile                    # for deploying to Render/Railway/Heroku-style hosts
├── requirements.txt
└── README.md
```

## 1. Set up Firebase (one-time, ~10 minutes)

You already created a Firebase project (`sat-sa`) with Authentication and
Firestore enabled — if you're setting this up fresh, or on a new machine,
here's the full process:

1. Go to [console.firebase.google.com](https://console.firebase.google.com) → **Add project**.
2. **Build → Authentication → Get started → Sign-in method → Email/Password → Enable.** Do NOT enable any "self sign-up" — this app never calls the sign-up API, so there is no public registration path regardless, but leaving this as just Email/Password (not, say, "email link") keeps it simple.
3. **Build → Firestore Database → Create database** → start in **production mode** (the default deny-all rules are fine — this app only ever talks to Firestore through the backend using the Admin SDK, which bypasses client-side security rules entirely, so the database is never reachable directly from a browser).
4. **Project settings (gear icon) → Service accounts → Generate new private key.** This downloads a JSON file — this is your admin credential, treat it like a password. Save it as `server/serviceAccountKey.json` for local development (it's already in `.gitignore`, so it will never be committed).
5. **Project settings → General → Your apps → Add app → Web app.** Copy the `firebaseConfig` object it gives you into `server/static/firebase-config.js` (this file only holds public, safe-to-expose identifiers — it is meant to ship to the browser, unlike the service-account key).

## 2. Put it online as a real website — everything below is done by clicking in a browser, no command line at all

### Step A — Put the code on GitHub (drag-and-drop, no `git` commands)

1. Go to [github.com](https://github.com) and sign in (or create a free account).
2. Click the **+** in the top-right corner → **New repository**. Name it e.g. `sat-sa`, keep it **Private**, click **Create repository**.
3. On the empty repo page, click **uploading an existing file**.
4. Open the unzipped `sat_sa` folder on your computer, select everything inside it, and **drag it into the browser window**. (`serviceAccountKey.json` isn't in this zip at all, so there's nothing to accidentally upload.)
5. Scroll down, click **Commit changes**. Your code is now on GitHub.

### Step B — Deploy it on Render (free)

1. Go to [render.com](https://render.com) → **Get Started** → sign up with your GitHub account (one click, no separate password).
2. Click **New → Web Service**, then pick the `sat-sa` repo you just uploaded.
3. Fill in:
   - **Build Command:** `pip install -r requirements.txt`
   - **Start Command:** `gunicorn --chdir server --bind 0.0.0.0:$PORT app:app`
4. Scroll to **Environment Variables** → **Add Environment Variable**, twice:
   - Key: `FIREBASE_SERVICE_ACCOUNT_JSON` → Value: open your `serviceAccountKey.json` file in Notepad, select all, copy, and paste the whole thing in here.
   - Key: `SETUP_TOKEN` → Value: type any hard-to-guess word or phrase, e.g. `sat-sa-setup-9f3k` (this is a one-time password just for creating your first login — pick your own, don't reuse an existing password).
5. Click **Create Web Service**. Wait a few minutes — Render is installing everything and starting the app. When it says **Live**, it gives you a web address like `https://sat-sa.onrender.com`.

### Step C — Create your login (also just a web page, no command line)

Open, in your browser:
```
https://sat-sa.onrender.com/setup?token=sat-sa-setup-9f3k
```
(use your own web address and your own `SETUP_TOKEN` value from Step B). Fill in your email, name, and a password, click **Create supervisor account**. That's your login.

Do this once for every supervisor who needs access — same link, each person fills in their own details. Nobody who doesn't have this exact link can create an account; there is no public "Sign up" anywhere in the app.

### Step D — Use it

Go to `https://sat-sa.onrender.com` (no `/setup` this time), log in with what you just created. This is the link you give your whole team — anyone can open it in Chrome, on any device, with nothing installed. Your computer does not need to be on.

## Adding an organization — the file format

Inside the app, **"+ Add organization"** → **"Download an example file"**
gives a ready-to-fill template. One row per alert:

| Column | What to put |
|---|---|
| `asset_name` | Whatever the system is called, e.g. "Core Banking Server" |
| `asset_criticality` | Critical / High / Medium / Low (blank → Medium) |
| `severity` | Critical / High / Medium / Low |
| `category` | e.g. Malware, Phishing, Unauthorized Access |
| `disposition` | True Positive / False Positive / Benign |
| `opened_at` | When the alert was raised, e.g. `2026-06-01 14:30` |
| `closed_at` | When the case was closed |
| `escalated` | Yes / No |
| `escalated_at` | Only if escalated = Yes |
| `root_cause_fixed` | Yes / No — was the actual root cause found & fixed? |
| `investigation_notes` | The investigator's actual notes — the tool measures how detailed they are itself |

Only `asset_name`, `severity`, `category`, `disposition`, `opened_at`,
`closed_at`, `escalated`, `root_cause_fixed` are required.

## How the scoring actually works

1. **Rule-based checks (`engine/detectors.py`) — 9 checks for known warning signs**: critical alerts closed in minutes with no escalation; the same problem recurring on one system without ever being fixed; a supposedly-monitored system generating almost no alerts (a blind spot); investigation notes that are suspiciously short and generic. Every check comes with a plain-English reason and the specific alert records behind it.

2. **AI/ML layer (`ml/anomaly_model.py`) — genuine machine learning, not fake numbers.** An **Isolation Forest** (unsupervised — needs no pre-labelled "good/bad" examples) compares each company's overall numbers (alert volume, escalation rate, closure speed, note detail, root-cause rate) against similar companies and flags whichever look statistically unusual *as a combination* — catching patterns the fixed rules didn't think to check for. It retrains automatically every time the data changes; there's no separate manual training step, and no external AI API is ever called — the model runs entirely inside this backend.

3. **Merging the two into one score:** `final_score = 75% × rule_score + 25% × ML_score`, scaled to 0–100 (see `engine/pipeline.py`). Rules get the majority weight because they're fully explainable — a supervisor can verify exactly why each one fired; ML adds coverage for patterns nobody wrote a specific rule for.

4. **Peer comparison:** a company's numbers are only ever compared against other companies in the *same sector* (a bank against banks, a power company against power companies) — never across sectors, since "normal" looks completely different for each.

5. **Execution Gap vs. Negative Space** (the two flag categories shown throughout the app): an *Execution Gap* finding means something clearly happened that shouldn't have (e.g. a critical alert closed in 4 minutes with no follow-up). A *Negative Space* finding means something that should exist is simply missing (e.g. escalation records that never appear despite critical incidents). Both are shown with a one-line plain-English definition wherever they appear.

The main screen shows one score out of 100 and a plain-English list of
reasons why. Every rule ID, evidence row, and raw ML number is still there
for anyone who wants to verify it — the **"Technical Details"** section on
each company's page (collapsed by default, so the main view stays simple
for a non-technical examiner).

## The calendar / assessment history

Every time an organization's data changes, that day's full result is saved
to Firestore under that date. Pick any organization, use the date field
under **"Assessment history,"** and **"Load this date"** — if a saved
assessment exists for that exact date it's shown exactly as it was; if not,
the app says so clearly instead of guessing or showing something else.

To add more alert data to an organization that already exists (instead of
only being able to attach a file when it's first created), open that
organization, pick a date on its calendar, and click **"+ Add report for
this date."** The new file's rows are added on top of everything already
stored for that organization — nothing is overwritten — the whole company is
re-analysed, and the fresh result is saved as that organization's assessment
for the date you picked.

## Honest limitations (good to know before judges ask)

- **This version needs internet access** — Firebase Authentication and
  Firestore are cloud services. This is a real trade-off against a fully
  air-gapped/offline deployment, made deliberately so multiple supervisors
  on different devices can share one login system and one database, per
  the final requirements. If NCIIPC ultimately needs a fully offline,
  on-premise deployment, the same rule engine and ML layer (100% unchanged
  throughout this rework) could be pointed at a local database and local
  auth system instead — the scoring logic itself has no cloud dependency.
- The ML model trains itself fresh from whatever companies exist at the
  time — it needs a handful of companies in the same sector before its
  contribution becomes meaningful. The rule-based checks work regardless of
  how much data there is.
- Each organization is currently added one CSV file at a time; a
  production version would need to accept whatever export format NCIIPC's
  real systems produce directly (JSON, database exports, etc.).

## Security notes

- The Firebase service-account key (`serviceAccountKey.json`) grants full
  admin access to your database and user accounts. It is gitignored and
  must never be committed, emailed, or pasted anywhere outside your own
  Firebase Console / hosting provider's environment-variable settings.
  If a key is ever exposed, go to **Firebase Console → Project settings →
  Service accounts → Manage service account permissions**, delete it, and
  generate a fresh one.
- There is no public sign-up route anywhere in the backend — the only way
  a new supervisor account is created is through the `/setup?token=...`
  page, which only responds at all if the visitor's link has the exact
  `SETUP_TOKEN` you set in Render (any other link, or no token, gets a
  plain "not found" — it never reveals that the page exists). Treat that
  token the same way as a password: only share the full link with people
  you want to be able to create their own supervisor login.
- Every `/api/*` route except the static login page itself requires a
  valid Firebase ID token *and* an active supervisor profile in Firestore
  — a Firebase account that exists but was never explicitly provisioned
  (or was deactivated) is rejected even though it can technically log in
  to Firebase itself.
- **Login does not persist across browser restarts, on purpose.** By
  default Firebase keeps a supervisor signed in indefinitely, even after
  closing and reopening the browser. This app deliberately turns that off
  (`browserSessionPersistence` in `login.js`/`app.js`): a login lasts for as
  long as that browser tab/window stays open, and closing it (or the whole
  browser) always requires logging in again next time.

## Advanced: creating accounts from the command line instead

If you'd rather not use the `/setup` web page, `create_supervisor.py` does
the same thing from a terminal, on a machine that has your
`serviceAccountKey.json`:

```bash
cd server
python3 create_supervisor.py                       # prompts for email, name, password
python3 create_supervisor.py --deactivate a@b.gov.in
python3 create_supervisor.py --reactivate a@b.gov.in
```

This is entirely optional — the website's `/setup` page (Step C above) does
the same job with no command line at all.
