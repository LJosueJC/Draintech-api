from flask import Flask, request, jsonify
from flask_cors import CORS
import firebase_admin
from firebase_admin import credentials, db, messaging
import os
import json
import datetime

app = Flask(__name__)
CORS(app)

# --- CONFIGURACIÓN ---
# Define aquí cuántos registros quieres guardar como máximo por dispositivo.
# Como tu app grafica los últimos 10, guardar 50 es un buen margen de seguridad.
MAX_REGISTROS = 50 

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

# --- NUEVA FUNCIÓN: Limpieza de Historial ---
def gestionar_tamano_historial(ref_historial):
    """
    Verifica la cantidad de registros y borra los más antiguos
    si superan el MAX_REGISTROS.
    """
    try:
        # Obtenemos todos los datos ordenados cronológicamente
        snapshot = ref_historial.order_by_key().get()
        
        if snapshot and len(snapshot) > MAX_REGISTROS:
            num_registros = len(snapshot)
            cantidad_a_borrar = num_registros - MAX_REGISTROS
            
            # Obtenemos las llaves (IDs) de todos los registros
            keys = list(snapshot.keys())
            
            # Seleccionamos las primeras llaves (las más antiguas)
            keys_a_borrar = keys[:cantidad_a_borrar]
            
            print(f"🧹 Limpiando historial... Borrando {cantidad_a_borrar} registros antiguos.")
            
            # Usamos un update masivo con 'None' para borrar eficientemente
            updates = {key: None for key in keys_a_borrar}
            ref_historial.update(updates)
            
    except Exception as e:
        print(f"⚠️ Error en limpieza de historial: {e}")

@app.route('/')
def home():
    return jsonify({"message": "DrainTech API V2 (Auto-Clean Enabled) Running!"})

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
        # 1. Referencias
        ref_control = db.reference(f"control/{mac}")
        ref_historial = db.reference(f"historial/{mac}")
        
        # Guardamos en HISTORIAL
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
        
        # --- NUEVO: Ejecutar limpieza después de guardar ---
        # Esto asegura que la base de datos nunca crezca infinitamente
        gestionar_tamano_historial(ref_historial)
        
        # Actualizamos CONTROL
        control_update = {
            "registroAbierto": registroAbierto,
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

        return jsonify({"status": "ok", "message": "Datos guardados y historial optimizado"}), 200
    
    except Exception as e:
        return jsonify({"error": f"Error al guardar: {e}"}), 500

# Ruta GET para leer el último registro
@app.route('/api/sensores/<mac>', methods=['GET'])
def obtener_datos(mac):
    try:
        ref = db.reference(f"historial/{mac}")
        snapshot = ref.order_by_key().limit_to_last(1).get()
        
        if snapshot:
            key = list(snapshot.keys())[0]
            return jsonify(snapshot[key])
        else:
            return jsonify({"error": "Sin datos"}), 404
    except Exception as e:
        return jsonify({"error": f"Error: {e}"}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))
