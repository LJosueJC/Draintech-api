from flask import Flask, request, jsonify
from flask_cors import CORS
import firebase_admin
from firebase_admin import credentials, db, messaging
import os
import json
import datetime

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

def get_tier(nivel):
    if nivel >= 100: return 100
    if nivel >= 90: return 90
    if nivel >= 80: return 80
    if nivel >= 70: return 70
    return 0 

@app.route('/')
def home():
    return jsonify({"message": "DrainTech API V2 (History Only) Running!"})

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
    
    timestamp_actual = datetime.datetime.now().timestamp()

    try:
        # 1. Referencia al CONTROL (Tabla pequeña, solo estado del sistema)
        # YA NO USAMOS 'dispositivos' PARA SENSORES.
        ref_control = db.reference(f"control/{mac}")
        
        # 2. Referencia al HISTORIAL (Aquí van los sensores)
        ref_historial = db.reference(f"historial/{mac}")
        
        # Guardamos en HISTORIAL (Base de datos principal)
        historial_data = {
            "timestamp": timestamp_actual,
            "canastilla": canastilla,
            "caudal": caudal,
            "lluvia": 1 if lluvia else 0,
            "obstruccion": 1 if obstruccion else 0,
            "tapaAbierta": 1 if tapaAbierta else 0,
            "registroAbierto": 1 if registroAbierto else 0
        }
        ref_historial.push(historial_data)
        
        # Actualizamos CONTROL (Solo metadatos y confirmación de estado)
        # Esto sirve para que el sistema sepa el estado actual de notificaciones
        # y confirme si el registro está abierto o cerrado.
        control_update = {
            "registroAbierto": registroAbierto, # Confirmación de estado
            "ultimoUpdate": timestamp_actual
        }
        ref_control.update(control_update)

        # --- LÓGICA DE NOTIFICACIONES ---
        try:
            canastilla_nivel = 0
            if canastilla is not None:
                try: canastilla_nivel = int(canastilla)
                except ValueError: pass
            
            current_tier = get_tier(canastilla_nivel)
            
            # Leemos el estado de notificación desde la tabla 'control'
            datos_control = ref_control.get() or {}
            last_tier = datos_control.get("tierUltimaNotificacion", 0)
            timestamp_ultima_notificacion = datos_control.get("timestampUltimaNotificacion", 0)
            
            dos_horas_en_segundos = 7200
            notificar = False
            razon = ""

            if current_tier < last_tier:
                ref_control.update({"tierUltimaNotificacion": current_tier})
            elif current_tier > last_tier and current_tier > 0:
                notificar = True
                razon = f"REGLA 1: Nivel subió ({current_tier}%)"
            elif current_tier == last_tier and current_tier > 0 and (timestamp_actual - timestamp_ultima_notificacion > dos_horas_en_segundos):
                notificar = True
                razon = f"REGLA 2: Recordatorio tiempo ({current_tier}%)"

            if notificar:
                print(f"INFO: {razon}. Enviando notificación...")
                mensaje = f"La canastilla está al {canastilla_nivel}% de su capacidad."
                
                if enviar_notificacion_fcm(mac, "Alerta de Canastilla 🗑️", mensaje):
                    ref_control.update({
                        "timestampUltimaNotificacion": timestamp_actual,
                        "tierUltimaNotificacion": current_tier
                    })

        except Exception as e:
            print(f"⚠️ Error notificación: {e}")

        return jsonify({"status": "ok", "message": "Datos guardados en historial"}), 200
    
    except Exception as e:
        return jsonify({"error": f"Error al guardar: {e}"}), 500

# Ruta GET modificada para leer del HISTORIAL (último registro)
@app.route('/api/sensores/<mac>', methods=['GET'])
def obtener_datos(mac):
    try:
        # Ahora leemos el último registro del historial
        ref = db.reference(f"historial/{mac}")
        snapshot = ref.order_by_key().limit_to_last(1).get()
        
        if snapshot:
            # Snapshot es un dict con una sola llave (el ID del push), necesitamos el valor interno
            key = list(snapshot.keys())[0]
            return jsonify(snapshot[key])
        else:
            return jsonify({"error": "Sin datos"}), 404
    except Exception as e:
        return jsonify({"error": f"Error: {e}"}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))
