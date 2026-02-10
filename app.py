from flask import (
    Flask, render_template, request,
    redirect, session, flash, jsonify, send_file
)
import sqlite3, io
from datetime import datetime, date
from apscheduler.schedulers.background import BackgroundScheduler
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4

from scheduler import auto_expire_reserved, send_reminders

app = Flask(__name__,static_folder="static")
app.secret_key = "medbuddy-secret"

DB = "medbuddy.db"

# ---------------- DB HELPER ----------------
def db():
    conn = sqlite3.connect(
        DB,
        timeout=10,          # wait before failing
        check_same_thread=False
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA busy_timeout = 5000;")
    return conn


# ---------------- CONFIRMATION CODE ----------------
import uuid

def generate_code():
    return f"MB-{datetime.now().strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:4]}"

#cancel
def doctor_cancel_message(appt):
    return (
        f"Appointment Cancelled by Patient\n\n"
        f"Confirmation No: {appt['confirmation_code']}\n"
        f"Patient: {appt['patient_name']}\n"
        f"Date: {appt['appointment_date']}\n"
        f"Time: {appt['slot_time']}\n\n"
        f"Please cancel/delete this appointment from admin panel."
    )

# ---------------- SCHEDULER ----------------
scheduler = BackgroundScheduler()
scheduler.add_job(auto_expire_reserved, "interval", minutes=10)
scheduler.add_job(send_reminders, "interval", minutes=5)
scheduler.start()

# =================================================
# PUBLIC
# =================================================
@app.route("/")
def home():
    return render_template("index.html")

@app.route("/patient")
def patient_page():
    return render_template("patient.html")


@app.after_request
def add_cache_headers(response):
    if response.content_type.startswith(("image/", "text/css", "application/javascript")):
        response.headers["Cache-Control"] = "public, max-age=31536000"
    return response

# =================================================
# PATIENT
# =================================================
@app.route("/slots")
def available_slots():
    today = date.today().isoformat()
    conn = db()
    rows = conn.execute("""
        SELECT * FROM slots
        WHERE is_booked = 0
        AND slot_date >= ?
        ORDER BY slot_date, start_time
    """, (today,)).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])

@app.route("/book", methods=["POST"])
def book():
    f = request.form
    conn = db()

    slot = conn.execute("""
        SELECT * FROM slots
        WHERE id=? AND is_booked=0
    """, (f["slot_id"],)).fetchone()

    if not slot or slot["slot_date"] < date.today().isoformat():
        flash("Slot not available", "patient-error")
        return redirect("/patient")

    # ✅ FIXED LINE
    consultation_type = f.get("consultation_type", "FIRST")

    settings = conn.execute("""
        SELECT default_amount, followup_amount
        FROM admin_settings WHERE id=1
    """).fetchone()

    amount = (
        settings["followup_amount"]
        if consultation_type == "FOLLOWUP"
        else settings["default_amount"]
    )

    code = generate_code()
    now = datetime.now().isoformat()

    conn.execute("""
        INSERT INTO appointments (
            confirmation_code,
            patient_name, mobile, address,
            slot_id, appointment_date, slot_time,
            consultation_type, amount,
            status, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'RESERVED', ?, ?)
    """, (
        code,
        f["patient_name"],
        f["mobile"],
        f["address"],
        slot["id"],
        slot["slot_date"],
        f'{slot["start_time"]}-{slot["end_time"]}',
        consultation_type,
        amount,
        now,
        now
    ))

    conn.execute("UPDATE slots SET is_booked=1 WHERE id=?", (slot["id"],))
    conn.commit()
    conn.close()

    flash("Appointment reserved. Payment details will be sent via WhatsApp.", "patient-info")
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

    appt = conn.execute("""
        SELECT *
        FROM appointments
        WHERE confirmation_code=?
    """, (code,)).fetchone()

    if not appt or appt["status"] != "RESERVED":
        conn.close()
        flash("Appointment cannot be cancelled", "patient-error")
        return redirect("/status")

    try:
        # ❌ Cancel appointment
        conn.execute("""
            UPDATE appointments
            SET status='CANCELLED', updated_at=?
            WHERE confirmation_code=?
        """, (datetime.now().isoformat(), code))

        # 🔓 Free slot
        conn.execute("""
            UPDATE slots SET is_booked=0
            WHERE id=?
        """, (appt["slot_id"],))

        conn.commit()

    except Exception as e:
        conn.rollback()
        print("Cancel error:", e)
        flash("Cancellation failed", "patient-error")
        conn.close()
        return redirect("/status")

    conn.close()

    # 📲 WhatsApp message to doctor
    doctor_number = db().execute(
        "SELECT doctor_whatsapp FROM admin_settings WHERE id=1"
    ).fetchone()["doctor_whatsapp"]

    msg = doctor_cancel_message(appt)

    wa_link = (
        f"https://wa.me/{doctor_number}"
        f"?text={msg.replace(' ', '%20').replace('\\n', '%0A')}"
    )

    return render_template(
        "cancel_success.html",
        wa_link=wa_link,
        confirmation_code=code
    )

# =================================================
# PDF RECEIPT
# =================================================
@app.route("/appointment/pdf/<code>")
def appointment_pdf(code):
    conn = db()
    a = conn.execute("""
        SELECT a.*, s.doctor_whatsapp
        FROM appointments a
        JOIN admin_settings s ON s.id = 1
        WHERE a.confirmation_code = ?
    """, (code,)).fetchone()
    conn.close()

    if not a:
        return "Invalid confirmation code", 404

    buf = io.BytesIO()
    pdf = canvas.Canvas(buf, pagesize=A4)
    width, height = A4

    y = height - 50

    # ================= HEADER =================
    pdf.setFont("Helvetica-Bold", 18)
    pdf.drawCentredString(width / 2, y, "SangamKripa HomeoCare")
    y -= 25

    pdf.setFont("Helvetica", 11)
    pdf.drawCentredString(
        width / 2, y,
        "Online Homeopathy Consultation"
    )
    y -= 15

    pdf.setFont("Helvetica", 10)
    pdf.drawCentredString(
        width / 2, y,
        f"WhatsApp: +{a['doctor_whatsapp']}"
    )

    # Divider
    y -= 25
    pdf.line(40, y, width - 40, y)
    y -= 30

    # ================= TITLE =================
    pdf.setFont("Helvetica-Bold", 14)
    pdf.drawCentredString(width / 2, y, "CONSULTATION RECEIPT")
    y -= 30

    # ================= DETAILS =================
    pdf.setFont("Helvetica", 11)

    def row(label, value):
        nonlocal y
        pdf.drawString(60, y, f"{label}:")
        pdf.drawString(220, y, str(value))
        y -= 22

    row("Receipt No", a["confirmation_code"])
    row("Patient Name", a["patient_name"])
    row("Mobile Number", a["mobile"])
    row("Appointment Date", a["appointment_date"])
    row("Time Slot", a["slot_time"])
    row("Consultation Type", (a["consultation_type"] or "FIRST").title())
    row("Status", a["status"])
    row("Amount Paid", f"₹ {a['amount']}")

    # ================= NOTES =================
    y -= 10
    pdf.line(60, y, width - 60, y)
    y -= 25

    pdf.setFont("Helvetica-Oblique", 10)
    pdf.drawString(
        60, y,
        "Note: This is a computer-generated receipt and does not require a signature."
    )

    # ================= FINALIZE =================
    pdf.showPage()
    pdf.save()
    buf.seek(0)

    return send_file(
        buf,
        as_attachment=True,
        download_name=f"{a['confirmation_code']}.pdf",
        mimetype="application/pdf"
    )

    # ================= FOOTER =================
    y -= 40
    pdf.setFont("Helvetica", 9)
    pdf.drawCentredString(
        width / 2, y,
        "Thank you for choosing SangamKripa HomeoCare"
    )

    pdf.showPage()
    pdf.save()
    buf.seek(0)

    return send_file(
        buf,
        as_attachment=True,
        download_name=f"{code}_receipt.pdf",
        mimetype="application/pdf"
    )


# =================================================
# ADMIN
# =================================================
@app.route("/admin/dashboard")
def admin_dashboard():
    if not session.get("admin"):
        return redirect("/admin")

    search = request.args.get("search", "")
    status_filter = request.args.get("status", "")
    consult_filter = request.args.get("consultation_type", "")
    from_date = request.args.get("from_date", "")
    to_date = request.args.get("to_date", "")

    conn = db()
    query = "SELECT * FROM appointments WHERE 1=1"
    params = []

    if search:
        like = f"%{search}%"
        query += " AND (patient_name LIKE ? OR mobile LIKE ? OR confirmation_code LIKE ?)"
        params.extend([like, like, like])

    if status_filter:
        query += " AND status=?"
        params.append(status_filter)

    if consult_filter:
        query += " AND consultation_type=?"
        params.append(consult_filter)

    if from_date:
        query += " AND appointment_date >= ?"
        params.append(from_date)

    if to_date:
        query += " AND appointment_date <= ?"
        params.append(to_date)

    query += " ORDER BY appointment_date DESC"

    appointments = conn.execute(query, params).fetchall()

    stats = {
        "total": conn.execute("SELECT COUNT(*) FROM appointments").fetchone()[0],
        "reserved": conn.execute("SELECT COUNT(*) FROM appointments WHERE status='RESERVED'").fetchone()[0],
        "confirmed": conn.execute("SELECT COUNT(*) FROM appointments WHERE status='CONFIRMED'").fetchone()[0],
        "today": conn.execute(
            "SELECT COUNT(*) FROM appointments WHERE appointment_date=DATE('now')"
        ).fetchone()[0],
    }

    slots = conn.execute(
        "SELECT * FROM slots ORDER BY slot_date,start_time"
    ).fetchall()

    settings = conn.execute(
        "SELECT * FROM admin_settings WHERE id=1"
    ).fetchone()

    conn.close()

    return render_template(
        "admin_dashboard.html",
        appointments=appointments,
        slots=slots,
        settings=settings,
        stats=stats,
        search=search,
        status_filter=status_filter,
        from_date=from_date,
        to_date=to_date
    )

@app.route("/admin/update/<int:id>", methods=["POST"])
def admin_update(id):
    if not session.get("admin"):
        return redirect("/admin")

    f = request.form
    now = datetime.now().isoformat()

    conn = db()

    # Fetch existing appointment
    appt = conn.execute(
        "SELECT consultation_type FROM appointments WHERE id=?",
        (id,)
    ).fetchone()

    if not appt:
        conn.close()
        flash("Appointment not found", "admin-error")
        return redirect("/admin/dashboard")

    # Get fee based on consultation type
    settings = conn.execute(
        "SELECT default_amount, followup_amount FROM admin_settings WHERE id=1"
    ).fetchone()

    amount = (
        settings["followup_amount"]
        if appt["consultation_type"] == "followup"
        else settings["default_amount"]
    )

    conn.execute("""
        UPDATE appointments
        SET status = ?,
            meeting_link = ?,
            admin_remarks = ?,
            amount = ?,
            updated_at = ?
        WHERE id = ?
    """, (
        f.get("status"),
        f.get("meeting_link"),
        f.get("remarks"),
        amount,
        now,
        id
    ))

    conn.commit()
    conn.close()

    flash("Appointment updated successfully", "admin-info")
    return redirect("/admin/dashboard")


# -------- DELETE SLOT --------
@app.route("/admin/delete/slot/<int:id>", methods=["POST"])
def delete_slot(id):
    if not session.get("admin"):
        return redirect("/admin")

    conn = db()
    slot = conn.execute(
        "SELECT is_booked FROM slots WHERE id=?",
        (id,)
    ).fetchone()

    if slot and not slot["is_booked"]:
        conn.execute("DELETE FROM slots WHERE id=?", (id,))
        conn.commit()

    conn.close()
    return redirect("/admin/dashboard")

# -------- DELETE APPOINTMENT --------
@app.route("/admin/delete_appointment/<int:id>", methods=["POST"])
def delete_appointment(id):
    if not session.get("admin"):
        return redirect("/admin")

    conn = db()

    appt = conn.execute("""
        SELECT slot_id FROM appointments WHERE id=?
    """, (id,)).fetchone()

    if appt:
        # free the slot
        conn.execute(
            "UPDATE slots SET is_booked=0 WHERE id=?",
            (appt["slot_id"],)
        )

        # delete appointment
        conn.execute(
            "DELETE FROM appointments WHERE id=?",
            (id,)
        )

        conn.commit()

    conn.close()
    flash("Appointment deleted and slot freed", "admin-info")
    return redirect("/admin/dashboard")



# -------- SETTINGS --------
@app.route("/admin/settings", methods=["POST"])
def admin_settings():
    if not session.get("admin"):
        return redirect("/admin")

    f = request.form
    conn = db()

    conn.execute("""
        UPDATE admin_settings
        SET doctor_whatsapp=?,
            upi_link=?,
            default_amount=?,
            followup_amount=?
        WHERE id=1
    """, (
        f.get("doctor_whatsapp"),
        f.get("upi_link"),
        f.get("default_amount"),
        f.get("followup_amount"),
    ))

    conn.commit()
    conn.close()
    flash("Settings updated successfully", "admin-info")
    return redirect("/admin/dashboard")

# -------- AUTH --------
@app.route("/admin", methods=["GET", "POST"])
def admin_login():
    if request.method == "POST":
        if request.form["username"] == "admin" and request.form["password"] == "admin123":
            session["admin"] = True
            return redirect("/admin/dashboard")
        flash("Invalid credentials", "admin-error")
    return render_template("admin_login.html")

@app.route("/admin/logout")
def admin_logout():
    session.clear()
    return redirect("/admin")

# -------- ADD SLOT --------
@app.route("/admin/slots", methods=["POST"])
def add_slot():
    if not session.get("admin"):
        return redirect("/admin")

    f = request.form
    if f["slot_date"] < date.today().isoformat():
        flash("Cannot create slots in the past", "admin-error")
        return redirect("/admin/dashboard")

    conn = db()
    conn.execute("""
        INSERT INTO slots (slot_date, start_time, end_time, is_booked)
        VALUES (?, ?, ?, 0)
    """, (
        f["slot_date"],
        f["start_time"],
        f["end_time"]
    ))
    conn.commit()
    conn.close()

    flash("Slot added successfully", "admin-info")
    return redirect("/admin/dashboard")

# =================================================
if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0")
