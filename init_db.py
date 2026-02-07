import sqlite3
from datetime import datetime

DB = "medbuddy.db"

conn = sqlite3.connect(DB)
c = conn.cursor()

# ---------------- SLOTS ----------------
c.execute("""
CREATE TABLE slots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    slot_date TEXT NOT NULL,
    start_time TEXT NOT NULL,
    end_time TEXT NOT NULL,
    is_booked INTEGER DEFAULT 0
)
""")

# ---------------- APPOINTMENTS ----------------
c.execute("""
CREATE TABLE appointments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    confirmation_code TEXT UNIQUE,
    patient_name TEXT NOT NULL,
    mobile TEXT NOT NULL,
    address TEXT NOT NULL,

    slot_id INTEGER NOT NULL,
    appointment_date TEXT NOT NULL,
    slot_time TEXT NOT NULL,

    status TEXT NOT NULL DEFAULT 'RESERVED',
    meeting_link TEXT,
    admin_remarks TEXT,

    reminder_sent INTEGER DEFAULT 0,

    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
)
""")

# ---------------- ADMIN SETTINGS ----------------
c.execute("""
CREATE TABLE admin_settings (
    id INTEGER PRIMARY KEY,
    doctor_whatsapp TEXT,
    upi_link TEXT,
    reservation_message TEXT,
    confirmation_message TEXT,
    reminder_message TEXT
)
""")

# ---------------- DEFAULT SETTINGS ----------------
c.execute("""
INSERT INTO admin_settings (
    id,
    doctor_whatsapp,
    upi_link,
    reservation_message,
    confirmation_message,
    reminder_message
) VALUES (
    1,
    '91XXXXXXXXXX',
    'upi://pay?pa=doctor@upi',
    'Hello {{name}},\n\nYour appointment on {{date}} at {{time}} is RESERVED.\n\nPlease complete payment using the UPI link below and send screenshot or transaction ID.\n\nUPI:\n{{upi}}\n\nThank you.',
    'Hello {{name}},\n\nYour appointment is CONFIRMED.\n\nConfirmation No: {{code}}\nDate: {{date}}\nTime: {{time}}\n\nMeeting Link:\n{{meeting_link}}\n\nDownload Receipt:\n{{receipt_link}}\n\nThank you.',
    'Reminder: Hello {{name}}, your consultation is today at {{time}}. Please join on time.'
)
""")

conn.commit()
conn.close()

print("✅ Fresh database initialized successfully")
