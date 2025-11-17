import math
import os
import requests
import datetime, time # BARU: Untuk timestamp
from datetime import timedelta
from flask import Flask, request, jsonify, render_template
from dotenv import load_dotenv
from flask_sqlalchemy import SQLAlchemy # BARU: Impor SQLAlchemy

# --- Konfigurasi Aplikasi ---
load_dotenv()
app = Flask(__name__)

# --- Konfigurasi Database (BARU) ---
# Tentukan lokasi file database kita
# Path ini adalah "disk" khusus di Render yang datanya lebih awet
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///fdg_planner.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
# Inisialisasi database
db = SQLAlchemy(app)

# --- KONSTANTA FISIKA & API KEY ---
T_HALF_F18 = 109.77 
LAMBDA_F18 = math.log(2) / T_HALF_F18
GOOGLE_API_KEY = os.getenv("GOOGLE_MAPS_API_KEY") # (Tidak apa-apa jika ini None, kita tidak pakai)

# --- MODEL DATABASE (BARU) ---
# Ini adalah "cetakan" untuk tabel di database kita

class ProductionRun(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    timestamp = db.Column(db.DateTime, default=datetime.datetime.utcnow)

    # Input
    final_activity = db.Column(db.Float, nullable=False)
    injection_time_str = db.Column(db.String(8)) # <-- INI YANG HILANG
    travel_time = db.Column(db.Float, nullable=False)
    t_synthesis = db.Column(db.Float)
    yield_synthesis = db.Column(db.Float)
    t_qc = db.Column(db.Float)
    t_dispensing = db.Column(db.Float)

    # Hasil Aktivitas
    calculated_eob = db.Column(db.Float, nullable=False)

    # Hasil Jadwal (BARU)
    deadline_dispatch = db.Column(db.String(8)) 
    deadline_qc_finish = db.Column(db.String(8)) 
    deadline_eos = db.Column(db.String(8)) 
    deadline_eob = db.Column(db.String(8)) 

def __repr__(self):
    return f'<Run {self.id} @ {self.timestamp.strftime("%Y-%m-%d %H:%M")} - Target: {self.calculated_eob} mCi>'

# --- FUNGSI HELPER KALKULATOR ---
def calculate_initial_activity(final_activity, time_minutes):
    if time_minutes < 0:
        time_minutes = 0
    return final_activity * math.exp(LAMBDA_F18 * time_minutes)

# --- ENDPOINT (URL) KITA ---

@app.route('/')
def home():
    # Ini masih sama, menampilkan halaman utama
    return render_template('index.html')

@app.route('/calculate_production_plan')
def api_production_plan():
    try:
        # --- 1. AMBIL INPUT (DENGAN TAMBAHAN WAKTU) ---
        final_activity = request.args.get('final_activity', type=float)

        # BARU: Ambil waktu injeksi sebagai string "HH:MM"
        injection_time_str = request.args.get('injection_time') # Misal: "11:00"

        travel_time = request.args.get('travel_time', type=float, default=45.0)
        t_dispensing = request.args.get('t_dispensing', type=float, default=15.0)
        t_qc = request.args.get('t_qc', type=float, default=25.0)
        t_synthesis = request.args.get('t_synthesis', type=float, default=30.0)
        # Ambil input sebagai persen (mis: 50)
        yield_synthesis_percent = request.args.get('yield_synthesis', type=float, default=50.0)
        # Ubah jadi desimal untuk kalkulasi (mis: 0.5)
        yield_synthesis_decimal = yield_synthesis_percent / 100.0
        yield_dispensing = request.args.get('yield_dispensing', type=float, default=1.0)

        # Validasi input wajib
        if not all([final_activity, injection_time_str]):
            return jsonify({"error": "Parameter 'final_activity' dan 'injection_time' wajib diisi."}), 400

        # --- 2. KALKULASI AKTIVITAS (Sama seperti sebelumnya) ---
        A_dispatch = calculate_initial_activity(final_activity, travel_time)
        A_pre_dispensing_yield_corrected = A_dispatch / yield_dispensing
        A_pre_dispensing = calculate_initial_activity(A_pre_dispensing_yield_corrected, t_dispensing)
        A_post_synthesis = calculate_initial_activity(A_pre_dispensing, t_qc)
        A_eob_yield_corrected = A_post_synthesis / yield_synthesis_decimal
        A_EOB_TARGET = calculate_initial_activity(A_eob_yield_corrected, t_synthesis)

        # --- 3. KALKULASI JADWAL (BARU!) ---

        # Ubah string "HH:MM" menjadi objek 'time'
        t_inj = datetime.datetime.strptime(injection_time_str, '%H:%M').time()

        # Buat 'datetime' dummy hari ini untuk perhitungan, lalu kurangi
        # Kita pakai 'today' hanya sebagai basis, tanggalnya tidak penting
        today = datetime.date.today()
        dt_injection = datetime.datetime.combine(today, t_inj)

        # Hitung mundur jadwal
        dt_deadline_dispatch = dt_injection - timedelta(minutes=travel_time)
        dt_deadline_qc_finish = dt_deadline_dispatch - timedelta(minutes=t_dispensing)
        dt_deadline_eos = dt_deadline_qc_finish - timedelta(minutes=t_qc)
        dt_deadline_eob = dt_deadline_eos - timedelta(minutes=t_synthesis)

        # Ubah kembali ke format string "HH:MM"
        str_dl_dispatch = dt_deadline_dispatch.strftime('%H:%M')
        str_dl_qc_finish = dt_deadline_qc_finish.strftime('%H:%M')
        str_dl_eos = dt_deadline_eos.strftime('%H:%M')
        str_dl_eob = dt_deadline_eob.strftime('%H:%M')

        # --- 4. SIMPAN KE DATABASE (Versi upgrade) ---
        new_run = ProductionRun(
            final_activity=final_activity,
            injection_time_str=injection_time_str, # Simpan input
            travel_time=travel_time,
            t_synthesis=t_synthesis,
            yield_synthesis=yield_synthesis_percent,
            t_qc=t_qc,
            t_dispensing=t_dispensing,
            calculated_eob=round(A_EOB_TARGET, 2),

            # Simpan hasil jadwal
            deadline_dispatch=str_dl_dispatch,
            deadline_qc_finish=str_dl_qc_finish,
            deadline_eos=str_dl_eos,
            deadline_eob=str_dl_eob
        )
        db.session.add(new_run)
        db.session.commit()

        # --- 5. KEMBALIKAN HASIL JSON (Versi upgrade) ---
        return jsonify({
            "status": "Perhitungan Berhasil dan TERSIMPAN",
            "database_record_id": new_run.id,

            # Data Aktivitas
            "RINGKASAN_TARGET_PRODUKSI": {
                "TARGET_EOB": f"{round(A_EOB_TARGET, 2)} mCi"
            },
            # Data Jadwal (BARU)
            "RINGKASAN_JADWAL": {
                "WAKTU_INJEKSI": injection_time_str,
                "DEADLINE_PENGIRIMAN": str_dl_dispatch,
                "DEADLINE_SELESAI_QC": str_dl_qc_finish,
                "DEADLINE_SELESAI_SINTESIS_EOS": str_dl_eos,
                "DEADLINE_EOB": str_dl_eob
            },

            # Data Rincian (untuk frontend)
            "rincian_aktivitas": {
                "A_FINAL_DI_RS": final_activity,
                "A_SAAT_PENGIRIMAN": round(A_dispatch, 2),
                "A_SELESAI_QC": round(A_pre_dispensing, 2),
                "A_SELESAI_SINTESIS_EOS": round(A_post_synthesis, 2)
            }
        })

    except Exception as e:
        db.session.rollback() 
        return jsonify({"error": f"Terjadi kesalahan server: {str(e)}"}), 500

# --- ENDPOINT BARU UNTUK MELIHAT RIWAYAT ---
@app.route('/history')
def history():
    # 1. Ambil semua data dari tabel ProductionRun
    # 2. Urutkan dari yang paling baru (descending)
    runs = ProductionRun.query.order_by(ProductionRun.timestamp.desc()).all()
    
    # 3. Kirim data 'runs' ke file HTML baru bernama 'history.html'
    return render_template('history.html', runs=runs)


# --- FUNGSI UNTUK MENJALANKAN SERVER ---
# ... (Di bawah class ProductionRun) ...

if __name__ == '__main__':
    # Pindahkan ke sini
    with app.app_context():
        db.create_all() 
    app.run(debug=True)