from flask import Flask, request, jsonify
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

# --- Simulación temporal de base de datos ---
devices = {}

@app.route('/')
def home():
    return jsonify({"message": "DrainTech API is running!"})

# --- Ruta para recibir datos desde el ESP32 ---
@app.route('/api/sensores', methods=['POST'])
def recibir_datos():
    data = request.get_json()
    mac = data.get('mac')
    lluvia = data.get('lluvia')
    caudal = data.get('caudal')
    obstruccion = data.get('obstruccion')
    canastilla = data.get('canastilla')

    # Guardamos temporalmente (puedes luego conectar Firebase)
    devices[mac] = {
        "lluvia": lluvia,
        "caudal": caudal,
        "obstruccion": obstruccion,
        "canastilla": canastilla
    }

    return jsonify({"status": "ok", "message": "Datos recibidos"}), 200

# --- Ruta para consultar datos desde la app Android ---
@app.route('/api/sensores/<mac>', methods=['GET'])
def obtener_datos(mac):
    if mac in devices:
        return jsonify(devices[mac])
    else:
        return jsonify({"error": "Dispositivo no encontrado"}), 404

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
