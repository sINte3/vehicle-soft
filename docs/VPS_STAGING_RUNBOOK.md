# VPS_STAGING_RUNBOOK.md — Windows VPS Staging Deployment

Task ID: TASK-DEPLOY-005A  
Created: 2026-05-23  
Author: Claude Code (documentation only — no code, database, or service changes made)

---

## 1. Purpose and Scope

This runbook describes how to deploy Vehicle Soft to a **Windows VPS staging server** cloned from the private GitHub repository `https://github.com/sINte3/vehicle-soft`.

This is a **staging deployment only**. The current office server (`http://10.103.25.200:5050`) remains production until:

- The staging VPS passes the full QA checklist (`docs/QA_CHECKLIST.md`).
- Topaz agent URL is verified on staging.
- The operator explicitly approves production cutover.

**Do NOT point the Topaz agent at staging without coordination. Do NOT destroy or modify the current production server during staging.**

---

## 2. VPS Prerequisites

| Requirement | Minimum | Recommended |
|---|---|---|
| OS | Windows Server 2019 | Windows Server 2022 |
| CPU | 1 vCPU | 2 vCPU |
| RAM | 2 GB | 4 GB |
| Disk | 40 GB | 60 GB |
| Network | Public IPv4 | Static public IPv4 |
| Access | RDP (port 3389) | RDP restricted to office IP |
| Permissions | Administrator account | Administrator account |

Additional requirements:

- Ability to configure Windows Firewall rules (inbound/outbound).
- RDP port 3389 accessible from operator workstation.
- Static IP is strongly recommended — dynamic IP complicates Topaz agent config and DNS.
- Domain name and HTTPS can be added after the app runs on HTTP staging (Phase 3 per `docs/DEPLOYMENT_PLAN.md`).

Suggested VPS providers: Timeweb, Hetzner, Contabo, Reg.ru.

---

## 3. Software Installation Checklist

Complete these steps via RDP before deploying the application.

### 3.1 Git for Windows

Download from https://git-scm.com/download/win  
Install with default options. Ensure "Git from the command line" is selected.

Verify after install (open CMD):

```cmd
git --version
```

Expected output: `git version 2.x.x.windows.x`

### 3.2 Python 3.14

Download Python 3.14 installer from https://www.python.org/downloads/  
Target install path: `C:\Program Files\Python314\`

During installation:

- Check **"Add Python to PATH"**.
- Check **"Install for all users"** (required for NSSM service context).
- Use **"Customize installation"** and set path to `C:\Program Files\Python314\`.

Verify after install (new CMD window):

```cmd
"C:\Program Files\Python314\python.exe" --version
```

Expected output: `Python 3.14.x`

> **Note**: Matching the exact path `C:\Program Files\Python314\python.exe` to production is important. `backup_transport_db.bat` and `update.bat` both hardcode this path.

### 3.3 NSSM (Non-Sucking Service Manager)

Download NSSM from https://nssm.cc/download  
Download the archive and extract `nssm.exe` (64-bit version) to a temporary folder for now.

The file must end up at one of these two locations:

```
C:\transport-report\nssm.exe
C:\nssm\nssm.exe
```

`install_service.bat` looks in both locations.

> **Do not create `C:\transport-report\` manually at this stage.** The repository will be
> cloned into that folder in Section 4 with `git clone`, and `git clone` requires an empty
> target. Copy `nssm.exe` into `C:\transport-report\` only **after** the clone step in
> Section 4 completes. Alternatively, place `nssm.exe` at `C:\nssm\nssm.exe` now and skip
> the post-clone copy.

### 3.4 Optional: Nginx for Windows (for reverse proxy — Phase 2/3)

Download from http://nginx.org/en/download.html (Windows build).  
Unzip to `C:\nginx\`. Configuration covered in Section 9.  
Not required for initial staging smoke test.

### 3.5 Optional: Text Editor

Download Notepad++ from https://notepad-plus-plus.org/ for editing config files and reviewing logs.

### 3.6 Visual C++ Runtime

Python 3.14 may require the Visual C++ 2022 Redistributable.  
Download from https://aka.ms/vs/17/release/vc_redist.x64.exe if Python installation fails.

All six required packages (`flask`, `flask-sqlalchemy`, `flask-login`, `openpyxl`, `waitress`, `werkzeug`) are pure Python or wheel-packaged. No additional native extensions are expected.

---

## 4. GitHub Access and Clone

The repository is private. Git authentication requires a **Personal Access Token (PAT)** with `repo` read access.

### 4.1 Generate a Personal Access Token

On GitHub:

1. Go to **Settings → Developer Settings → Personal Access Tokens → Tokens (classic)**.
2. Click **Generate new token (classic)**.
3. Name: `VPS-deploy-read`.
4. Scopes: check **`repo`** (full control of private repositories).
5. Expiration: 90 days or as appropriate.
6. Click **Generate token** and copy the value immediately.

**Store the token securely. Do not paste it into any documentation or log file.**

### 4.2 Clone the Repository

Open CMD as Administrator on the VPS:

```cmd
git clone https://github.com/sINte3/vehicle-soft.git C:\transport-report
```

When prompted for credentials:

- Username: your GitHub username (e.g., `sINte3`)
- Password: paste the Personal Access Token (not your GitHub account password)

After clone, verify:

```cmd
cd C:\transport-report
git status
git log --oneline -3
```

Expected: `On branch main. Your branch is up to date with 'origin/main'. Nothing to commit.`

### 4.3 Credential Storage (Optional)

To avoid re-entering the token on future `git pull` runs, configure Windows Credential Manager:

```cmd
git config --global credential.helper manager
```

The next `git pull` will store the token securely. Do not use `credential.helper store` (stores token in a plain text file).

---

## 5. Environment Variables

All sensitive values are set as **persistent system-wide variables** using `setx /M`.  
The `/M` flag writes to `HKEY_LOCAL_MACHINE` — required for NSSM service context.

Open CMD as Administrator.

### 5.1 Generate a SECRET_KEY

Run on the VPS:

```cmd
"C:\Program Files\Python314\python.exe" -c "import secrets; print(secrets.token_hex(32))"
```

Copy the printed 64-character hex string. This is your SECRET_KEY value.

### 5.2 Set Required Variables

Replace the placeholder values below with actual secret values:

```cmd
setx SECRET_KEY "REPLACE-WITH-YOUR-GENERATED-SECRET-KEY" /M
setx FUEL_API_TOKEN "REPLACE-WITH-YOUR-TOPAZ-AGENT-TOKEN" /M
```

> **FUEL_API_TOKEN**: must match the token configured in the Topaz agent software on all Topaz-enabled devices. Do not change it mid-deployment without updating the Topaz agent simultaneously.

### 5.3 Optional Variables

For staging, you can leave these at their defaults. Set only if you need to override:

```cmd
setx FLASK_ENV "sqlite_prod" /M
setx PORT "5050" /M
```

For the HOST bind address:

- **Staging with direct firewall-restricted access**: leave HOST unset (defaults to `0.0.0.0` — binds all interfaces; restrict via firewall, see Section 9).
- **Staging behind Nginx reverse proxy**: bind app to loopback only:
  ```cmd
  setx HOST "127.0.0.1" /M
  ```
  If HOST is set to `127.0.0.1`, the app is not reachable directly on port 5050 from outside — only through Nginx.

### 5.4 Verify Variables Are Set

```cmd
reg query "HKEY_LOCAL_MACHINE\SYSTEM\CurrentControlSet\Control\Session Manager\Environment" /v SECRET_KEY
reg query "HKEY_LOCAL_MACHINE\SYSTEM\CurrentControlSet\Control\Session Manager\Environment" /v FUEL_API_TOKEN
```

Both commands must print the variable name and value. If either is missing, run the `setx` command again.

### 5.5 Behavior if Variables Are Missing

| Variable | Missing behavior |
|---|---|
| `SECRET_KEY` | `run_server.py` exits immediately with `ERROR: SECRET_KEY environment variable is not set.` NSSM marks the service as failed. |
| `FUEL_API_TOKEN` | Service starts normally. All Topaz sync requests to `/fuel/api/fuel_sync` return HTTP 401. |

---

## 6. Database Migration to VPS

The production database must be copied to the VPS before starting the service. **Do not run `init_data.py` on the VPS** — it seeds a blank database with only the default admin user, discarding all production data.

### 6.1 On the Current Production Server

Open CMD as Administrator:

```cmd
cd C:\transport-report
backup_transport_db.bat
```

Wait for the output to show:

```
Integrity check : ok
SUCCESS: Backup written to:
         C:\transport-report-backups\daily\transport_YYYYMMDD_HHMMSS.db
Backup completed successfully.
```

Note the exact backup filename (e.g., `transport_20260523_182423.db`).

Verify the backup file:

```cmd
dir C:\transport-report-backups\daily\
```

Confirm the file is present and its size matches the source (expected ~47 MB or larger).

### 6.2 Transfer the Backup to the VPS

Transfer options (choose one):

- **SCP / SFTP**: if OpenSSH server is enabled on the VPS.
- **RDP clipboard / shared drive**: via RDP's drive redirection, drag-and-drop the file.
- **Cloud storage**: upload to OneDrive or Google Drive on the production server, download on VPS.
- **USB / network share**: if both servers are reachable on LAN.

Transfer the file to the VPS at a temporary location, e.g.:

```
C:\temp\transport_YYYYMMDD_HHMMSS.db
```

### 6.3 On the VPS — Prepare the Instance Directory

> **Order matters**: this step runs **after** `git clone` (Section 4) has placed the
> source into `C:\transport-report\`. The `instance\` folder lives inside the cloned
> project; do not create `C:\transport-report\` ahead of the clone.

Open CMD as Administrator on the VPS:

```cmd
if not exist "C:\transport-report\instance" mkdir "C:\transport-report\instance"
```

Copy the verified backup as the production database:

```cmd
copy /Y "C:\temp\transport_YYYYMMDD_HHMMSS.db" "C:\transport-report\instance\transport.db"
```

Confirm: `1 file(s) copied.`

### 6.4 Verify the Database

Run a table count check (read-only):

```cmd
"C:\Program Files\Python314\python.exe" -c "import sqlite3; c=sqlite3.connect('C:/transport-report/instance/transport.db'); rows=list(c.execute(\"SELECT name FROM sqlite_master WHERE type='table' ORDER BY name\")); print('Tables found:', len(rows)); [print(' -', r[0]) for r in rows]; c.close()"
```

Expected output includes tables: `users`, `equipment`, `daily_records`, `engine_hours_records`, `fuel_transactions2`, `schema_migrations`, and others.

Expected minimum table count: 15+

> **Do not use raw copy backups.** The backup must have been created by `backup_transport_db.bat` (which uses the SQLite online backup API with integrity check). Raw copies of `.db` files made while the service is running may be inconsistent.

---

## 7. Python Environment Setup

Open CMD as Administrator in the project directory:

```cmd
cd C:\transport-report
```

### 7.1 Install Dependencies

```cmd
"C:\Program Files\Python314\python.exe" -m pip install -r requirements.txt
```

Expected: all 6 packages install without errors.

```
flask==3.0.0
flask-sqlalchemy==3.1.1
flask-login==0.6.3
openpyxl==3.1.2
waitress==3.0.0
werkzeug==3.0.1
```

If pip is not found or outdated:

```cmd
"C:\Program Files\Python314\python.exe" -m ensurepip --upgrade
"C:\Program Files\Python314\python.exe" -m pip install --upgrade pip
"C:\Program Files\Python314\python.exe" -m pip install -r requirements.txt
```

### 7.2 Syntax Check All Main Modules

```cmd
"C:\Program Files\Python314\python.exe" -m py_compile app.py models.py config.py run_server.py fuel_routes.py spare_parts.py wialon_import.py workload_report.py translations.py backup_transport_db.py
```

No output means all files pass. Any error message indicates a file failed — do not continue until resolved.

### 7.3 Application Import Check

```cmd
"C:\Program Files\Python314\python.exe" -c "from app import app; print('APP IMPORT OK')"
```

Must print `APP IMPORT OK`. If it prints an error, read the traceback — most common causes are missing environment variables or missing dependencies.

---

## 8. NSSM Service Setup

### 8.1 Use `install_service.bat` (Recommended Path)

`install_service.bat` handles all NSSM commands automatically: it installs the service, sets the working directory, configures logging, and starts the service.

**Before running it**, verify prerequisites (in deployment order):

- Repository cloned into `C:\transport-report\` (Section 4).
- `nssm.exe` is in `C:\transport-report\` or `C:\nssm\` (Section 3.3 — placed after clone).
- Python 3.14 is installed at `C:\Program Files\Python314\python.exe`.
- `SECRET_KEY` is set via `setx /M` (Section 5).
- `FUEL_API_TOKEN` is set via `setx /M` (Section 5).
- `C:\transport-report\instance\transport.db` exists (database copied in Section 6).
- Dependencies installed and syntax/import checks pass (Section 7).
- You are running CMD as Administrator.

Run the script:

```cmd
cd C:\transport-report
install_service.bat
```

The script will:

1. Find Python in PATH and NSSM in known locations.
2. Run `pip install -r requirements.txt`.
3. Skip `init_data.py` because `instance\transport.db` already exists.
4. Create the `logs\` folder.
5. Remove any old `TransportReport` service.
6. Install and configure the NSSM service.
7. Start the service.

### 8.2 NSSM Service Parameters (What install_service.bat Sets)

| NSSM Parameter | Value |
|---|---|
| Service name | `TransportReport` |
| Executable | Python path found via `where python` (should be Python 3.14) |
| Arguments | `run_server.py` |
| AppDirectory (working dir) | `C:\transport-report\` |
| AppStdout | `C:\transport-report\logs\service.log` |
| AppStderr | `C:\transport-report\logs\error.log` |
| AppRotateFiles | 1 (rotate at 1 MB) |
| Start type | `SERVICE_AUTO_START` |
| AppEnvironmentExtra | `FLASK_ENV=sqlite_prod`, `PORT=5050` |

> **Note**: `install_service.bat` does NOT set `SECRET_KEY` or `FUEL_API_TOKEN` in the NSSM environment — those are read from the system environment (set via `setx /M` in Section 5). Do not add secrets to NSSM AppEnvironmentExtra; they would appear in logs and in registry keys with broad read access.

### 8.3 Manual NSSM Commands (If install_service.bat Is Not Used)

If you need to configure NSSM manually (e.g., troubleshooting):

```cmd
cd C:\transport-report

:: Install the service
nssm.exe install TransportReport "C:\Program Files\Python314\python.exe" "run_server.py"
nssm.exe set TransportReport AppDirectory "C:\transport-report"
nssm.exe set TransportReport DisplayName "Bukhoro Agrocluster - Transport Report"
nssm.exe set TransportReport Description "Daily transport work reporting system"
nssm.exe set TransportReport Start SERVICE_AUTO_START
nssm.exe set TransportReport AppEnvironmentExtra "FLASK_ENV=sqlite_prod" "PORT=5050"

:: Logging
nssm.exe set TransportReport AppStdout "C:\transport-report\logs\service.log"
nssm.exe set TransportReport AppStderr "C:\transport-report\logs\error.log"
nssm.exe set TransportReport AppRotateFiles 1
nssm.exe set TransportReport AppRotateBytes 1048576

:: Start
nssm.exe start TransportReport
```

To set HOST=127.0.0.1 for use behind Nginx, run this AFTER install_service.bat completes:

```cmd
nssm.exe set TransportReport AppEnvironmentExtra "FLASK_ENV=sqlite_prod" "PORT=5050" "HOST=127.0.0.1"
nssm.exe restart TransportReport
```

### 8.4 Verify the Service Started

```cmd
sc query TransportReport
```

Expected `STATE: 4  RUNNING`.

Check the startup log:

```cmd
type C:\transport-report\logs\service.log
```

Expected output includes:

```
  Bukhoro Agrocluster - Transport Report
  Server: http://0.0.0.0:5050
  Mode:   SQLite
```

If the log shows `ERROR: SECRET_KEY environment variable is not set.`, the variable was not set correctly. Re-run `setx SECRET_KEY "..." /M` and restart the service.

### 8.5 Change the Admin Password Immediately

The initial database copied from production already has real users and passwords. However, if this is a fresh database seeded by `init_data.py` (scenario: you chose to start fresh), the default admin password is `admin123` and must be changed immediately.

1. Open the app in a browser.
2. Log in with `admin` / `admin123`.
3. Navigate to Admin → Change Password.
4. Set a strong password (minimum 12 characters).

---

## 9. Firewall Staging Plan

### 9.1 Immediate Firewall Rules (During Staging)

Configure Windows Firewall via CMD as Administrator:

```cmd
:: Allow RDP only from office IP (replace 203.0.113.10 with your actual office public IP)
netsh advfirewall firewall add rule name="RDP-Office-Only" protocol=TCP dir=in localport=3389 action=allow remoteip=203.0.113.10

:: Block RDP from all other sources
netsh advfirewall firewall add rule name="RDP-Block-Others" protocol=TCP dir=in localport=3389 action=block

:: Allow port 5050 ONLY from trusted operator IPs for staging test
:: (replace 203.0.113.10 with actual office public IP)
netsh advfirewall firewall add rule name="TransportApp-Staging" protocol=TCP dir=in localport=5050 action=allow remoteip=203.0.113.10

:: Block port 5050 from all other sources
netsh advfirewall firewall add rule name="TransportApp-Block-Public" protocol=TCP dir=in localport=5050 action=block
```

> Do NOT expose port 5050 broadly to the internet without firewall rules. The application is HTTP-only at this stage and has no brute-force protection on login.

### 9.2 After Nginx Is Configured (Later Phase)

When Nginx is installed and the app is behind a reverse proxy:

```cmd
:: Allow HTTP and HTTPS from everyone
netsh advfirewall firewall add rule name="HTTP-Public" protocol=TCP dir=in localport=80 action=allow
netsh advfirewall firewall add rule name="HTTPS-Public" protocol=TCP dir=in localport=443 action=allow

:: Block direct access to port 5050 from outside (loopback only for Nginx)
netsh advfirewall firewall add rule name="TransportApp-Block-Direct" protocol=TCP dir=in localport=5050 action=block
```

At this point, update NSSM to bind app to `127.0.0.1` only:

```cmd
nssm.exe set TransportReport AppEnvironmentExtra "FLASK_ENV=sqlite_prod" "PORT=5050" "HOST=127.0.0.1"
nssm.exe restart TransportReport
```

### 9.3 Nginx Reverse Proxy Configuration (Skeleton)

Save to `C:\nginx\conf\nginx.conf`:

```nginx
events { worker_connections 1024; }

http {
    server {
        listen 80;
        server_name your-vps-ip-or-domain;

        location / {
            proxy_pass http://127.0.0.1:5050;
            proxy_set_header Host $host;
            proxy_set_header X-Real-IP $remote_addr;
            proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
            proxy_set_header X-Forwarded-Proto $scheme;
        }
    }
}
```

Replace `your-vps-ip-or-domain` with the actual VPS IP or domain name.  
HTTPS/TLS configuration to be added in Phase 3 (after domain is registered).

---

## 10. Backup Setup on VPS

Replicate the same automated backup setup that is verified on production.

### 10.1 Run a Manual Backup First

```cmd
cd C:\transport-report
backup_transport_db.bat
```

Expected output:

```
============================================================
 Transport DB Daily Backup
============================================================

============================================================
 Transport DB Backup  (SQLite online backup API)
============================================================
 Source : C:\transport-report\instance\transport.db
 Dest   : C:\transport-report-backups\daily\transport_YYYYMMDD_HHMMSS.db

 Source size : 46,800,896 bytes

Running SQLite online backup...
 Dest size   : 46,800,896 bytes

Running integrity check on destination database...
 Integrity check : ok

SUCCESS: Backup written to:
         C:\transport-report-backups\daily\transport_YYYYMMDD_HHMMSS.db
Backup completed successfully.
```

If it fails with `ERROR: Source database not found`, the database was not placed correctly in Section 6.

### 10.2 Create the Daily Backup Task

Open CMD as Administrator:

```cmd
schtasks /create /tn "TransportDBBackup" /tr "C:\transport-report\backup_transport_db.bat" /sc daily /st 02:00 /ru SYSTEM /f
```

Verify the task was created:

```cmd
schtasks /query /tn "TransportDBBackup" /fo LIST
```

Expected: `Status: Ready`, `Next Run Time: <tomorrow> 2:00:00 AM`.

### 10.3 Test the Scheduled Task Immediately

```cmd
schtasks /run /tn "TransportDBBackup"
```

Wait 10–15 seconds, then verify a new backup file appeared:

```cmd
dir C:\transport-report-backups\daily\
```

Two files should be present (the manual test from Step 10.1 and this scheduled task test).

---

## 11. QA Checklist

Use `docs/QA_CHECKLIST.md` as the primary reference for full testing.

### 11.1 Minimum Smoke Tests for Staging Sign-Off

Open the staging URL in a browser (e.g., `http://VPS-IP:5050` or `http://VPS-IP` if Nginx is configured).

| Test | Path | Expected Result |
|---|---|---|
| Login page loads | `/login` | Login form appears |
| Login succeeds | POST `/login` | Dashboard loads, user name shown |
| Daily entry page | `/entry` | All 9 equipment category sections visible |
| Report generation | `/report` | Report form loads; generate 1-day Excel report; file downloads |
| Wialon import | `/wialon` | Import page loads; upload a real ZIP to test |
| Workload report | `/wialon/workload` | Report loads with norm/fact columns |
| Fuel dashboard | `/fuel/` | Fuel stats visible |
| Equipment reference | `/ref/equipment` | Equipment list visible |
| Admin users | `/admin/users` | User list visible (admin account only) |
| Language switch UZ/RU | nav button | All labels change on current page |
| Excel report download | `/report` | File opens in Excel without errors |
| Backup task | Task Scheduler | `TransportDBBackup` state is Ready |
| Log check | `logs\error.log` | No Python exceptions since startup |

### 11.2 Wialon-Specific Tests

- Upload a real Wialon ZIP file with `Моточасы.csv`.
- Verify mapped vehicles appear in `engine_hours_records`.
- Test period filter (daily / week / month).

### 11.3 Authorization Tests

- Non-admin user cannot access `/admin/users` (should get 403 or redirect).
- User without fuel module permission cannot access `/fuel/` (403).
- Admin can access all modules.

---

## 12. Topaz / Wialon Staging Policy

### 12.1 Topaz Fuel Sync

**Do NOT update the Topaz agent to point to the staging VPS until the operator explicitly confirms staging QA is complete.**

Pointing Topaz at staging while production is still active will:

- Cause production fuel records to be written to the staging database instead.
- Stop fuel sync on the production server.

**Staging-safe test approach**:

1. Test the ping endpoint only (no data written):
   ```
   GET http://VPS-IP:5050/fuel/api/fuel_ping
   ```
   Expected response: `{"status": "ok"}`

2. If a real sync test is required, coordinate a short window with the operator:
   - Temporarily update one Topaz agent.
   - Verify sync in `logs\service.log` on the VPS.
   - Immediately revert the agent to the production URL.
   - The production server has not changed; no data is lost.

3. Verify the token is set correctly:
   ```cmd
   reg query "HKEY_LOCAL_MACHINE\SYSTEM\CurrentControlSet\Control\Session Manager\Environment" /v FUEL_API_TOKEN
   ```

### 12.2 Wialon Import

Wialon import is a browser-based ZIP file upload (`/wialon/upload`). No external agent changes are required. Operators simply upload files to the new staging URL. This is safe to test at any time.

---

## 13. Cutover Plan Draft

Production cutover happens **only after staging QA passes and the operator approves**.

**Steps for final cutover (future — do not execute during staging)**:

1. Keep the current office server running and accessible throughout.
2. Announce planned maintenance window to all users.
3. Stop all active user sessions (notify users to log out).
4. On production server: run `backup_transport_db.bat` one final time.
5. Transfer the final backup to the VPS (Section 6 procedure).
6. Stop the staging VPS service.
7. Replace staging `instance\transport.db` with the final backup.
8. Start the staging VPS service — it becomes the new production server.
9. Set final DNS A record to point domain to VPS IP.
10. If applicable, configure HTTPS (Phase 3 per `docs/DEPLOYMENT_PLAN.md`).
11. Update Topaz agent URL from `http://10.103.25.200:5050/fuel/api/fuel_sync` to new URL.
12. Verify Topaz sync arrives in VPS `logs\service.log`.
13. Monitor `logs\error.log` for 24 hours.
14. Keep the old office server powered on and accessible (read-only standby) for at least 5 business days.
15. After 5 days of stable VPS operation, the office server can be powered down.

---

## 14. Rollback Plan

### If Staging VPS Fails at Any Point

- The production office server is untouched.
- Simply stop using the staging VPS.
- No user data was in the staging DB unless a Topaz test window was opened — in that case, restore from the last production backup taken before the Topaz test.

### If Cutover Fails After DNS Switch

1. Revert DNS A record to office server IP (takes up to TTL minutes to propagate).
2. Revert Topaz agent URL back to `http://10.103.25.200:5050/fuel/api/fuel_sync`.
3. Restart Topaz agent.
4. Notify users of the temporary URL.
5. Start the office server service if it was stopped:
   ```cmd
   cd C:\transport-report
   .\nssm.exe start TransportReport
   ```
6. The office server's database is the authoritative backup until VPS is stable.

---

## 15. Open Questions for Operator

Before or during VPS setup, the following decisions are needed:

| # | Question | Notes |
|---|---|---|
| 1 | Which VPS provider? | Timeweb, Hetzner, Contabo, or other? Affects billing currency, latency, and support language. |
| 2 | Do we have a domain or subdomain? | e.g., `transport.agrocluster.uz` — needed for Phase 3 HTTPS setup. |
| 3 | Will access be public HTTPS or VPN/IP-allowlisted? | IP-allowlist is simpler; public HTTPS requires domain registration + Nginx TLS. |
| 4 | What is the static office IP (public)? | Needed to allowlist RDP and port 5050 in Windows Firewall. |
| 5 | Who will update the Topaz agent configuration on all devices? | Must be coordinated during Topaz test window. |
| 6 | Windows VPS first, or skip directly to Linux VPS? | Linux is cheaper and has free Let's Encrypt TLS; requires systemd instead of NSSM. See `docs/DEPLOYMENT_PLAN.md` Phase 3–4. |
| 7 | How many days before cutover is staging expected to run? | Affects whether to set up full backup rotation on staging. |
| 8 | Is the FUEL_API_TOKEN value the same on staging and production? | It should be — the token must match whatever the Topaz agent sends. Coordinate with whoever manages Topaz config. |

---

## 16. Exact Operator Command Checklist

Execute in this exact order. Run CMD as Administrator for all steps.

```
STEP  COMMAND / ACTION
----  ---------------------------------------------------------------
 1.   [VPS PROVIDER] Rent Windows Server 2022 VPS. Note the public IP.

 2.   [RDP] Connect via Remote Desktop.

 3.   [INSTALL GIT] Download and install Git for Windows.
      Verify: git --version

 4.   [INSTALL PYTHON 3.14] Download and install with "Add to PATH"
      and "Install for all users". Target: C:\Program Files\Python314\
      Verify: "C:\Program Files\Python314\python.exe" --version

 5.   [CLONE REPO — into an empty C:\transport-report]
      Do NOT pre-create C:\transport-report. git clone needs an empty target.
      git clone https://github.com/sINte3/vehicle-soft.git C:\transport-report
      (Enter GitHub username and Personal Access Token when prompted)
      Verify: cd C:\transport-report && git status && git log --oneline -3

 6.   [PLACE NSSM] Copy nssm.exe into the cloned project folder:
      copy /Y "C:\path-to-extracted\nssm.exe" "C:\transport-report\nssm.exe"
      (Or place it at C:\nssm\nssm.exe — install_service.bat checks both.)

 7.   [SET ENV VARS — SECRET_KEY]
      "C:\Program Files\Python314\python.exe" -c "import secrets; print(secrets.token_hex(32))"
      Copy output, then:
      setx SECRET_KEY "PASTE-GENERATED-VALUE-HERE" /M

 8.   [SET ENV VARS — FUEL_API_TOKEN]
      setx FUEL_API_TOKEN "PASTE-YOUR-TOPAZ-TOKEN-HERE" /M

      [VERIFY ENV VARS]
      reg query "HKEY_LOCAL_MACHINE\SYSTEM\CurrentControlSet\Control\Session Manager\Environment" /v SECRET_KEY
      reg query "HKEY_LOCAL_MACHINE\SYSTEM\CurrentControlSet\Control\Session Manager\Environment" /v FUEL_API_TOKEN

 9.   [CONFIGURE FIREWALL — RDP]
      netsh advfirewall firewall add rule name="RDP-Office-Only" protocol=TCP dir=in localport=3389 action=allow remoteip=YOUR-OFFICE-PUBLIC-IP
      netsh advfirewall firewall add rule name="RDP-Block-Others" protocol=TCP dir=in localport=3389 action=block

      [CONFIGURE FIREWALL — APP PORT]
      netsh advfirewall firewall add rule name="TransportApp-Staging" protocol=TCP dir=in localport=5050 action=allow remoteip=YOUR-OFFICE-PUBLIC-IP
      netsh advfirewall firewall add rule name="TransportApp-Block-Public" protocol=TCP dir=in localport=5050 action=block

10.   [BACKUP PRODUCTION DB — on production server]
      cd C:\transport-report
      backup_transport_db.bat
      Note the backup filename: transport_YYYYMMDD_HHMMSS.db

11.   [TRANSFER DB] Copy backup file from production to VPS.
      (RDP drive share / SCP / cloud storage)
      Place at: C:\temp\transport_YYYYMMDD_HHMMSS.db

12.   [PREPARE INSTANCE DIR — on VPS, inside the cloned folder]
      if not exist "C:\transport-report\instance" mkdir "C:\transport-report\instance"

13.   [COPY DB INTO INSTANCE]
      copy /Y "C:\temp\transport_YYYYMMDD_HHMMSS.db" "C:\transport-report\instance\transport.db"

14.   [VERIFY DB]
      "C:\Program Files\Python314\python.exe" -c "import sqlite3; c=sqlite3.connect('C:/transport-report/instance/transport.db'); rows=list(c.execute(\"SELECT name FROM sqlite_master WHERE type='table' ORDER BY name\")); print('Tables:', len(rows)); [print(' -', r[0]) for r in rows]; c.close()"

15.   [INSTALL DEPENDENCIES]
      cd C:\transport-report
      "C:\Program Files\Python314\python.exe" -m pip install -r requirements.txt

16.   [SYNTAX CHECK]
      "C:\Program Files\Python314\python.exe" -m py_compile app.py models.py config.py run_server.py fuel_routes.py spare_parts.py wialon_import.py workload_report.py translations.py backup_transport_db.py
      (No output = all pass)

17.   [IMPORT CHECK]
      "C:\Program Files\Python314\python.exe" -c "from app import app; print('APP IMPORT OK')"
      Must print: APP IMPORT OK

18.   [INSTALL AND START SERVICE]
      cd C:\transport-report
      install_service.bat
      (Confirm service starts. Check output for errors.)

19.   [VERIFY SERVICE RUNNING]
      sc query TransportReport
      type C:\transport-report\logs\service.log

20.   [TEST APP IN BROWSER]
      Open: http://VPS-PUBLIC-IP:5050
      Login, check dashboard, open /entry, /report, /fuel/

21.   [SETUP BACKUP TASK]
      backup_transport_db.bat
      schtasks /create /tn "TransportDBBackup" /tr "C:\transport-report\backup_transport_db.bat" /sc daily /st 02:00 /ru SYSTEM /f
      schtasks /run /tn "TransportDBBackup"
      dir C:\transport-report-backups\daily\

22.   [FULL QA] Run docs\QA_CHECKLIST.md smoke tests.

23.   [TOPAZ PING TEST — staging safe]
      Open in browser: http://VPS-PUBLIC-IP:5050/fuel/api/fuel_ping
      Expected: {"status": "ok"}

24.   [OPTIONAL — Nginx install] See Section 9.3 for reverse proxy config.

25.   [SIGN OFF] Report staging QA results. Do not proceed to cutover until
      the operator approves.
```

> **Primary path**: clone into an empty `C:\transport-report`, then drop `nssm.exe` into
> it and create `instance\` inside it. Set env vars, firewall rules, transfer the DB, then
> run `install_service.bat`. This avoids the failure mode where `git clone` refuses to
> write into a non-empty target.

### Alternative if `C:\transport-report` already exists

If `C:\transport-report\` was created by an earlier manual step (or by a previous attempt)
and is not empty, do **not** clone into it. Pick one of these:

- **Recommended**: rename or move the existing folder out of the way (e.g.,
  `rename C:\transport-report transport-report.bak`), then run the primary `git clone`
  command from Step 5 into a fresh empty `C:\transport-report`.
- **Only if you understand the implications**: initialise the existing folder as a Git
  working tree and pull `main` over the existing files. This is fragile (it will leave
  any untracked files in place and may conflict with tracked files) and should only be
  used when the existing contents are known-safe:
  ```cmd
  cd C:\transport-report
  git init
  git remote add origin https://github.com/sINte3/vehicle-soft.git
  git fetch origin main
  git checkout -B main origin/main
  ```

For the first VPS deployment, always prefer starting with an empty folder.

---

## Service Management Quick Reference

```cmd
:: Start service
cd C:\transport-report
.\nssm.exe start TransportReport

:: Stop service
.\nssm.exe stop TransportReport

:: Restart service
.\nssm.exe restart TransportReport

:: Check status
sc query TransportReport

:: View startup log
type C:\transport-report\logs\service.log

:: View error log
type C:\transport-report\logs\error.log

:: If nssm.exe is not in folder, use:
net start TransportReport
net stop TransportReport
```

---

## References

| Document | Purpose |
|---|---|
| `docs/DEPLOYMENT_PLAN.md` | Full deployment phases, hosting comparison, VPS task scope |
| `docs/DEPLOYMENT_SECURITY.md` | SECRET_KEY and FUEL_API_TOKEN setup and rollback |
| `docs/RELEASE_AND_BACKUP_PROCEDURE.md` | Update, backup, and rollback procedures |
| `docs/MIGRATIONS.md` | How to run migration scripts safely |
| `docs/QA_CHECKLIST.md` | Full QA test procedures |
| `.env.example` | Template for environment variable values |
| `install_service.bat` | NSSM service installer |
| `backup_transport_db.bat` | Daily backup wrapper |
| `update.bat` | Production update helper (git pull + service restart) |
