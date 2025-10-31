from flask import Flask, request, jsonify
from flask_cors import CORS
import firebase_admin
from firebase_admin import credentials, db, messaging
import os
import json
import datetime  # Importar para manejar el tiempo

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

# --- Función Auxiliar para Lógica de Umbrales ---
def get_tier(nivel):
    """
    Determina el umbral (tier) basado en el nivel de la canastilla.
    """
    if nivel >= 100:
        return 100
    if nivel >= 90:
        return 90
    if nivel >= 80:
        return 80
    if nivel >= 70:
        return 70
    return 0 # Debajo del umbral

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
    
    # Obtener todos los datos
    lluvia = data.get('lluvia')
    caudal = data.get('caudal')
    obstruccion = data.get('obstruccion')
    canastilla = data.get('canastilla')
    tapaAbierta = data.get('tapaAbierta')
    registroAbierto = data.get('registroAbierto')

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
        
        # --- INICIO DE LÓGICA DE NOTIFICACIÓN AVANZADA ---
        try:
            canastilla_nivel = 0
            if canastilla is not None:
                try:
                    canastilla_nivel = int(canastilla)
                except ValueError:
                    print(f"Valor de canastilla no es un número: {canastilla}")
            
            current_tier = get_tier(canastilla_nivel)
            
            if current_tier > 0: # Solo nos importa si está en un umbral
                
                # 2. Revisar estado anterior de Firebase
                datos_dispositivo = ref.get()
                if not datos_dispositivo:
                    datos_dispositivo = {}
                
                last_tier = datos_dispositivo.get("tierUltimaNotificacion", 0)
                timestamp_ultima_notificacion = datos_dispositivo.get("timestampUltimaNotificacion", 0)
                ahora = datetime.datetime.now().timestamp()
                dos_horas_en_segundos = 7200 # 2 * 60 * 60
                
                notificar = False
                razon = ""

                # REGLA 1: El nivel ha SUBIDO a un nuevo umbral
                if current_tier > last_tier:
                    notificar = True
                    razon = f"REGLA 1: Nivel subió a un nuevo umbral ({current_tier}%)"
                
                # REGLA 2: El nivel está ESTABLE, pero han pasado 2 horas
                elif current_tier == last_tier and (ahora - timestamp_ultima_notificacion > dos_horas_en_segundos):
                    notificar = True
                    razon = f"REGLA 2: Recordatorio de 2 horas en umbral ({current_tier}%)"
                
                # REGLA 3: El nivel BAJÓ pero sigue en un umbral
                elif current_tier < last_tier:
                    # No notificamos, pero actualizamos el tier para REGLA 1 si vuelve a subir
                    print(f"INFO: Nivel bajó a {current_tier}%. Actualizando 'tier' sin notificar.")
                    ref.update({"tierUltimaNotificacion": current_tier})
                
                else:
                    # Mismo tier, dentro de las 2 horas
                    print(f"INFO: Umbral ({current_tier}%) estable y dentro del límite de 2 horas. No se notifica.")

                # 3. Enviar notificación si se cumple una regla
                if notificar:
                    print(f"INFO: {razon}. Enviando notificación...")
                    mensaje = f"La canastilla está al {canastilla_nivel}% de su capacidad, se recomienda tomar acciones."
                    
                    if enviar_notificacion_fcm(mac, "Alerta de Canastilla 🗑️", mensaje):
                        # Actualizar AMBOS campos solo si la notificación fue exitosa
                        ref.update({
                            "timestampUltimaNotificacion": ahora,
                            "tierUltimaNotificacion": current_tier
                        })

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
    # El puerto 10000 es comúnmente usado por Render, pero Gunicorn lo manejará
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))

