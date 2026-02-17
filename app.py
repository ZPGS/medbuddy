import select
from flask import (
    Flask, render_template, request,
    redirect, session, flash, jsonify, send_file
)
from supabase import create_client
from dotenv import load_dotenv
import os, io, uuid
from datetime import datetime, date
from apscheduler.schedulers.background import BackgroundScheduler
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4
from werkzeug.utils import secure_filename

from scheduler import auto_expire_reserved, send_reminders

# ==============================
# CONFIG
# ==============================

load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

app = Flask(__name__, static_folder="static")
app.secret_key = "medbuddy-secret"

# ==============================
# SAFE QUERY HELPERS
# ==============================

def get_single(table, column, value):
    response = (
        supabase.table(table)
        .select("*")
        .eq(column, value)
        .maybe_single()
        .execute()
    )
    return response.data if response and response.data else None


def get_all(query):
    response = query.execute()
    return response.data if response and response.data else []

# ==============================
# CONFIRMATION CODE
# ==============================

def generate_code():
    return f"MB-{datetime.utcnow().strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:4]}"

# ==============================
# FILE UPLOAD
# ==============================

UPLOAD_FOLDER = "uploads"
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

@app.route("/upload/<code>", methods=["GET", "POST"])
def upload_reports(code):

    appt = get_single("appointments", "confirmation_code", code)

    if not appt:
        return "Invalid confirmation code", 404

    if request.method == "POST":
        file = request.files.get("report")

        if not file or file.filename == "":
            return render_template(
                "upload_reports.html",
                code=code,
                error="Please select a file."
            )

        filename = secure_filename(file.filename)
        path = os.path.join(UPLOAD_FOLDER, filename)
        file.save(path)

        supabase.table("medical_reports").insert({
            "confirmation_code": code,
            "file_name": filename,
            "file_path": path,
            "uploaded_at": datetime.utcnow().isoformat()
        }).execute()

        return render_template(
            "upload_success.html",
            code=code,
            patient_name=appt["patient_name"]
        )

    return render_template("upload_reports.html", code=code)

@app.route("/admin/reports/<code>")
def admin_reports(code):
    if not session.get("admin"):
        return redirect("/admin")

    reports = supabase.table("medical_reports") \
        .select("*") \
        .eq("confirmation_code", code) \
        .order("uploaded_at", desc=True) \
        .execute().data

    return render_template(
        "admin_reports.html",
        reports=reports,
        code=code
    )


@app.route('/uploads/<path:filename>')
def uploaded_file(filename):
    return send_file(
        os.path.join(UPLOAD_FOLDER, filename),
        as_attachment=False  # 🔥 THIS is important
    )

# ==============================
# HISTORY
@app.route("/admin/settings", methods=["POST"])
def admin_settings():
    if not session.get("admin"):
        return redirect("/admin")

    f = request.form

    # Update settings row id=1
    response = supabase.table("admin_settings") \
        .update({
            "doctor_whatsapp": f.get("doctor_whatsapp"),
            "upi_link": f.get("upi_link"),
            "default_amount": int(f.get("default_amount") or 0),
            "followup_amount": int(f.get("followup_amount") or 0),
            "default_meeting_link": f.get("default_meeting_link")
        }) \
        .eq("id", 1) \
        .execute()

    if response.data is None:
        flash("Failed to update settings", "admin-error")
    else:
        flash("Settings updated successfully", "admin-info")

    return redirect("/admin/dashboard")



@app.route("/history/<code>")
def history_detail(code):
    appt = get_single("appointments", "confirmation_code", code)

    if not appt:
        return "Invalid confirmation code", 404

    reports = supabase.table("medical_reports") \
        .select("*") \
        .eq("confirmation_code", code) \
        .order("uploaded_at", desc=True) \
        .execute().data
    return render_template(
        "history.html",
        appt=appt,
        reports=reports
    )

@app.route("/admin/history", methods=["GET", "POST"], strict_slashes=False)
def admin_history():
    if not session.get("admin"):
        return redirect("/admin")

    patients = []
    search_value = ""

    if request.method == "POST":
        search_value = request.form.get("search", "").strip()

        if search_value:
            response = (
                supabase
                .table("appointments")
                .select("*")
                .or_(f"mobile.ilike.%{search_value}%,patient_name.ilike.%{search_value}%")
                .order("created_at", desc=True)
                .execute()
            )

            rows = response.data or []

            # GROUP BY MOBILE
            grouped = {}
            for row in rows:
                mobile = row["mobile"]

                if mobile not in grouped:
                    grouped[mobile] = {
                        "name": row["patient_name"],
                        "mobile": mobile,
                        "appointments": []
                    }

                grouped[mobile]["appointments"].append(row)

            patients = list(grouped.values())

    return render_template(
        "admin_history.html",
        patients=patients,
        search_value=search_value
    )





# ==============================
# PDF RECEIPT (NEW)
# ==============================

@app.route("/appointment/pdf/<code>")
def appointment_pdf(code):

    appt = get_single("appointments", "confirmation_code", code)
    settings = get_single("admin_settings", "id", 1)

    if not appt:
        return "Invalid confirmation code", 404

    buf = io.BytesIO()
    pdf = canvas.Canvas(buf, pagesize=A4)
    width, height = A4

    y = height - 50

    # Header
    pdf.setFont("Helvetica-Bold", 18)
    pdf.drawCentredString(width / 2, y, "Harmony HomeoCare")
    y -= 25

    pdf.setFont("Helvetica", 11)
    pdf.drawCentredString(width / 2, y, "Online Homeopathy Consultation")
    y -= 15

    pdf.setFont("Helvetica", 10)
    pdf.drawCentredString(
        width / 2,
        y,
        f"WhatsApp: +{settings['doctor_whatsapp'] if settings else ''}"
    )

    y -= 25
    pdf.line(40, y, width - 40, y)
    y -= 30

    pdf.setFont("Helvetica-Bold", 14)
    pdf.drawCentredString(width / 2, y, "CONSULTATION RECEIPT")
    y -= 30

    pdf.setFont("Helvetica", 11)

    def row(label, value):
        nonlocal y
        pdf.drawString(60, y, f"{label}:")
        pdf.drawString(220, y, str(value))
        y -= 22

    row("Receipt No", appt["confirmation_code"])
    row("Patient Name", appt["patient_name"])
    row("Mobile Number", appt["mobile"])
    row("Appointment Date", appt["appointment_date"])
    row("Time Slot", appt["slot_time"])
    row("Consultation Type", (appt["consultation_type"] or "FIRST").title())
    row("Status", appt["status"])
    row("Amount Paid", f"₹ {appt['amount']}")

    y -= 20
    pdf.setFont("Helvetica-Oblique", 9)
    pdf.drawString(
        60,
        y,
        "This is a computer-generated receipt."
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

@app.route("/appointment/cancel/<code>", methods=["POST"])
def cancel_appointment(code):
    if not session.get("admin"):
        return redirect("/admin")

    appt = get_single("appointments", "confirmation_code", code)
    if not appt:
        flash("Invalid confirmation code", "patient-error")
        return redirect("/admin/dashboard")

    supabase.table("appointments") \
        .update({"status": "CANCELLED"}) \
        .eq("confirmation_code", code) \
        .execute()

    supabase.table("slots") \
        .update({"is_booked": False}) \
        .eq("id", appt["slot_id"]) \
        .execute()

    flash(f"Appointment cancelled for {code}", "patient-info")
    return redirect("/admin/dashboard")

# ==============================
# PUBLIC
# ==============================

@app.route("/")
def home():
    return render_template("index.html")

@app.route("/patient")
def patient_page():
    return render_template("patient.html")

# ==============================
# PATIENT
# ==============================

@app.route("/slots")
def available_slots():
    today = date.today().isoformat()

    rows = get_all(
        supabase.table("slots")
        .select("*")
        .eq("is_booked", False)
        .gte("slot_date", today)
        .order("slot_date")
    )

    return jsonify(rows)

@app.route("/book", methods=["POST"])
def book():

    f = request.form

    slot = get_single("slots", "id", f["slot_id"])

    if not slot or slot["is_booked"]:
        flash("Slot not available", "patient-error")
        return redirect("/patient")

    settings = get_single("admin_settings", "id", 1)

    consultation_type = f.get("consultation_type", "FIRST")

    amount = (
        settings["followup_amount"]
        if consultation_type == "FOLLOWUP"
        else settings["default_amount"]
    )

    code = generate_code()
    now = datetime.utcnow().isoformat()

    supabase.table("appointments").insert({
        "confirmation_code": code,
        "patient_name": f["patient_name"],
        "mobile": f["mobile"],
        "address": f["address"],
        "slot_id": slot["id"],
        "appointment_date": slot["slot_date"],
        "slot_time": f'{slot["start_time"]}-{slot["end_time"]}',
        "consultation_type": consultation_type,
        "amount": amount,
        "status": "RESERVED",
        "created_at": now,
        "updated_at": now
    }).execute()

    supabase.table("slots") \
        .update({"is_booked": True}) \
        .eq("id", slot["id"]) \
        .execute()

    flash("Appointment reserved.", "patient-info")
    return redirect("/patient")

# ==============================
# ADMIN
# ==============================

@app.route("/admin/dashboard")
def admin_dashboard():
    if not session.get("admin"):
        return redirect("/admin")

    search = request.args.get("search")
    status_filter = request.args.get("status")
    consult_filter = request.args.get("consultation_type")
    from_date = request.args.get("from_date")
    to_date = request.args.get("to_date")

    query = supabase.table("appointments").select("*")

    if search:
        query = query.or_(
            f"patient_name.ilike.%{search}%,"
            f"mobile.ilike.%{search}%,"
            f"confirmation_code.ilike.%{search}%"
        )

    if status_filter:
        query = query.eq("status", status_filter)

    if consult_filter:
        query = query.eq("consultation_type", consult_filter)

    if from_date:
        query = query.gte("appointment_date", from_date)

    if to_date:
        query = query.lte("appointment_date", to_date)

    appointments = query.order("appointment_date", desc=True).execute().data

    slots = supabase.table("slots") \
        .select("*") \
        .order("slot_date") \
        .execute().data

    settings = supabase.table("admin_settings") \
        .select("*") \
        .eq("id", 1) \
        .maybe_single() \
        .execute().data

    stats = {
        "total": len(appointments),
        "reserved": len([a for a in appointments if a["status"] == "RESERVED"]),
        "confirmed": len([a for a in appointments if a["status"] == "CONFIRMED"]),
        "today": len([a for a in appointments if a["appointment_date"] == date.today().isoformat()])
    }

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


# ==============================
# AUTH
# ==============================

@app.route("/admin", methods=["GET", "POST"])
def admin_login():
    if request.method == "POST":
        username = request.form.get("username")
        password = request.form.get("password")

        if username == "admin" and password == "admin123":
            session["admin"] = True
            flash("Login successful", "admin-info")
            return redirect("/admin/dashboard")

        flash("Invalid username or password", "admin-error")

    return render_template("admin_login.html")


# ==============================
# ADD SLOT
# ==============================

@app.route("/admin/slots", methods=["POST"])
def add_slot():
    if not session.get("admin"):
        return redirect("/admin")

    f = request.form

    # Prevent past date slot creation
    if f["slot_date"] < date.today().isoformat():
        flash("Cannot create slots in the past", "admin-error")
        return redirect("/admin/dashboard")

    # Insert into Supabase
    supabase.table("slots").insert({
        "slot_date": f["slot_date"],
        "start_time": f["start_time"],
        "end_time": f["end_time"],
        "is_booked": False
    }).execute()

    flash("Slot added successfully", "admin-info")
    return redirect("/admin/dashboard")

@app.route("/admin/update/<int:id>", methods=["POST"])
def admin_update(id):
    if not session.get("admin"):
        return redirect("/admin")

    f = request.form
    now = datetime.utcnow().isoformat()

    # Fetch appointment
    appt_res = supabase.table("appointments") \
        .select("*") \
        .eq("id", id) \
        .maybe_single() \
        .execute()

    appt = appt_res.data

    if not appt:
        flash("Appointment not found", "admin-error")
        return redirect("/admin/dashboard")

    # Get settings
    settings_res = supabase.table("admin_settings") \
        .select("*") \
        .eq("id", 1) \
        .maybe_single() \
        .execute()

    settings = settings_res.data

    if not settings:
        flash("Settings missing", "admin-error")
        return redirect("/admin/dashboard")

    # Calculate correct fee
    amount = (
        settings["followup_amount"]
        if appt["consultation_type"] == "FOLLOWUP"
        else settings["default_amount"]
    )

    # Update
    supabase.table("appointments") \
        .update({
            "status": f.get("status"),
            "meeting_link": f.get("meeting_link"),
            "admin_remarks": f.get("remarks"),
            "amount": amount,
            "updated_at": now
        }) \
        .eq("id", id) \
        .execute()

    flash("Appointment updated successfully", "admin-info")
    return redirect("/admin/dashboard")

# ==============================
# STATUS
# ==============================

@app.route("/status", methods=["GET", "POST"])
def status():
    appointment = None

    if request.method == "POST":
        code = request.form.get("confirmation_code")

        if code:
            res = supabase.table("appointments") \
                .select("*") \
                .eq("confirmation_code", code) \
                .maybe_single() \
                .execute()

            appointment = res.data

            if not appointment:
                flash("Invalid confirmation code", "patient-error")

    return render_template("status.html", appointment=appointment)

@app.route("/admin/delete_appointment/<int:id>", methods=["POST"])
def delete_appointment(id):
    if not session.get("admin"):
        return redirect("/admin")

    # Get appointment to free slot
    appt = supabase.table("appointments") \
        .select("slot_id") \
        .eq("id", id) \
        .maybe_single() \
        .execute()

    if appt.data:
        # Free the slot
        supabase.table("slots") \
            .update({"is_booked": False}) \
            .eq("id", appt.data["slot_id"]) \
            .execute()

        # Delete appointment
        supabase.table("appointments") \
            .delete() \
            .eq("id", id) \
            .execute()

        flash("Appointment deleted and slot freed", "admin-info")
    else:
        flash("Appointment not found", "admin-error")

    return redirect("/admin/dashboard")

@app.route("/admin/logout")
def admin_logout():
    session.clear()
    return redirect("/admin")

# ==============================

if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)
