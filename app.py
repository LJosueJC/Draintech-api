from flask import Flask, request, jsonify
from flask_cors import CORS
import firebase_admin
from firebase_admin import credentials, db, messaging
import os
import json
import datetime  # Importante para el historial

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
        print(f"❌ Error al inicializar Firebase: {e}")
else:
    print("⚠️ Variables de entorno FIREBASE_CRED o FIREBASE_DB no definidas.")

# --- Función Auxiliar para Enviar Notificación ---
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
        response = messaging.send(message)
        print(f"✅ Notificación enviada exitosamente al tópico {topic_sanitizado}: {response}")
        return True
    except Exception as e:
        print(f"❌ Error al enviar notificación FCM al tópico {topic_sanitizado}: {e}")
        return False

# --- Función Auxiliar para Lógica de Umbrales ---
def get_tier(nivel):
    if nivel >= 100: return 100
    if nivel >= 90: return 90
    if nivel >= 80: return 80
    if nivel >= 70: return 70
    return 0 

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
    if not mac:
        return jsonify({"error": "MAC no proporcionada"}), 400
    
    # Obtener variables
    lluvia = data.get('lluvia')
    caudal = data.get('caudal')
    obstruccion = data.get('obstruccion')
    canastilla = data.get('canastilla')
    tapaAbierta = data.get('tapaAbierta')
    registroAbierto = data.get('registroAbierto')
    
    # Timestamp actual para el historial
    timestamp_actual = datetime.datetime.now().timestamp()

    try:
        # 1. Referencia al estado ACTUAL (Sobrescribe valores para mostrar en tiempo real)
        ref_actual = db.reference(f"dispositivos/{mac}")
        
        # 2. Referencia al HISTORIAL (Crea una lista nueva de registros)
        ref_historial = db.reference(f"historial/{mac}")
        
        # Datos para el estado actual
        update_data = {
            "lluvia": lluvia,
            "caudal": caudal,
            "obstruccion": obstruccion,
            "canastilla": canastilla,
            "tapaAbierta": tapaAbierta,
            "registroAbierto": registroAbierto,
            "ultimoUpdate": timestamp_actual 
        }
        ref_actual.update(update_data)

        # Datos para el historial (Convertimos booleanos a int para facilitar graficación si es necesario)
        # Nota: Push genera un ID único automáticamente
        historial_data = {
            "timestamp": timestamp_actual,
            "canastilla": canastilla,
            "caudal": caudal,
            "lluvia": 1 if lluvia else 0,
            "obstruccion": 1 if obstruccion else 0
        }
        ref_historial.push(historial_data)
        
        # --- LÓGICA DE NOTIFICACIONES ---
        try:
            canastilla_nivel = 0
            if canastilla is not None:
                try:
                    canastilla_nivel = int(canastilla)
                except ValueError:
                    pass
            
            current_tier = get_tier(canastilla_nivel)
            
            # Obtener estado anterior para comparar notificaciones
            datos_dispositivo = ref_actual.get() or {}
            last_tier = datos_dispositivo.get("tierUltimaNotificacion", 0)
            timestamp_ultima_notificacion = datos_dispositivo.get("timestampUltimaNotificacion", 0)
            
            dos_horas_en_segundos = 7200
            
            notificar = False
            razon = ""

            # REGLA 3: Nivel bajó
            if current_tier < last_tier:
                print(f"INFO: Nivel bajó de {last_tier}% a {current_tier}%. Reseteando tier.")
                ref_actual.update({"tierUltimaNotificacion": current_tier})
            
            # REGLA 1: Nivel subió
            elif current_tier > last_tier and current_tier > 0:
                notificar = True
                razon = f"REGLA 1: Nivel subió a un nuevo umbral ({current_tier}%)"
            
            # REGLA 2: Recordatorio por tiempo
            elif current_tier == last_tier and current_tier > 0 and (timestamp_actual - timestamp_ultima_notificacion > dos_horas_en_segundos):
                notificar = True
                razon = f"REGLA 2: Recordatorio de 2 horas en umbral ({current_tier}%)"

            if notificar:
                print(f"INFO: {razon}. Enviando notificación...")
                mensaje = f"La canastilla está al {canastilla_nivel}% de su capacidad, se recomienda tomar acciones."
                
                if enviar_notificacion_fcm(mac, "Alerta de Canastilla 🗑️", mensaje):
                    ref_actual.update({
                        "timestampUltimaNotificacion": timestamp_actual,
                        "tierUltimaNotificacion": current_tier
                    })

        except Exception as e:
            print(f"⚠️ Error en la lógica de notificación (no crítico): {e}")

        return jsonify({"status": "ok", "message": "Datos guardados y historial actualizado"}), 200
    
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
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))
