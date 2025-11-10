import math
import os
import requests
import datetime, time
from datetime import timedelta
from flask import Flask, request, jsonify, render_template, redirect, url_for
from dotenv import load_dotenv
from flask_sqlalchemy import SQLAlchemy

# --- KONFIGURASI APLIKASI UNIVERSAL ---
# 1. Dapatkan "basedir" (path absolut ke folder proyek ini)
basedir = os.path.abspath(os.path.dirname(__file__))

# 2. Muat file .env (jika ada)
load_dotenv(os.path.join(basedir, '.env'))

# 3. Inisialisasi Flask & beritahu di mana folder statis/template
# Ini adalah perbaikan "anti-gagal" untuk masalah TemplateNotFound
app = Flask(__name__,
            template_folder=os.path.join(basedir, 'templates'),
            static_folder=os.path.join(basedir, 'static')
            )

# --- KONFIGURASI DATABASE "PINTAR" ---
# Cek apakah kita sedang di server PythonAnywhere
if 'PYTHONANYWHERE_USERNAME' in os.environ:
    # Kita di server! Gunakan path absolut server.
    # GANTI 'hafizsurya' DENGAN USERNAME ANDA JIKA BERBEDA
    username = os.environ.get('PYTHONANYWHERE_USERNAME', 'hafizsurya')
    db_path = f'/home/{username}/fdg-planner/fdg_planner.db'
    app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///' + db_path
else:
    # Kita di lokal! Gunakan path lokal.
    app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///' + os.path.join(basedir, 'fdg_planner.db')

app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)

# --- KONSTANTA FISIKA & API KEY ---
T_HALF_F18 = 109.77
LAMBDA_F18 = math.log(2) / T_HALF_F18
GOOGLE_API_KEY = os.getenv("GOOGLE_MAPS_API_KEY")

# --- MODEL DATABASE ---
# (Ini sama seperti Tutorial 8)
class ProductionRun(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    timestamp = db.Column(db.DateTime, default=datetime.datetime.utcnow)
    
    # Input
    final_activity = db.Column(db.Float, nullable=False)
    injection_time_str = db.Column(db.String(8)) 
    travel_time = db.Column(db.Float, nullable=False)
    t_synthesis = db.Column(db.Float)
    yield_synthesis = db.Column(db.Float) # Menyimpan 50 (untuk 50%)
    t_qc = db.Column(db.Float)
    t_dispensing = db.Column(db.Float)
    
    # Hasil Aktivitas
    calculated_eob = db.Column(db.Float, nullable=False)

    # Hasil Jadwal
    deadline_dispatch = db.Column(db.String(8))
    deadline_qc_finish = db.Column(db.String(8))
    deadline_eos = db.Column(db.String(8))
    deadline_eob = db.Column(db.String(8))

    def __repr__(self):
        return f'<Run {self.id} @ {self.timestamp.strftime("%Y-%m-%d %H:%M")} - Target: {self.calculated_eob} mCi>'

# --- BUAT TABEL DATABASE ---
# (Dipindah ke luar agar bisa dijalankan di server saat startup)
with app.app_context():
    db.create_all()

# --- FUNGSI HELPER KALKULATOR ---
def calculate_initial_activity(final_activity, time_minutes):
    if time_minutes < 0:
        time_minutes = 0
    return final_activity * math.exp(LAMBDA_F18 * time_minutes)

# --- ENDPOINT (URL) KITA ---

@app.route('/')
def home():
    return render_template('index.html')

@app.route('/calculate_production_plan')
def api_production_plan():
    try:
        # --- 1. AMBIL INPUT ---
        final_activity = request.args.get('final_activity', type=float)
        injection_time_str = request.args.get('injection_time') # Misal: "11:00"
        travel_time = request.args.get('travel_time', type=float, default=45.0)
        t_dispensing = request.args.get('t_dispensing', type=float, default=15.0)
        t_qc = request.args.get('t_qc', type=float, default=25.0)
        t_synthesis = request.args.get('t_synthesis', type=float, default=30.0)
        
        # Ambil yield sebagai persen (mis: 50)
        yield_synthesis_percent = request.args.get('yield_synthesis', type=float, default=50.0)
        # Ubah jadi desimal untuk kalkulasi (mis: 0.5)
        yield_synthesis_decimal = yield_synthesis_percent / 100.0
        
        yield_dispensing = request.args.get('yield_dispensing', type=float, default=1.0) # (Kita belum tambahkan ini di UI)
        
        if not all([final_activity, injection_time_str]):
            return jsonify({"error": "Parameter 'final_activity' dan 'injection_time' wajib diisi."}), 400

        # --- 2. KALKULASI AKTIVITAS ---
        A_dispatch = calculate_initial_activity(final_activity, travel_time)
        A_pre_dispensing_yield_corrected = A_dispatch / yield_dispensing
        A_pre_dispensing = calculate_initial_activity(A_pre_dispensing_yield_corrected, t_dispensing)
        A_post_synthesis = calculate_initial_activity(A_pre_dispensing, t_qc)
        A_eob_yield_corrected = A_post_synthesis / yield_synthesis_decimal
        A_EOB_TARGET = calculate_initial_activity(A_eob_yield_corrected, t_synthesis)
        
        # --- 3. KALKULASI JADWAL ---
        t_inj = datetime.datetime.strptime(injection_time_str, '%H:%M').time()
        today = datetime.date.today()
        dt_injection = datetime.datetime.combine(today, t_inj)

        dt_deadline_dispatch = dt_injection - timedelta(minutes=travel_time)
        dt_deadline_qc_finish = dt_deadline_dispatch - timedelta(minutes=t_dispensing)
        dt_deadline_eos = dt_deadline_qc_finish - timedelta(minutes=t_qc)
        dt_deadline_eob = dt_deadline_eos - timedelta(minutes=t_synthesis)

        str_dl_dispatch = dt_deadline_dispatch.strftime('%H:%M')
        str_dl_qc_finish = dt_deadline_qc_finish.strftime('%H:%M')
        str_dl_eos = dt_deadline_eos.strftime('%H:%M')
        str_dl_eob = dt_deadline_eob.strftime('%H:%M')

        # --- 4. SIMPAN KE DATABASE ---
        new_run = ProductionRun(
            final_activity=final_activity,
            injection_time_str=injection_time_str,
            travel_time=travel_time,
            t_synthesis=t_synthesis,
            yield_synthesis=yield_synthesis_percent, # Simpan 50, bukan 0.5
            t_qc=t_qc,
            t_dispensing=t_dispensing,
            calculated_eob=round(A_EOB_TARGET, 2),
            deadline_dispatch=str_dl_dispatch,
            deadline_qc_finish=str_dl_qc_finish,
            deadline_eos=str_dl_eos,
            deadline_eob=str_dl_eob
        )
        db.session.add(new_run)
        db.session.commit()

        # --- 5. KEMBALIKAN HASIL JSON ---
        return jsonify({
            "status": "Perhitungan Berhasil dan TERSIMPAN",
            "database_record_id": new_run.id,
            "RINGKASAN_TARGET_PRODUKSI": {
                "TARGET_EOB": f"{round(A_EOB_TARGET, 2)} mCi"
            },
            "RINGKASAN_JADWAL": {
                "WAKTU_INJEKSI": injection_time_str,
                "DEADLINE_PENGIRIMAN": str_dl_dispatch,
                "DEADLINE_SELESAI_QC": str_dl_qc_finish,
                "DEADLINE_SELESAI_SINTESIS_EOS": str_dl_eos,
                "DEADLINE_EOB": str_dl_eob
            },
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

@app.route('/history')
def history():
    runs = ProductionRun.query.order_by(ProductionRun.timestamp.desc()).all()
    return render_template('history.html', runs=runs)

@app.route('/delete_run/<int:id>')
def delete_run(id):
    run_to_delete = ProductionRun.query.get_or_404(id)
    try:
        db.session.delete(run_to_delete)
        db.session.commit()
        return redirect(url_for('history'))
    except Exception as e:
        db.session.rollback()
        return f"Error saat menghapus data: {str(e)}"

# --- HANYA UNTUK RUN LOKAL ---
if __name__ == '__main__':
    # Hapus file .db lokal lama agar struktur baru diterapkan
    if os.path.exists(os.path.join(basedir, 'fdg_planner.db')):
        print("Menghapus database lokal lama...")
        os.remove(os.path.join(basedir, 'fdg_planner.db'))
        
    # Buat ulang database dengan struktur baru
    with app.app_context():
        db.create_all()
        
    print("Menjalankan server lokal di http://127.0.0.1:5000")
    app.run(debug=True)
