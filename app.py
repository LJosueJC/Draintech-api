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

# --- Inicialización de Firebase ---
cred_json = os.getenv("FIREBASE_CRED")
firebase_db_url = os.getenv("FIREBASE_DB")

if cred_json and firebase_db_url:
    try:
        cred_dict = json.loads(cred_json)
        cred = credentials.Certificate(cred_dict)
        firebase_admin.initialize_app(cred, {
            "databaseURL": firebase_db_url
        })
        print("✅ Firebase inicializado correctamente.")
    except Exception as e:
        print(f"❌ Error al inicializar Firebase: {e}")
else:
    print("⚠️ Variables de entorno FIREBASE_CRED o FIREBASE_DB no definidas.")

# --- Funciones Auxiliares ---

def enviar_notificacion_fcm(topic, title, body):
    try:
        topic_sanitizado = topic.replace(":", "_")
        message = messaging.Message(
            notification=messaging.Notification(
                title=title,
                body=body,
            ),
            topic=topic_sanitizado
        )
        messaging.send(message)
        return True
    except Exception as e:
        print(f"❌ Error FCM: {e}")
        return False

def get_tier(nivel):
    if nivel >= 100: return 100
    if nivel >= 90: return 90
    if nivel >= 80: return 80
    if nivel >= 70: return 70
    return 0 

def gestionar_tamano_historial(ref_historial):
    try:
        snapshot = ref_historial.order_by_key().get()
        if snapshot and len(snapshot) > MAX_REGISTROS:
            keys = list(snapshot.keys())
            cantidad_a_borrar = len(keys) - MAX_REGISTROS
            keys_a_borrar = keys[:cantidad_a_borrar]
            updates = {key: None for key in keys_a_borrar}
            ref_historial.update(updates)
    except Exception as e:
        print(f"⚠️ Error limpieza: {e}")

# --- RUTAS ---

@app.route('/')
def home():
    return jsonify({"message": "DrainTech API V3 - Optimized for ESP32"})

# 1. POST: El ESP32 envía sus lecturas aquí
@app.route('/api/sensores', methods=['POST'])
def recibir_datos():
    data = request.get_json()
    if not data or 'mac' not in data:
        return jsonify({"error": "Datos incompletos"}), 400

    mac = data.get('mac')
    timestamp_actual = datetime.datetime.now().timestamp()

    try:
        ref_control = db.reference(f"control/{mac}")
        ref_historial = db.reference(f"historial/{mac}")
        
        # Guardar en Historial (para gráficas)
        historial_data = {
            "timestamp": timestamp_actual,
            "canastilla": data.get('canastilla'),
            "caudal": data.get('caudal'),
            "lluvia": 1 if data.get('lluvia') else 0,
            "obstruccion": 1 if data.get('obstruccion') else 0,
            "tapaAbierta": 1 if data.get('tapaAbierta') else 0,
            "registroAbierto": 1 if data.get('registroAbierto') else 0
        }
        ref_historial.push(historial_data)
        gestionar_tamano_historial(ref_historial)
        
        # Actualizar estado actual en CONTROL (para que la App vea lo último)
        # Importante: No sobreescribimos 'registroAbierto' si no viene en el JSON
        # para no chocar con las órdenes de la App.
        ref_control.update({
            "ultimoUpdate": timestamp_actual,
            "sensor_data": historial_data # Guardamos una copia rápida aquí
        })

        # --- LÓGICA DE NOTIFICACIONES ---
        # (Se mantiene igual que tu código original)
        canastilla_nivel = int(data.get('canastilla', 0))
        current_tier = get_tier(canastilla_nivel)
        datos_control = ref_control.get() or {}
        last_tier = datos_control.get("tierUltimaNotificacion", 0)
        
        if current_tier > last_tier and current_tier > 0:
            if enviar_notificacion_fcm(mac, "Alerta de Canastilla 🗑️", f"Nivel al {canastilla_nivel}%"):
                ref_control.update({"tierUltimaNotificacion": current_tier})

        return jsonify({"status": "ok"}), 200
    
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# 2. GET: El ESP32 consulta esta ruta para saber si debe abrir el registro
# AHORA BUSCA EN EL NODO /control/ DIRECTAMENTE
@app.route('/api/sensores/<mac>', methods=['GET'])
def obtener_control(mac):
    try:
        ref = db.reference(f"control/{mac}")
        datos = ref.get()
        
        if datos:
            # Solo devolvemos lo que el ESP32 necesita saber
            # Si registroAbierto no existe, devolvemos 0 por defecto
            estado = 1 if datos.get('registroAbierto') is True else 0
            return jsonify({"registroAbierto": estado})
        else:
            return jsonify({"registroAbierto": 0}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))
