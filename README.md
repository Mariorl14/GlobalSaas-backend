# Barber API

Flask REST API for a barbershop management system.

---

## 1. How to run the project locally?

### Prerequisites

- Python 3.x
- PostgreSQL (running locally with a database created)
- (Optional) A virtual environment

### Step-by-step

1. **Clone the repository** and navigate to the project root:
   ```bash
   cd backend
   ```

2. **Create and activate a virtual environment** (recommended):
   ```bash
   # Create
   python -m venv venv

   # Activate (Windows PowerShell)
   .\venv\Scripts\Activate.ps1

   # Activate (Windows CMD)
   venv\Scripts\activate.bat

   # Activate (Linux/macOS)
   source venv/bin/activate
   ```

3. **Install dependencies**:
   ```bash
   pip install -r requirements.txt
   ```

4. **Configure environment variables**:
   - Copy `.env.example` to `.env`
   - Edit `.env` and set your database connection string:
     ```
     DATABASE_URL=postgresql://user:password@localhost:5432/barber_db
     ```

5. **Create the PostgreSQL database** (if not already created):
   ```sql
   CREATE DATABASE barber_db;
   ```

6. **Run migrations** to create the database tables:
   ```bash
   flask db upgrade
   ```
   > See Section 2 for more migration details.

7. **Start the development server**:
   ```bash
   python run.py
   ```
   The API runs at `http://127.0.0.1:5000` (or `http://localhost:5000`).

8. **Verify the API**:
   ```bash
   curl http://localhost:5000/api/health
   # Expected: {"status":"ok"}
   ```

---

## 2. How to create and run migrations?

Migrations are managed with **Flask-Migrate** (Alembic).

### Prerequisites

- Virtual environment activated
- `FLASK_APP` set (see below)
- Database reachable

### Step-by-step

1. **Activate the virtual environment** and set `FLASK_APP`:
   ```bash
   .\venv\Scripts\Activate.ps1   # Windows
   # source venv/bin/activate    # Linux/macOS

   $env:FLASK_APP="run:app"      # Windows PowerShell
   # export FLASK_APP=run:app    # Linux/macOS
   ```

2. **Initialize migrations** (only once per project):
   ```bash
   flask db init
   ```
   This creates the `migrations/` folder.

3. **Create a new migration** after changing models:
   ```bash
   flask db migrate -m "Description of the change"
   ```
   A new file is generated under `migrations/versions/`. Review it before applying.

4. **Apply migrations** to update the database:
   ```bash
   flask db upgrade
   ```

5. **Rollback the last migration** (if needed):
   ```bash
   flask db downgrade
   ```

### Useful commands

| Command                  | Description                            |
|--------------------------|----------------------------------------|
| `flask db current`       | Show current migration revision        |
| `flask db history`       | Show migration history                 |
| `flask db heads`         | Show head revisions                    |

---

## 3. Short explanation of the structure/architecture of the API

### Overview

The API follows a typical **Flask Application Factory** pattern with blueprints, extensions, and SQLAlchemy models.

### Project structure

```
backend/
├── app/
│   ├── __init__.py          # App factory (create_app)
│   ├── extensions.py        # Shared extensions (db, migrate)
│   ├── routes.py            # Blueprint with API endpoints
│   └── models/              # SQLAlchemy models
│       ├── __init__.py
│       ├── business.py
│       ├── plan.py
│       ├── appointment.py
│       └── ...
├── migrations/              # Alembic migrations
├── config.py                # Configuration (env vars)
├── run.py                   # Entry point
├── requirements.txt
└── .env                     # Environment variables (not in git)
```

### Architecture components

- **`run.py`**: Entry point. Instantiates the app via `create_app()` and runs the development server.

- **`config.py`**: Loads configuration from environment variables (e.g. `DATABASE_URL`) via `python-dotenv`.

- **`app/__init__.py`**: Application factory. Creates the Flask app, loads config, initializes extensions (SQLAlchemy, Flask-Migrate, CORS), and registers blueprints.

- **`app/extensions.py`**: Central place for shared extensions (`db`, `migrate`) so they can be initialized once and reused across models and blueprints.

- **`app/routes.py`**: Blueprint defining API endpoints. Currently exposes `/api/health` for health checks.

- **`app/models/`**: SQLAlchemy models representing database tables (Business, Plan, User, Appointment, etc.) with relationships.

- **`migrations/`**: Alembic migration scripts for versioning and applying database schema changes.

### Data flow

1. Request → Flask app → Blueprint route handler
2. Models access data via SQLAlchemy (`db`) bound to the app
3. Database connection uses `DATABASE_URL` from `.env`

---

## 4. WhatsApp appointment confirmations (Twilio)

After a reservation is saved and committed, the API can send an automatic WhatsApp confirmation via Twilio.

### Environment variables

Copy from `.env.example` and set:

| Variable | Description |
|----------|-------------|
| `WHATSAPP_NOTIFICATIONS_ENABLED` | `true` to enable sending |
| `TWILIO_ACCOUNT_SID` | Twilio account SID |
| `TWILIO_AUTH_TOKEN` | Twilio auth token (also used for webhook signature validation) |
| `TWILIO_WHATSAPP_FROM` | Sender, e.g. Sandbox `whatsapp:+14155238886` or production sender |
| `TWILIO_WHATSAPP_CONTENT_SID` | Approved WhatsApp utility template Content SID |
| `DEFAULT_PHONE_COUNTRY_CODE` | ISO alpha-2 fallback when `business.country_code` is unset |
| `TWILIO_REQUEST_TIMEOUT` | HTTP timeout seconds (default `10`) |

When disabled or credentials are missing, appointment creation still succeeds; notifications are logged as `skipped`.

### Twilio Sandbox (local testing)

1. Create a [Twilio account](https://www.twilio.com/) and open **Messaging → Try it out → Send a WhatsApp message**.
2. Note the Sandbox sender (typically `whatsapp:+14155238886`) and set `TWILIO_WHATSAPP_FROM`.
3. From your phone, send the join code shown in the Console to the Sandbox number (e.g. `join <word>`).
4. Create a WhatsApp template in Twilio Content Template Builder (Spanish utility template with 6 variables) and set `TWILIO_WHATSAPP_CONTENT_SID`.
5. Set `WHATSAPP_NOTIFICATIONS_ENABLED=true` and other vars in `.env`.
6. Run migrations: `flask db upgrade`
7. Create a public booking via `POST /api/public/booking/<slug>/bookings` with a phone number that joined the Sandbox.
8. Verify `notification_log` row: status `sent`, `provider_message_sid` populated.

### Production

1. Register a WhatsApp sender in Twilio (business verification).
2. Submit and get approval for a utility template (business-initiated messages require approved templates).
3. Replace `TWILIO_WHATSAPP_FROM` with the approved sender.
4. Set production `TWILIO_WHATSAPP_CONTENT_SID`.
5. Configure Twilio status callback URL: `POST https://<your-api-host>/api/webhooks/twilio/whatsapp-status`

### Automated tests

```bash
pip install -r requirements.txt
pytest
```

Tests mock Twilio and never send real messages.

### Rollback

```bash
flask db downgrade
```

Then remove or disable WhatsApp env vars. Appointment APIs are unchanged if the feature is disabled.
