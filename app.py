from flask import Flask, request, jsonify
from flask_cors import CORS
import firebase_admin
from firebase_admin import credentials, db, messaging
import os
import json
import datetime # Importar para manejar el tiempo

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

# --- Función Auxiliar para Enviar Notificación ---
def enviar_notificacion_fcm(topic, title, body):
    """
    Envía una notificación push a un tópico de FCM.
    """
    try:
        # Sanitizar el tópico: FCM no permite ':'
        topic_sanitizado = topic.replace(":", "_")
        
        message = messaging.Message(
            notification=messaging.Notification(
                title=title,
                body=body,
            ),
            topic=topic_sanitizado
        )
        
        # Enviar el mensaje
        response = messaging.send(message)
        print(f"✅ Notificación enviada exitosamente al tópico {topic_sanitizado}: {response}")
        return True
    except Exception as e:
        print(f"❌ Error al enviar notificación FCM al tópico {topic_sanitizado}: {e}")
        return False

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
    tapaAbierta = data.get('tapaAbierta')
    registroAbierto = data.get('registroAbierto')

    if not mac:
        return jsonify({"error": "MAC no proporcionada"}), 400

    try:
        ref = db.reference(f"dispositivos/{mac}")
        
        # 1. Actualizamos Firebase con los datos del sensor
        update_data = {
            "lluvia": lluvia,
            "caudal": caudal,
            "obstruccion": obstruccion,
            "canastilla": canastilla,
            "tapaAbierta": tapaAbierta,
            "registroAbierto": registroAbierto
        }
        ref.update(update_data)
        
        # --- INICIO DE LÓGICA DE NOTIFICACIÓN ---
        try:
            canastilla_nivel = 0
            if canastilla is not None:
                try:
                    canastilla_nivel = int(canastilla)
                except ValueError:
                    print(f"Valor de canastilla no es un número: {canastilla}")
            
            # 2. Revisar si la canastilla supera el umbral
            if canastilla_nivel >= 70:
                
                # 3. Revisar cuándo fue la última notificación
                datos_dispositivo = ref.get()
                # Usar .get() con un valor default de 0 si no existe
                timestamp_ultima_notificacion = datos_dispositivo.get("timestampUltimaNotificacion", 0) 
                
                ahora = datetime.datetime.now().timestamp()
                dos_horas_en_segundos = 7200 # 2 * 60 * 60
                
                # 4. Comprobar si han pasado más de 2 horas
                if (ahora - timestamp_ultima_notificacion > dos_horas_en_segundos):
                    print(f"INFO: Umbral superado ({canastilla_nivel}%) y tiempo cumplido. Enviando notificación...")
                    
                    # 5. Enviar notificación
                    mensaje = f"La canastilla está al {canastilla_nivel}% de su capacidad, se recomienda tomar acciones."
                    if enviar_notificacion_fcm(mac, "Alerta de Canastilla 🗑️", mensaje):
                    
                        # 6. Actualizar el timestamp SOLO si la notificación fue exitosa
                        ref.update({"timestampUltimaNotificacion": ahora})
                else:
                    print(f"INFO: Umbral superado ({canastilla_nivel}%) pero dentro del límite de 2 horas. No se notifica.")
                    
        except Exception as e:
            # Si la lógica de notificación falla, lo imprimimos, pero no rompemos la ruta
            print(f"⚠️ Error en la lógica de notificación (no crítico): {e}")
        # --- FIN DE LÓGICA DE NOTIFICACIÓN ---

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

