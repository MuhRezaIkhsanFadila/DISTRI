import os
import uuid
from datetime import datetime, date, timedelta
from zoneinfo import ZoneInfo
from functools import wraps

import click
import cv2
import numpy as np
import qrcode
from flask import Flask, render_template, redirect, url_for, request, flash, jsonify
from flask_login import LoginManager, login_user, logout_user, login_required, current_user, UserMixin
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import func, inspect, text
from werkzeug.security import generate_password_hash, check_password_hash


app = Flask(__name__)
app.config["SECRET_KEY"] = "change-this-secret-key"
app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///tml_arena.db"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["QR_FOLDER"] = os.path.join(app.static_folder, "qrs")
os.makedirs(app.config["QR_FOLDER"], exist_ok=True)
DEFAULT_TZ = ZoneInfo("Asia/Jakarta")

db = SQLAlchemy(app)
login_manager = LoginManager(app)
login_manager.login_view = "login"


class User(db.Model, UserMixin):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(20), nullable=False, default="user")

    def set_password(self, password: str) -> None:
        self.password_hash = generate_password_hash(password)

    def check_password(self, password: str) -> bool:
        return check_password_hash(self.password_hash, password)


class Booking(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    customer_name = db.Column(db.String(120), nullable=False)
    phone = db.Column(db.String(50), nullable=False)
    play_date = db.Column(db.Date, nullable=False)
    start_time = db.Column(db.Time, nullable=False)
    end_time = db.Column(db.Time, nullable=False)
    notes = db.Column(db.Text)
    status = db.Column(db.String(20), nullable=False, default="pending")
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    user = db.relationship("User", backref=db.backref("bookings", lazy=True))


class Membership(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    package_name = db.Column(db.String(120), nullable=False)
    price = db.Column(db.Integer, nullable=False)
    start_date = db.Column(db.Date, nullable=False)
    end_date = db.Column(db.Date, nullable=False)
    type = db.Column(db.String(20), nullable=False, default="membership")
    token = db.Column(db.String(64), unique=True, nullable=False)
    status = db.Column(db.String(20), nullable=False, default="active")
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    user = db.relationship("User", backref=db.backref("memberships", lazy=True))

    def qr_filename(self) -> str:
        return f"{self.token}.png"

    def qr_path(self) -> str:
        return os.path.join(app.config["QR_FOLDER"], self.qr_filename())


class MembershipSlot(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    membership_id = db.Column(db.Integer, db.ForeignKey("membership.id"), nullable=False)
    day_of_week = db.Column(db.Integer, nullable=False)  # 0 = Senin
    start_time = db.Column(db.Time, nullable=False)
    end_time = db.Column(db.Time, nullable=False)

    membership = db.relationship("Membership", backref=db.backref("slots", lazy=True, cascade="all, delete-orphan"))


class ChatMessage(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    sender_role = db.Column(db.String(20), nullable=False)
    content = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    user = db.relationship("User", backref=db.backref("chat_messages", lazy=True))


class Attendance(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    membership_id = db.Column(db.Integer, db.ForeignKey("membership.id"), nullable=False)
    slot_id = db.Column(db.Integer, db.ForeignKey("membership_slot.id"), nullable=False)
    check_in_time = db.Column(db.DateTime, default=datetime.utcnow)

    membership = db.relationship("Membership", backref=db.backref("attendances", lazy=True))
    slot = db.relationship("MembershipSlot", backref=db.backref("attendances", lazy=True))


@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))


DAY_NAMES = [
    "Senin",
    "Selasa",
    "Rabu",
    "Kamis",
    "Jumat",
    "Sabtu",
    "Minggu",
]


@app.context_processor
def inject_now():
    now = datetime.now(tz=DEFAULT_TZ)
    return {
        "current_year": now.year,
        "current_time": now.strftime('%H:%M'),
        "DAY_NAMES": DAY_NAMES,
    }


def generate_membership_qr(membership: Membership) -> None:
    qr_data = url_for("membership_verify", token=membership.token, _external=True)
    img = qrcode.make(qr_data)
    img.save(membership.qr_path())


def extract_token_from_payload(payload: str) -> str:
    if not payload:
        return ""
    payload = payload.strip()
    if "/membership/" in payload:
        payload = payload.split("/membership/", 1)[1]
    payload = payload.split("?")[0].strip("/")
    return payload


def get_current_slot(membership: Membership, now: datetime | None = None):
    now = (now or datetime.now(tz=DEFAULT_TZ))
    for slot in membership.slots:
        if slot.day_of_week == now.weekday() and slot.start_time <= now.time() <= slot.end_time:
            return slot
    return None


def perform_checkin(membership: Membership, slot: MembershipSlot | None, now: datetime | None = None):
    now = (now or datetime.now(tz=DEFAULT_TZ))
    if not slot:
        return False, "Tidak ada jadwal yang aktif saat ini."

    already_present = (
        Attendance.query.filter(
            Attendance.membership_id == membership.id,
            Attendance.slot_id == slot.id,
            func.date(Attendance.check_in_time) == now.date(),
        ).first()
        is not None
    )
    if already_present:
        return False, "Kehadiran sudah dicatat untuk slot ini hari ini."

    att = Attendance(membership_id=membership.id, slot_id=slot.id, check_in_time=now)
    db.session.add(att)
    db.session.commit()
    return True, "Check-in berhasil direkam."


def role_required(role_name):
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            if not current_user.is_authenticated:
                return login_manager.unauthorized()
            if current_user.role != role_name:
                flash("Anda tidak memiliki akses ke halaman tersebut.", "warning")
                return redirect(url_for("dashboard"))
            return func(*args, **kwargs)

        return wrapper

    return decorator


def ensure_membership_type_column():
    inspector = inspect(db.engine)
    if inspector.has_table('membership'):
        columns = [col['name'] for col in inspector.get_columns('membership')]
        if 'type' not in columns:
            db.session.execute(text("ALTER TABLE membership ADD COLUMN type VARCHAR(20) DEFAULT 'membership'"))
            db.session.commit()


with app.app_context():
    db.create_all()
    ensure_membership_type_column()


@app.route("/")
def home():
    return render_template("index.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard"))

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")

        user = User.query.filter_by(username=username).first()
        if user and user.check_password(password):
            login_user(user)
            flash("Berhasil masuk.", "success")
            return redirect(url_for("dashboard"))

        flash("Nama pengguna atau kata sandi salah.", "danger")

    return render_template("login.html")


@app.route("/register", methods=["POST"])
def register():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard"))

    team_name = request.form.get("team_name", "").strip()
    team_type = request.form.get("team_type", "").strip()
    email = request.form.get("email", "").strip()
    pic_name = request.form.get("pic_name", "").strip()
    phone = request.form.get("phone", "").strip()
    password = request.form.get("password", "")
    password_confirm = request.form.get("password_confirm", "")

    if not all([team_name, team_type, email, pic_name, phone, password, password_confirm]):
        flash("Semua field wajib diisi.", "danger")
        return redirect(url_for("login"))

    if password != password_confirm:
        flash("Password dan konfirmasi tidak sama.", "danger")
        return redirect(url_for("login"))

    if User.query.filter_by(username=email).first():
        flash("Akun dengan email tersebut sudah terdaftar.", "danger")
        return redirect(url_for("login"))

    user = User(username=email, role="user")
    user.set_password(password)
    db.session.add(user)
    db.session.commit()

    flash("Pendaftaran berhasil. Silakan masuk.", "success")
    return redirect(url_for("login"))


@app.route("/logout", methods=["POST"])
@login_required
def logout():
    logout_user()
    flash("Anda sudah keluar.", "info")
    return redirect(url_for("login"))


@app.route("/dashboard")
@login_required
def dashboard():
    if current_user.role == "admin":
        return redirect(url_for("admin_dashboard"))

    membership = get_active_membership(current_user.id)
    if membership:
        return redirect(url_for("member_dashboard"))

    return render_template("access_choice.html")


@app.route("/membership/offer")
@login_required
def membership_offer():
    membership = get_active_membership(current_user.id)
    if membership:
        return redirect(url_for("member_dashboard"))
    return render_template("membership_offer.html")


@app.route("/admin")
@login_required
@role_required("admin")
def admin_dashboard():
    today = datetime.now(tz=DEFAULT_TZ).date()
    bookings = Booking.query.order_by(Booking.play_date.desc(), Booking.start_time).all()

    upcoming_bookings = Booking.query.filter(Booking.play_date >= today).count()
    bookings_today = Booking.query.filter(Booking.play_date == today).count()
    pending_bookings = Booking.query.filter_by(status="pending").count()
    active_memberships = Membership.query.filter_by(status="active").count()
    total_users = User.query.count()
    revenue_total = db.session.query(func.coalesce(func.sum(Membership.price), 0)).scalar() or 0
    expiring_soon = Membership.query.filter(
        Membership.end_date >= today,
        Membership.end_date <= today + timedelta(days=7),
        Membership.status == "active",
    ).count()

    booking_status_counts = {row[0]: row[1] for row in db.session.query(Booking.status, func.count(Booking.id)).group_by(Booking.status).all()}
    membership_type_counts = {row[0]: row[1] for row in db.session.query(Membership.type, func.count(Membership.id)).group_by(Membership.type).all()}

    first_day = today.replace(day=1)
    start_offset = first_day.weekday()
    calendar_start = first_day - timedelta(days=start_offset)
    if first_day.month == 12:
        next_month = first_day.replace(year=first_day.year + 1, month=1, day=1)
    else:
        next_month = first_day.replace(month=first_day.month + 1, day=1)
    last_day = next_month - timedelta(days=1)
    end_offset = 6 - last_day.weekday()
    calendar_end = last_day + timedelta(days=end_offset)

    month_calendar = []
    current_week = []
    day = calendar_start
    while day <= calendar_end:
        day_bookings = Booking.query.filter(Booking.play_date == day).order_by(Booking.start_time).all()
        current_week.append({
            'date': day,
            'day_number': day.day,
            'is_current_month': day.month == today.month,
            'is_today': day == today,
            'booking_count': len(day_bookings),
            'bookings': day_bookings,
        })
        if len(current_week) == 7:
            month_calendar.append(current_week)
            current_week = []
        day += timedelta(days=1)

    latest_memberships = Membership.query.order_by(Membership.created_at.desc()).limit(3).all()
    chat_users = User.query.filter(User.role != 'admin').order_by(User.username.asc()).all()
    selected_chat_user_id = request.args.get('chat_user', type=int)
    selected_chat_user = None
    if selected_chat_user_id:
        selected_chat_user = User.query.filter_by(id=selected_chat_user_id, role='user').first()
    if not selected_chat_user and chat_users:
        selected_chat_user = chat_users[0]

    chat_messages = []
    if selected_chat_user:
        chat_messages = ChatMessage.query.filter_by(user_id=selected_chat_user.id).order_by(ChatMessage.created_at.asc()).all()

    return render_template(
        "admin_dashboard.html",
        bookings=bookings,
        upcoming_bookings=upcoming_bookings,
        bookings_today=bookings_today,
        pending_bookings=pending_bookings,
        active_memberships=active_memberships,
        total_users=total_users,
        revenue_total=revenue_total,
        expiring_soon=expiring_soon,
        booking_status_counts=booking_status_counts,
        membership_type_counts=membership_type_counts,
        month_calendar=month_calendar,
        month_label=first_day.strftime('%B %Y'),
        today=today,
        latest_memberships=latest_memberships,
        chat_messages=chat_messages,
        chat_users=chat_users,
        selected_chat_user=selected_chat_user,
    )


@app.route("/admin/bookings/<int:booking_id>/status", methods=["POST"])
@login_required
@role_required("admin")
def update_booking_status(booking_id):
    booking = Booking.query.get_or_404(booking_id)
    new_status = request.form.get("status", "pending")
    booking.status = new_status
    db.session.commit()
    flash("Status booking diperbarui.", "success")
    return redirect(url_for("admin_dashboard"))


@app.route("/admin/memberships", methods=["GET", "POST"])
@login_required
@role_required("admin")
def admin_memberships():
    users = User.query.order_by(User.username.asc()).all()
    memberships = Membership.query.order_by(Membership.created_at.desc()).all()

    if request.method == "POST":
        user_id = request.form.get("user_id")
        package_name = request.form.get("package_name", "").strip()
        price = request.form.get("price", "0")
        membership_type = request.form.get("membership_type", "membership")
        start_date = request.form.get("start_date")
        end_date = request.form.get("end_date")

        if membership_type == 'membership' and not all([user_id, package_name, start_date, end_date]):
            flash("Semua field wajib diisi untuk membership berlangganan.", "danger")
            return redirect(url_for("admin_memberships"))

        if membership_type == 'single' and not all([user_id, package_name]):
            flash("Pilih user dan nama paket untuk sekali main.", "danger")
            return redirect(url_for("admin_memberships"))

        user = User.query.get(int(user_id))
        if not user:
            flash("User tidak ditemukan.", "danger")
            return redirect(url_for("admin_memberships"))

        # For single-play, set start/end to today
        if membership_type == 'single':
            sd = datetime.now(tz=DEFAULT_TZ).date()
            ed = sd
        else:
            sd = datetime.strptime(start_date, "%Y-%m-%d").date()
            ed = datetime.strptime(end_date, "%Y-%m-%d").date()

        membership = Membership(
            user_id=user.id,
            package_name=package_name,
            price=int(price or 0),
            start_date=sd,
            end_date=ed,
            type=membership_type,
            token=uuid.uuid4().hex,
        )
        db.session.add(membership)
        db.session.commit()
        generate_membership_qr(membership)
        flash("Membership baru dibuat dan QR dihasilkan.", "success")
        return redirect(url_for("admin_membership_detail", membership_id=membership.id))

    return render_template("admin_memberships.html", memberships=memberships, users=users)


@app.route("/admin/memberships/<int:membership_id>")
@login_required
@role_required("admin")
def admin_membership_detail(membership_id):
    membership = Membership.query.get_or_404(membership_id)
    attendance_today = [
        att
        for att in membership.attendances
        if att.check_in_time.astimezone(DEFAULT_TZ).date() == datetime.now(tz=DEFAULT_TZ).date()
    ]
    return render_template(
        "admin_membership_detail.html",
        membership=membership,
        attendance_today=attendance_today,
    )


@app.route("/chat/send", methods=["POST"])
@login_required
def send_chat_message():
    content = request.form.get("content", "").strip()
    if not content:
        flash("Isi pesan tidak boleh kosong.", "danger")
        return redirect(url_for("admin_dashboard") if current_user.role == "admin" else url_for("member_dashboard"))

    if current_user.role == "admin":
        user_id = request.form.get("user_id")
        if not user_id:
            flash("Pilih member tujuan.", "danger")
            return redirect(url_for("admin_dashboard"))
        recipient = User.query.filter_by(id=int(user_id), role="user").first()
        if not recipient:
            flash("Member tujuan tidak valid.", "danger")
            return redirect(url_for("admin_dashboard"))
        target_user_id = recipient.id
    else:
        target_user_id = current_user.id

    message = ChatMessage(user_id=target_user_id, sender_role=current_user.role, content=content)
    db.session.add(message)
    db.session.commit()
    flash("Pesan chat berhasil dikirim.", "success")
    if current_user.role == 'admin':
        return redirect(url_for("admin_dashboard", chat_user=target_user_id))
    return redirect(url_for("member_dashboard"))


@app.route("/api/chat/messages")
@login_required
def api_chat_messages():
    if current_user.role == 'admin':
        user_id = request.args.get('user_id', type=int)
        if not user_id:
            return jsonify({'messages': []})
        user = User.query.filter_by(id=user_id, role='user').first()
        if not user:
            return jsonify({'messages': []}), 404
        messages = ChatMessage.query.filter_by(user_id=user.id).order_by(ChatMessage.created_at.asc()).all()
    else:
        messages = ChatMessage.query.filter_by(user_id=current_user.id).order_by(ChatMessage.created_at.asc()).all()

    return jsonify({
        'messages': [
            {
                'id': msg.id,
                'sender_role': msg.sender_role,
                'sender_name': 'Admin' if msg.sender_role == 'admin' else current_user.username if current_user.role != 'admin' else msg.user.username,
                'content': msg.content,
                'created_at': msg.created_at.strftime('%d %b %H:%M:%S'),
            }
            for msg in messages
        ]
    })


@app.route("/api/chat/send", methods=["POST"])
@login_required
def api_chat_send():
    data = request.get_json(silent=True) or {}
    content = (data.get('content') or '').strip()
    if not content:
        return jsonify({'status': 'error', 'message': 'Isi pesan tidak boleh kosong.'}), 400

    if current_user.role == 'admin':
        user_id = data.get('user_id')
        if not user_id:
            return jsonify({'status': 'error', 'message': 'Pilih member tujuan.'}), 400
        recipient = User.query.filter_by(id=int(user_id), role='user').first()
        if not recipient:
            return jsonify({'status': 'error', 'message': 'Member tujuan tidak valid.'}), 400
        target_user_id = recipient.id
    else:
        target_user_id = current_user.id

    message = ChatMessage(user_id=target_user_id, sender_role=current_user.role, content=content)
    db.session.add(message)
    db.session.commit()
    return jsonify({'status': 'success', 'message': 'Pesan chat berhasil dikirim.'})


@app.route("/admin/scan")
@login_required
@role_required("admin")
def admin_scan():
    return render_template("admin_scan.html")


@app.route("/admin/memberships/<int:membership_id>/slots", methods=["POST"])
@login_required
@role_required("admin")
def add_membership_slot(membership_id):
    membership = Membership.query.get_or_404(membership_id)
    day_of_week = request.form.get("day_of_week")
    start_time = request.form.get("start_time")
    end_time = request.form.get("end_time")

    if not all([day_of_week, start_time, end_time]):
        flash("Lengkapi hari dan jam untuk slot.", "danger")
        return redirect(url_for("admin_membership_detail", membership_id=membership.id))

    slot = MembershipSlot(
        membership_id=membership.id,
        day_of_week=int(day_of_week),
        start_time=datetime.strptime(start_time, "%H:%M").time(),
        end_time=datetime.strptime(end_time, "%H:%M").time(),
    )
    db.session.add(slot)
    db.session.commit()
    flash("Slot ditambahkan.", "success")
    return redirect(url_for("admin_membership_detail", membership_id=membership.id))


@app.route("/admin/checkin/upload", methods=["POST"])
@login_required
@role_required("admin")
def upload_checkin():
    origin_membership_id = request.form.get("membership_id")
    file = request.files.get("qr_file")
    fallback_redirect = (
        url_for("admin_membership_detail", membership_id=origin_membership_id)
        if origin_membership_id
        else url_for("admin_memberships")
    )
    if not file:
        flash("Pilih file QR terlebih dahulu.", "danger")
        return redirect(fallback_redirect)

    file_bytes = np.frombuffer(file.read(), np.uint8)
    image = cv2.imdecode(file_bytes, cv2.IMREAD_COLOR)
    if image is None:
        flash("File tidak valid.", "danger")
        return redirect(fallback_redirect)

    detector = cv2.QRCodeDetector()
    data, _, _ = detector.detectAndDecode(image)
    if not data:
        flash("QR tidak berhasil dibaca.", "danger")
        return redirect(fallback_redirect)

    token = extract_token_from_payload(data)
    membership = Membership.query.filter_by(token=token).first()
    if not membership:
        flash("Membership tidak ditemukan dari QR tersebut.", "danger")
        return redirect(fallback_redirect)

    success, message = perform_checkin(membership, get_current_slot(membership))
    flash(message, "success" if success else "warning")
    return redirect(url_for("admin_membership_detail", membership_id=membership.id))


@app.route("/membership/<string:token>")
def membership_verify(token):
    membership = Membership.query.filter_by(token=token).first_or_404()
    now = datetime.now(tz=DEFAULT_TZ)
    current_slot = get_current_slot(membership, now)
    return render_template(
        "membership_public.html",
        membership=membership,
        current_slot=current_slot,
        now=now,
    )


@app.route("/membership/<string:token>/checkin", methods=["POST"])
def membership_checkin(token):
    membership = Membership.query.filter_by(token=token).first_or_404()
    current_slot = get_current_slot(membership)
    success, message = perform_checkin(membership, current_slot)
    flash(message, "success" if success else "warning")
    return redirect(url_for("membership_verify", token=token))


@app.route("/api/checkin", methods=["POST"])
@login_required
@role_required("admin")
def api_checkin():
    payload = request.get_json(silent=True) or {}
    token = extract_token_from_payload(payload.get("payload", ""))
    if not token:
        return jsonify({"status": "error", "message": "Payload tidak valid."}), 400

    membership = Membership.query.filter_by(token=token).first()
    if not membership:
        return jsonify({"status": "error", "message": "Membership tidak ditemukan."}), 404

    success, message = perform_checkin(membership, get_current_slot(membership))
    status_code = 200 if success else 409
    return jsonify({"status": "success" if success else "warning", "message": message}), status_code


def get_active_membership(user_id, today: date | None = None):
    today = today or datetime.now(tz=DEFAULT_TZ).date()
    return (
        Membership.query.filter_by(user_id=user_id, status="active")
        .filter(Membership.end_date >= today)
        .order_by(Membership.created_at.desc())
        .first()
    )


def build_user_dashboard_context():
    today = datetime.now(tz=DEFAULT_TZ).date()
    bookings = Booking.query.filter_by(user_id=current_user.id).order_by(Booking.play_date.desc(), Booking.start_time.desc()).all()
    next_booking = (
        Booking.query.filter(Booking.user_id == current_user.id, Booking.play_date >= today)
        .order_by(Booking.play_date, Booking.start_time)
        .first()
    )
    membership = get_active_membership(current_user.id)
    total_bookings = len(bookings)
    booking_counts = {
        'pending': Booking.query.filter_by(user_id=current_user.id, status='pending').count(),
        'approved': Booking.query.filter_by(user_id=current_user.id, status='approved').count(),
        'done': Booking.query.filter_by(user_id=current_user.id, status='done').count(),
        'rejected': Booking.query.filter_by(user_id=current_user.id, status='rejected').count(),
    }

    first_day = today.replace(day=1)
    start_offset = first_day.weekday()
    calendar_start = first_day - timedelta(days=start_offset)
    if first_day.month == 12:
        next_month = first_day.replace(year=first_day.year + 1, month=1, day=1)
    else:
        next_month = first_day.replace(month=first_day.month + 1, day=1)
    last_day = next_month - timedelta(days=1)
    end_offset = 6 - last_day.weekday()
    calendar_end = last_day + timedelta(days=end_offset)

    month_calendar = []
    current_week = []
    day = calendar_start
    while day <= calendar_end:
        day_bookings = Booking.query.filter(Booking.play_date == day).order_by(Booking.start_time).all()
        current_week.append({
            'date': day,
            'day_number': day.day,
            'is_current_month': day.month == today.month,
            'is_today': day == today,
            'booking_count': len(day_bookings),
            'bookings': day_bookings,
        })
        if len(current_week) == 7:
            month_calendar.append(current_week)
            current_week = []
        day += timedelta(days=1)

    days_left = None
    if membership:
        days_left = (membership.end_date - today).days

    chat_messages = ChatMessage.query.filter_by(user_id=current_user.id).order_by(ChatMessage.created_at.asc()).all()

    return {
        'bookings': bookings,
        'membership': membership,
        'next_booking': next_booking,
        'total_bookings': total_bookings,
        'booking_counts': booking_counts,
        'days_left': days_left,
        'month_calendar': month_calendar,
        'month_label': first_day.strftime('%B %Y'),
        'today': today,
        'chat_messages': chat_messages,
    }


@app.route("/user")
@login_required
def user_dashboard():
    return redirect(url_for("dashboard"))


@app.route("/visitor")
@login_required
def visitor_dashboard():
    ctx = build_user_dashboard_context()
    return render_template("visitor_dashboard.html", **ctx)


@app.route("/member")
@login_required
def member_dashboard():
    ctx = build_user_dashboard_context()
    if not ctx['membership']:
        return redirect(url_for('visitor_dashboard'))
    return render_template("member_dashboard.html", **ctx)


@app.route("/book", methods=["POST"])
@login_required
def create_booking():
    customer_name = request.form.get("customer_name", "").strip()
    phone = request.form.get("phone", "").strip()
    play_date = request.form.get("play_date")
    start_time = request.form.get("start_time")
    end_time = request.form.get("end_time")
    notes = request.form.get("notes", "").strip()

    if not all([customer_name, phone, play_date, start_time, end_time]):
        flash("Semua field wajib diisi.", "danger")
        return redirect(url_for("dashboard"))

    booking = Booking(
        user_id=current_user.id,
        customer_name=customer_name,
        phone=phone,
        play_date=datetime.strptime(play_date, "%Y-%m-%d").date(),
        start_time=datetime.strptime(start_time, "%H:%M").time(),
        end_time=datetime.strptime(end_time, "%H:%M").time(),
        notes=notes,
    )
    db.session.add(booking)
    db.session.commit()
    flash("Booking berhasil ditambahkan.", "success")
    return redirect(url_for("dashboard"))


@app.cli.command("init-db")
def init_db():
    """Inisialisasi basis data dan buat akun admin default."""
    db.create_all()

    if not User.query.filter_by(username="admin").first():
        admin = User(username="admin", role="admin")
        admin.set_password("admin123")
        db.session.add(admin)
        db.session.commit()
        click.echo("Admin default dibuat: admin / admin123")
    else:
        click.echo("Admin sudah ada, lewati pembuatan.")


@app.route("/membership/purchase", methods=["POST"])
@login_required
def user_purchase_membership():
    """Fungsi agar member bisa mengaktifkan paket membership sendiri dari dashboard."""
    # Keamanan dasar: cegah admin membeli paket untuk dirinya sendiri
    if current_user.role == "admin":
        flash("Admin tidak perlu membeli paket membership.", "warning")
        return redirect(url_for("admin_dashboard"))

    package_name = request.form.get("package_name")
    price = request.form.get("price", 0)
    
    if not package_name:
        flash("Silakan pilih paket membership yang tersedia.", "danger")
        return redirect(url_for("dashboard"))

    # Hitung masa berlaku otomatis (30 hari kalender sejak hari aktif)
    import datetime as dt
    start_date = dt.date.today()
    end_date = start_date + dt.timedelta(days=30)

    # Buat token enkripsi unik sebagai isi data dari QR Code
    token = str(uuid.uuid4())

    # Inisialisasi data membership baru ke model Database
    # Status diset langsung 'active' agar member bisa langsung memakainya seolah-olah tanpa campur tangan admin
    new_membership = Membership(
        user_id=current_user.id,
        package_name=package_name,
        price=float(price),
        start_date=start_date,
        end_date=end_date,
        token=token,
        status="active" 
    )

    # Generate gambar fisik QR Code secara terprogram di server lokal
    qr_img = qrcode.make(token)
    qr_filename = f"qr_{token}.png"
    qr_path = os.path.join(app.config["QR_FOLDER"], qr_filename)
    qr_img.save(qr_path)

    # Simpan transaksi ke SQLite database
    db.session.add(new_membership)
    db.session.commit()

    flash(f"Aktivasi Berhasil! Paket {package_name} Anda telah aktif selama 30 hari.", "success")
    return redirect(url_for("dashboard"))

if __name__ == "__main__":
    app.run(debug=True)
