# Deploying the RFS Stress Test Web App

End-to-end walkthrough to get this Flask app live on the internet with
**submission logging to Google Sheets** and **email notifications to the
admin** every time someone runs the stress test.

Total setup time: ~45 minutes if you don't have any of the accounts yet,
~15 minutes if you do.

---

## 0. What you'll end up with

- A public URL (e.g. `https://rfs-stress-test.onrender.com`) you can share.
- Every submission appends a row to a Google Sheet you own — inputs and headline
  KPIs. You can browse, filter, and chart them later.
- Every submission also emails you with the user's uploaded CSV and the
  generated KPI results attached.

If you ever want to take it down, deleting the Render service is one click.

---

## 1. Prerequisites — accounts you'll need

| Service | Why | Free? |
|---|---|---|
| [GitHub](https://github.com/signup) | Source code hosting; Render deploys from it | Yes |
| [Render](https://render.com/) | Hosts the Flask app | Yes (free tier sleeps after 15 min idle) |
| Gmail account | Sends the notification emails | Yes |
| [Google Cloud](https://console.cloud.google.com/) | Service account for Sheets API | Yes |

Create accounts now if you don't have them.

---

## 2. Push the code to GitHub

From the project directory:

```bash
git init
git add .
git commit -m "Initial commit — RFS stress test web app"

# Create a new private repo at https://github.com/new (name it whatever).
# Then connect your local repo to it (GitHub shows you the exact commands).
git branch -M main
git remote add origin https://github.com/<your-username>/<repo-name>.git
git push -u origin main
```

**Sanity check** — open the repo on GitHub. Make sure `.env` is NOT in the file
list (your `.gitignore` should keep it out). If you see it, stop and remove it
from history before going further.

---

## 3. Create a Gmail App Password

1. Visit <https://myaccount.google.com/security>.
2. Turn on **2-Step Verification** if it isn't already.
3. Visit <https://myaccount.google.com/apppasswords>.
4. Pick "Mail" and "Other (Custom name)", call it "RFS Stress Test".
5. Google gives you a 16-character password like `abcd efgh ijkl mnop`. **Copy
   it — you only see it once.** Strip the spaces when you paste it later.

This is what goes in `SMTP_PASSWORD`. Do NOT use your regular Gmail password.

---

## 4. Create the Google Sheet + Service Account

### 4a. Make the Sheet

1. Go to <https://docs.google.com> and create a new blank spreadsheet.
2. Rename it something like "RFS Submissions".
3. From the URL, copy the long ID between `/d/` and `/edit`:
   `https://docs.google.com/spreadsheets/d/`**`1AbCdEfGhIj...XYZ`**`/edit`
   Save that ID — it's your `GSHEETS_SHEET_ID`.

### 4b. Make the Service Account

1. Go to <https://console.cloud.google.com/projectcreate> and create a new
   project (name: "RFS Stress Test").
2. Once it loads, go to <https://console.cloud.google.com/apis/library> and
   enable both:
   - **Google Sheets API**
   - **Google Drive API**
3. Go to <https://console.cloud.google.com/iam-admin/serviceaccounts>.
4. Click **+ Create Service Account**. Name: "rfs-stress-test-bot". Skip the
   optional steps and click Done.
5. Click into the new service account. Go to the **Keys** tab. Click
   **Add Key → Create new key → JSON**. A `.json` file downloads — open it in
   a text editor.
6. Find the `client_email` field — it looks like
   `rfs-stress-test-bot@your-project.iam.gserviceaccount.com`. Copy this email.

### 4c. Share the Sheet with the Service Account

1. Back in your Google Sheet, click **Share** (top-right).
2. Paste the service account email.
3. Give it **Editor** access. Uncheck "Notify people". Click **Send**.

### 4d. Prepare the JSON for the env var

The downloaded `.json` file has multiple lines. You need it as a **single line**
to paste into Render. The easiest way:

```bash
# In your terminal — copies the single-line JSON to your clipboard (macOS)
cat ~/Downloads/your-project-abc123.json | python3 -c "import sys,json; print(json.dumps(json.load(sys.stdin)))" | pbcopy
```

What's on your clipboard is what you'll paste as `GSHEETS_SERVICE_ACCOUNT_JSON`
in step 5.

---

## 5. Deploy to Render

1. Go to <https://dashboard.render.com/> and sign in.
2. Click **+ New → Web Service**.
3. Connect your GitHub account when prompted and pick the repo you just pushed.
4. Fill in the form:
   - **Name:** anything, e.g. `rfs-stress-test`. This becomes part of your URL.
   - **Region:** pick the one closest to you.
   - **Branch:** `main`.
   - **Runtime:** Python 3.
   - **Build Command:** `pip install -r requirements.txt`
   - **Start Command:** `gunicorn app:app --bind 0.0.0.0:$PORT --workers 2 --timeout 120`
   - **Plan:** Free.
5. Scroll to **Environment Variables**. Click **Add Environment Variable** for
   each line below (use the values you collected in steps 3 and 4):

| Key | Value |
|---|---|
| `FLASK_SECRET` | (random string — `python3 -c "import secrets; print(secrets.token_urlsafe(32))"`) |
| `ADMIN_EMAIL` | the email address that should receive notifications |
| `SMTP_USER` | your Gmail address |
| `SMTP_PASSWORD` | the 16-char Gmail app password from step 3 (no spaces) |
| `GSHEETS_SHEET_ID` | the Sheet ID from step 4a |
| `GSHEETS_SERVICE_ACCOUNT_JSON` | the single-line JSON from step 4d |

6. Click **Create Web Service**. Render builds and starts the app — takes ~3
   minutes. When it goes green, click the URL at the top of the page.

---

## 6. Smoke test

1. Open your live URL.
2. Fill in property name, your name, your email.
3. Upload `static/reserve_study_template.csv` (or your own).
4. Click **Run Stress Test**.
5. Within ~15 seconds you should:
   - See the results dashboard.
   - Receive an email at `ADMIN_EMAIL` with the user CSV + KPI files attached.
   - See a new row in your Google Sheet.

If any of those don't happen, check Render's **Logs** tab — both helpers print
the reason they failed (e.g. `[email] send failed: ...` or
`[sheets] log failed: ...`).

---

## 7. Free-tier caveat

Render's free plan sleeps after 15 minutes of inactivity. The first request
after a sleep takes ~30 seconds to wake the app. If that matters for your
audience, upgrade to the $7/month "Starter" plan to keep it warm.

---

## 8. Sharing

Just send the Render URL to whoever you want to run a stress test. Anyone with
the link can use it — no login required. If you ever need to lock it down,
ask me to wire in a single shared password.
