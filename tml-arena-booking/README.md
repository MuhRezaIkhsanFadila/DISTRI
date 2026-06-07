# Pembookingan Lapangan Futsal TML Arena

Aplikasi web berbasis Flask untuk mengelola booking lapangan futsal, membership, dan verifikasi QR secara terintegrasi.

## Fitur Utama

- Open registration publik untuk member baru.
- Login menggunakan username atau email.
- Dukungan membership tipe `membership` dan `single`/`per visit`.
- Panel admin yang responsif untuk memantau booking, membership, dan attendance.
- Halaman login/register dengan tampilan panel modern dan responsif.
- QR code otomatis dibuat untuk setiap member dan dapat di-scan untuk verifikasi.
- Scanner QR berbasis kamera web untuk check-in cepat.
- Upload QR / screenshot dari membership detail sebagai alternatif check-in.
- Penyimpanan data menggunakan SQLite dan SQLAlchemy.

## Komponen Sistem

- **Client**: browser user/admin yang menampilkan UI login, dashboard, membership, dan scan QR.
- **Server**: Flask menjalankan routing, autentikasi, logika booking, dan render template Jinja.
- **Database**: SQLite (`instance/tml_arena.db`) menyimpan user, booking, membership, attendance, dan token QR.

## Alur Membership & QR

1. Admin membuat membership dan mengatur tipe, durasi, serta slot jadwal.
2. Sistem membuat token unik dan menyimpan QR di `static/qrs/`.
3. Member dapat melihat QR di dashboard atau melalui halaman publik.
4. QR memuat link verifikasi membership.
5. Admin dapat melakukan check-in kehadiran via scan kamera atau upload QR.
6. Absensi disimpan di tabel `Attendance` dengan timestamp dan status validasi.

## Cara Menjalankan

```bash
cd tml-arena-booking
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
flask --app app.py init-db
flask --app app.py run --host 0.0.0.0 --port 5000
```

Buka `http://127.0.0.1:5000` di browser.

## Catatan

- Server menggunakan `flask_login` untuk autentikasi.
- Pastikan folder `instance/` dapat ditulis oleh aplikasi.
- Jika ingin deployment produksi, disarankan mengganti SQLite ke database server seperti PostgreSQL/MySQL dan menambahkan konfigurasi secret key serta HTTPS.

## Kontak

Untuk penyesuaian tema atau fitur tambahan, edit `templates/login.html` dan `static/styles.css` untuk tampilan auth page, serta `app.py` untuk logika routing dan database.
