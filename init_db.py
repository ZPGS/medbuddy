import sqlite3

DB = "medbuddy.db"

conn = sqlite3.connect(DB)
c = conn.cursor()

# =================================================
# HELPER
# =================================================
def column_exists(table, column):
    c.execute(f"PRAGMA table_info({table})")
    return column in [row[1] for row in c.fetchall()]

# =================================================
# SLOTS
# =================================================
c.execute("""
CREATE TABLE IF NOT EXISTS slots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    slot_date TEXT NOT NULL,
    start_time TEXT NOT NULL,
    end_time TEXT NOT NULL,
    is_booked INTEGER DEFAULT 0
)
""")

# =================================================
# APPOINTMENTS
# =================================================
c.execute("""
CREATE TABLE IF NOT EXISTS appointments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    confirmation_code TEXT UNIQUE,

    patient_name TEXT NOT NULL,
    mobile TEXT NOT NULL,
    address TEXT NOT NULL,

    slot_id INTEGER NOT NULL,
    appointment_date TEXT NOT NULL,
    slot_time TEXT NOT NULL,

    consultation_type TEXT DEFAULT 'first',
    amount INTEGER NOT NULL DEFAULT 500,
    payment_ref TEXT,

    status TEXT NOT NULL DEFAULT 'RESERVED',
    meeting_link TEXT,
    admin_remarks TEXT,

    reminder_sent INTEGER DEFAULT 0,

    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
)
""")

# =================================================
# MEDICAL REPORTS
# =================================================
c.execute("""
CREATE TABLE IF NOT EXISTS medical_reports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    confirmation_code TEXT NOT NULL,
    appointment_id INTEGER,
    file_name TEXT NOT NULL,
    file_path TEXT NOT NULL,
    uploaded_at TEXT NOT NULL
)
""")

# =================================================
# ADMIN SETTINGS
# =================================================
c.execute("""
CREATE TABLE IF NOT EXISTS admin_settings (
    id INTEGER PRIMARY KEY,
    doctor_whatsapp TEXT,
    upi_link TEXT,

    default_amount INTEGER,
    followup_amount INTEGER,

    default_meeting_link TEXT,

    reservation_message TEXT,
    confirmation_message TEXT,
    reminder_message TEXT
)
""")

# =================================================
# SAFE MIGRATIONS
# =================================================

# ---- appointments ----
if not column_exists("appointments", "consultation_type"):
    c.execute("ALTER TABLE appointments ADD COLUMN consultation_type TEXT DEFAULT 'first'")

# ---- admin_settings ----
if not column_exists("admin_settings", "followup_amount"):
    c.execute("ALTER TABLE admin_settings ADD COLUMN followup_amount INTEGER DEFAULT 300")

if not column_exists("admin_settings", "default_meeting_link"):
    c.execute("ALTER TABLE admin_settings ADD COLUMN default_meeting_link TEXT")

# ---- medical_reports ----
if not column_exists("medical_reports", "appointment_id"):
    c.execute("ALTER TABLE medical_reports ADD COLUMN appointment_id INTEGER")

# =================================================
# DEFAULT MESSAGE TEMPLATES
# =================================================

reservation_message = (
    "Hello {{name}},\n\n"
    "Your appointment has been RESERVED.\n\n"
    "📅 Date: {{date}}\n"
    "⏰ Time: {{time}}\n\n"
    "💰 Consultation Fee: ₹{{amount}}\n\n"
    "Please complete the payment using the UPI link below "
    "and share the payment screenshot or transaction ID.\n\n"
    "🔗 Pay via UPI:\n"
    "upi://pay?pa={{upi}}&am={{amount}}&cu=INR\n\n"
    "UPI ID (manual):\n"
    "{{upi}}\n\n"
    "Thank you,\n"
    "Dr. Shweta Chandrakant Zungare"
)

confirmation_message = (
    "Hello {{name}},\n\n"
    "Your appointment is CONFIRMED.\n\n"
    "Confirmation No: {{code}}\n"
    "📅 Date: {{date}}\n"
    "⏰ Time: {{time}}\n\n"
    "Meeting Link:\n"
    "{{meeting_link}}\n\n"
    "Download Receipt:\n"
    "{{receipt_link}}\n\n"
    "Upload Medical Reports:\n"
    "{{upload_link}}\n\n"
    "Thank you,\n"
    "Dr. Shweta Chandrakant Zungare"
)

reminder_message = (
    "Hello {{name}},\n\n"
    "This is a reminder for your consultation today.\n\n"
    "⏰ Time: {{time}}\n\n"
    "Please join on time.\n\n"
    "Dr. Shweta Chandrakant Zungare"
)

# =================================================
# INSERT DEFAULT SETTINGS (ONLY IF EMPTY)
# =================================================
c.execute("SELECT COUNT(*) FROM admin_settings")
if c.fetchone()[0] == 0:
    c.execute("""
    INSERT INTO admin_settings (
        id,
        doctor_whatsapp,
        upi_link,
        default_amount,
        followup_amount,
        default_meeting_link,
        reservation_message,
        confirmation_message,
        reminder_message
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        1,
        "919588460141",
        "9588460141@ybl",
        500,
        300,
        "",
        reservation_message,
        confirmation_message,
        reminder_message
    ))

conn.commit()
conn.close()

print("✅ Database initialized & migrated successfully")
