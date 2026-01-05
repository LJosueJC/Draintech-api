from flask import Flask, request, jsonify
from flask_cors import CORS
import firebase_admin
from firebase_admin import credentials, db, messaging
import os
import json
import datetime

app = Flask(__name__)
CORS(app)

MAX_REGISTROS = 25

# ─────────────────────────────────────────────
# 🔥 Inicialización de Firebase
# ─────────────────────────────────────────────
cred_json = os.getenv("FIREBASE_CRED")
firebase_db_url = os.getenv("FIREBASE_DB")

if cred_json and firebase_db_url:
    cred_dict = json.loads(cred_json)
    cred = credentials.Certificate(cred_dict)
    firebase_admin.initialize_app(cred, {
        "databaseURL": firebase_db_url
    })
    print("✅ Firebase inicializado correctamente")
else:
    print("❌ Variables FIREBASE no configuradas")

# ─────────────────────────────────────────────
# 🔔 Notificaciones FCM
# ─────────────────────────────────────────────
def enviar_notificacion_fcm(topic, title, body):
    try:
        topic = topic.replace(":", "_")
        message = messaging.Message(
            notification=messaging.Notification(
                title=title,
                body=body
            ),
            topic=topic
        )
        messaging.send(message)
        return True
    except Exception as e:
        print("❌ Error FCM:", e)
        return False

# ─────────────────────────────────────────────
# 📊 Nivel de canastilla
# ─────────────────────────────────────────────
def get_tier(nivel):
    if nivel >= 100: return 100
    if nivel >= 90: return 90
    if nivel >= 80: return 80
    if nivel >= 70: return 70
    return 0

# ─────────────────────────────────────────────
# 🧹 Limpieza de historial
# ─────────────────────────────────────────────
def limpiar_historial(ref):
    snapshot = ref.order_by_key().get()
    if snapshot and len(snapshot) > MAX_REGISTROS:
        borrar = list(snapshot.keys())[:len(snapshot) - MAX_REGISTROS]
        ref.update({k: None for k in borrar})

# ─────────────────────────────────────────────
# 🌐 HOME
# ─────────────────────────────────────────────
@app.route('/')
def home():
    return jsonify({"status": "DrainTech API V3 ONLINE"})

# ─────────────────────────────────────────────
# 📡 ESP32 → ENVÍO DE SENSORES
# ─────────────────────────────────────────────
@app.route('/api/sensores', methods=['POST'])
def recibir_sensores():
    data = request.get_json()
    mac = data.get("mac")

    if not mac:
        return jsonify({"error": "MAC requerida"}), 400

    lluvia = data.get("lluvia", 0)
    caudal = data.get("caudal", 0)
    obstruccion = data.get("obstruccion", 0)
    canastilla = data.get("canastilla", 0)
    tapaAbierta = data.get("tapaAbierta", 0)
    estadoRegistro = data.get("estadoRegistro", "DESCONOCIDO")

    timestamp = datetime.datetime.now().timestamp()

    ref_control = db.reference(f"control/{mac}")
    ref_historial = db.reference(f"historial/{mac}")

    # Guardar historial
    ref_historial.push({
        "timestamp": timestamp,
        "lluvia": int(lluvia),
        "caudal": caudal,
        "obstruccion": int(obstruccion),
        "canastilla": canastilla,
        "tapaAbierta": int(tapaAbierta),
        "estadoRegistro": estadoRegistro
    })

    limpiar_historial(ref_historial)

    # Actualizar control (ESTADO, NO COMANDO)
    ref_control.update({
        "estadoRegistro": estadoRegistro,
        "ultimoUpdate": timestamp
    })

    # ─────────── Notificaciones ───────────
    nivel = int(canastilla)
    tier_actual = get_tier(nivel)

    control = ref_control.get() or {}
    tier_anterior = control.get("tierUltimaNotificacion", 0)
    ts_ultima = control.get("timestampUltimaNotificacion", 0)

    if tier_actual > tier_anterior or (
        tier_actual > 0 and timestamp - ts_ultima > 7200
    ):
        if enviar_notificacion_fcm(
            mac,
            "Alerta DrainTech 🚨",
            f"Canastilla al {nivel}%"
        ):
            ref_control.update({
                "tierUltimaNotificacion": tier_actual,
                "timestampUltimaNotificacion": timestamp
            })

    return jsonify({"status": "ok"}), 200

# ─────────────────────────────────────────────
# 🎮 ESP32 ← COMANDO DE REGISTRO
# ─────────────────────────────────────────────
@app.route('/api/control/registro/<mac>', methods=['GET'])
def leer_comando_registro(mac):
    ref = db.reference(f"control/{mac}")
    data = ref.get() or {}

    return jsonify({
        "cmdAbrirRegistro": data.get("cmdAbrirRegistro", False),
        "estadoRegistro": data.get("estadoRegistro", "DESCONOCIDO")
    }), 200

# ─────────────────────────────────────────────
# 📱 APP → ABRIR REGISTRO
# ─────────────────────────────────────────────
@app.route('/api/control/registro/<mac>', methods=['POST'])
def enviar_comando_registro(mac):
    ref = db.reference(f"control/{mac}")
    ref.update({
        "cmdAbrirRegistro": True
    })
    return jsonify({"status": "comando enviado"}), 200

# ─────────────────────────────────────────────
# 🔄 ESP32 → LIMPIAR COMANDO
# ─────────────────────────────────────────────
@app.route('/api/control/registro/<mac>/ack', methods=['POST'])
def limpiar_comando(mac):
    ref = db.reference(f"control/{mac}")
    ref.update({
        "cmdAbrirRegistro": False
    })
    return jsonify({"status": "ack recibido"}), 200

# ─────────────────────────────────────────────
# 📊 ÚLTIMO REGISTRO
# ─────────────────────────────────────────────
@app.route('/api/sensores/<mac>', methods=['GET'])
def ultimo_registro(mac):
    ref = db.reference(f"historial/{mac}")
    data = ref.order_by_key().limit_to_last(1).get()

    if not data:
        return jsonify({"error": "Sin datos"}), 404

    return jsonify(list(data.values())[0]), 200

# ─────────────────────────────────────────────
if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 5000)))
