from flask import Flask, request, jsonify
from flask_cors import CORS
import firebase_admin
from firebase_admin import credentials, db
import os
import json

app = Flask(__name__)
CORS(app)

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
        print("❌ Error al inicializar Firebase:", e)
else:
    print("⚠️ Variables de entorno FIREBASE_CRED o FIREBASE_DB no definidas.")

# --- Ruta raíz ---
@app.route('/')
def home():
    return jsonify({"message": "DrainTech API + Firebase is running!"})

# --- Ruta para recibir datos desde el ESP32 ---
@app.route('/api/sensores', methods=['POST'])
def recibir_datos():
    data = request.get_json()
    if not data:
        return jsonify({"error": "Solicitud inválida, no hay JSON."}), 400

    mac = data.get('mac')
    lluvia = data.get('lluvia')
    caudal = data.get('caudal')
    obstruccion = data.get('obstruccion')
    canastilla = data.get('canastilla')
    tapa_abierta = data.get('tapaAbierta')
    registro_abierto = data.get('registroAbierto')

    if not mac:
        return jsonify({"error": "MAC no proporcionada"}), 400

    try:
        ref = db.reference(f"dispositivos/{mac}")
        ref.update({
            "lluvia": lluvia,
            "caudal": caudal,
            "obstruccion": obstruccion,
            "canastilla": canastilla,
            "tapaAbierta": tapa_abierta,
            "registroAbierto": registro_abierto
        })
        return jsonify({"status": "ok", "message": "Datos guardados en Firebase"}), 200
    except Exception as e:
        return jsonify({"error": f"Error al guardar datos: {e}"}), 500

# --- Ruta para consultar datos desde la app Android ---
@app.route('/api/sensores/<mac>', methods=['GET'])
def obtener_datos(mac):
    try:
        ref = db.reference(f"dispositivos/{mac}")
        datos = ref.get()
        if datos:
            return jsonify(datos)
        else:
            return jsonify({"error": "Dispositivo no encontrado"}), 404
    except Exception as e:
        return jsonify({"error": f"Error al leer datos: {e}"}), 500


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
