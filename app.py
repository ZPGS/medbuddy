from flask import (
    Flask, render_template, request,
    redirect, session, flash, jsonify, send_file
)
import sqlite3, io
from datetime import datetime
from apscheduler.schedulers.background import BackgroundScheduler
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4

from scheduler import auto_expire_reserved, send_reminders

app = Flask(__name__)
app.secret_key = "medbuddy-secret"

DB = "medbuddy.db"

# ---------------- DB HELPER ----------------
def db():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    return conn

# ---------------- CONFIRMATION CODE ----------------
def generate_code(conn):
    count = conn.execute(
        "SELECT COUNT(*) FROM appointments"
    ).fetchone()[0] + 1
    return f"MB-{datetime.now().strftime('%Y%m%d')}-{str(count).zfill(4)}"

# ---------------- SCHEDULER ----------------
scheduler = BackgroundScheduler()
scheduler.add_job(auto_expire_reserved, "interval", minutes=10)
scheduler.add_job(send_reminders, "interval", minutes=5)
scheduler.start()

# =================================================
# PUBLIC / LANDING
# =================================================
@app.route("/")
def home():
    return render_template("index.html")

@app.route("/patient")
def patient_page():
    return render_template("patient.html")

# =================================================
# PATIENT
# =================================================
@app.route("/slots")
def available_slots():
    conn = db()
    rows = conn.execute(
        "SELECT * FROM slots WHERE is_booked=0 ORDER BY slot_date,start_time"
    ).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])

@app.route("/book", methods=["POST"])
def book():
    f = request.form
    conn = db()

    slot = conn.execute(
        "SELECT * FROM slots WHERE id=? AND is_booked=0",
        (f["slot_id"],)
    ).fetchone()

    if not slot:
        flash("Slot not available", "error")
        return redirect("/patient")

    code = generate_code(conn)
    now = datetime.now().isoformat()

    conn.execute("""
        INSERT INTO appointments (
            confirmation_code, patient_name, mobile, address,
            slot_id, appointment_date, slot_time,
            status, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, 'RESERVED', ?, ?)
    """, (
        code,
        f["patient_name"],
        f["mobile"],
        f["address"],
        slot["id"],
        slot["slot_date"],
        f'{slot["start_time"]}-{slot["end_time"]}',
        now,
        now
    ))

    conn.execute(
        "UPDATE slots SET is_booked=1 WHERE id=?",
        (slot["id"],)
    )

    conn.commit()
    conn.close()

    flash(
        "Spot is reserved. You will receive a WhatsApp message shortly for payment.",
        "info"
    )
    return redirect("/patient")

# =================================================
# STATUS / HISTORY / CANCEL
# =================================================
@app.route("/status", methods=["GET", "POST"])
def status():
    appt = None
    if request.method == "POST":
        conn = db()
        appt = conn.execute(
            "SELECT * FROM appointments WHERE confirmation_code=?",
            (request.form["confirmation_code"],)
        ).fetchone()
        conn.close()
    return render_template("status.html", appointment=appt)

@app.route("/history", methods=["GET", "POST"])
def history():
    rows = None
    if request.method == "POST":
        conn = db()
        rows = conn.execute(
            "SELECT * FROM appointments WHERE mobile=? ORDER BY created_at DESC",
            (request.form["mobile"],)
        ).fetchall()
        conn.close()
    return render_template("history.html", appointments=rows)

@app.route("/cancel/<code>", methods=["POST"])
def cancel(code):
    conn = db()
    appt = conn.execute(
        "SELECT slot_id,status FROM appointments WHERE confirmation_code=?",
        (code,)
    ).fetchone()

    if appt and appt["status"] == "RESERVED":
        conn.execute(
            "UPDATE appointments SET status='CANCELLED' WHERE confirmation_code=?",
            (code,)
        )
        conn.execute(
            "UPDATE slots SET is_booked=0 WHERE id=?",
            (appt["slot_id"],)
        )
        conn.commit()

    conn.close()
    return redirect("/history")

# =================================================
# PDF
# =================================================
@app.route("/appointment/pdf/<code>")
def appointment_pdf(code):
    conn = db()
    a = conn.execute(
        "SELECT * FROM appointments WHERE confirmation_code=?",
        (code,)
    ).fetchone()
    conn.close()

    buf = io.BytesIO()
    pdf = canvas.Canvas(buf, pagesize=A4)
    y = 760

    pdf.setFont("Helvetica-Bold", 16)
    pdf.drawString(180, y, "Appointment Slip")
    y -= 50

    pdf.setFont("Helvetica", 11)
    fields = [
        ("Confirmation", a["confirmation_code"]),
        ("Patient", a["patient_name"]),
        ("Mobile", a["mobile"]),
        ("Date", a["appointment_date"]),
        ("Time", a["slot_time"]),
        ("Status", a["status"]),
    ]

    for k, v in fields:
        pdf.drawString(100, y, f"{k}: {v}")
        y -= 30

    pdf.drawString(100, y, "Meeting Link:")
    pdf.drawString(
        250, y,
        a["meeting_link"] if a["status"] == "CONFIRMED"
        else "Will be shared after confirmation"
    )

    pdf.showPage()
    pdf.save()
    buf.seek(0)

    return send_file(
        buf,
        as_attachment=True,
        download_name=f"{code}.pdf",
        mimetype="application/pdf"
    )

# =================================================
# ADMIN
# =================================================
@app.route("/admin", methods=["GET", "POST"])
def admin_login():
    if request.method == "POST" and request.form["password"] == "admin123":
        session["admin"] = True
        return redirect("/admin/dashboard")
    return render_template("admin_login.html")

@app.route("/admin/dashboard")
def admin_dashboard():
    if not session.get("admin"):
        return redirect("/admin")

    conn = db()

    apps = conn.execute(
        "SELECT * FROM appointments ORDER BY created_at DESC"
    ).fetchall()

    slots = conn.execute(
        "SELECT * FROM slots ORDER BY slot_date, start_time"
    ).fetchall()

    settings = conn.execute(
        "SELECT * FROM admin_settings WHERE id = 1"
    ).fetchone()

    conn.close()

    return render_template(
        "admin_dashboard.html",
        appointments=apps,
        slots=slots,
        settings=settings,
        confirmation_message=settings["confirmation_message"]
    )

@app.route("/admin/slots", methods=["POST"])
def add_slot():
    if session.get("admin"):
        f = request.form
        conn = db()
        conn.execute(
            "INSERT INTO slots VALUES (NULL,?,?,?,0)",
            (f["slot_date"], f["start_time"], f["end_time"])
        )
        conn.commit()
        conn.close()
    return redirect("/admin/dashboard")

@app.route("/admin/update/<int:id>", methods=["POST"])
def admin_update(id):
    f = request.form
    conn = db()
    conn.execute("""
        UPDATE appointments
        SET status=?, meeting_link=?, admin_remarks=?, updated_at=?
        WHERE id=?
    """, (
        f["status"],
        f["meeting_link"],
        f["remarks"],
        datetime.now().isoformat(),
        id
    ))
    conn.commit()
    conn.close()
    return redirect("/admin/dashboard")

@app.route("/admin/settings", methods=["POST"])
def admin_settings():
    if not session.get("admin"):
        return redirect("/admin")

    f = request.form
    conn = db()
    conn.execute("""
        UPDATE admin_settings
        SET doctor_whatsapp=?, upi_link=?
        WHERE id=1
    """, (f["doctor_whatsapp"], f["upi_link"]))
    conn.commit()
    conn.close()
    return redirect("/admin/dashboard")

@app.route("/admin/logout")
def admin_logout():
    session.clear()
    return redirect("/admin")

# =================================================
if __name__ == "__main__":
    app.run(debug=True)
