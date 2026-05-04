"""
╔══════════════════════════════════════════════════════════════╗
║                        actions.py                            ║
║         Acciones personalizadas del Chatbot Meteorológico    ║
╠══════════════════════════════════════════════════════════════╣
║ Este archivo es el "cerebro ejecutivo" del bot. Rasa lo      ║
║ llama cuando necesita hacer algo más que responder con texto  ║
║ fijo: consultar una API, tomar decisiones, guardar datos.     ║
║                                                              ║
║ Está organizado en 5 módulos:                                ║
║   1. Configuración y constantes                              ║
║   2. Conectores de API (clima + LLM)                         ║
║   3. Procesamiento de datos (formateo)                        ║
║   4. Helpers de sesión (comunicación con main.py)             ║
║   5. Acciones de Rasa (lo que Rasa ejecuta directamente)     ║
╚══════════════════════════════════════════════════════════════╝
"""

import os
import json
import signal
import datetime
import requests
from pathlib import Path
from dotenv import load_dotenv
from typing import Any, Text, Dict, List
from rasa_sdk import Action, Tracker
from rasa_sdk.executor import CollectingDispatcher
from rasa_sdk.events import SlotSet, Restarted

# Carga las variables del archivo .env al entorno de Python.
# Sin esto, os.getenv() devolvería None para las claves de API.
load_dotenv()

# ============================================================
# MODULO 1: CONFIGURACIÓN Y CONSTANTES
# ============================================================
# Las claves sensibles se leen del archivo .env, nunca se
# escriben directamente en el código fuente (seguridad básica).
GROQ_API_KEY    = os.getenv("GROQ_API_KEY")       # Clave para el LLM (Groq/LLaMA)
WEATHER_API_KEY = os.getenv("WEATHER_API_KEY")    # Clave para OpenWeatherMap

MODELO_LLM = "llama-3.1-8b-instant"               # Modelo de lenguaje a usar en Groq
URL_GROQ   = "https://api.groq.com/openai/v1/chat/completions"

# Rutas a los archivos temporales de comunicación con main.py.
# __file__ es este archivo (actions.py), .parent es la carpeta /actions,
# .parent.parent sube un nivel más a la raíz del proyecto.
SESSION_FILE = Path(__file__).parent.parent / ".session.json"  # datos de sesión
PIDFILE      = Path(__file__).parent.parent / ".rasa_shell.pid" # PID del proceso rasa shell

# Límites independientes para cada tipo de comportamiento.
# Se pueden cambiar aquí sin tocar el resto del código.
LIMITE_CHITCHAT = 3   # mensajes fuera de tema permitidos por sesión
LIMITE_ERRORES  = 3   # errores de ciudad permitidos antes de derivar

# Diccionario para traducir el número de día de la semana (0=lunes)
# al nombre en español. Lo usamos al formatear el pronóstico.
DIAS_ES = {
    0: "lunes", 1: "martes", 2: "miércoles",
    3: "jueves", 4: "viernes", 5: "sábado", 6: "domingo"
}


# ============================================================
# MODULO 2: CONECTORES DE API
# ============================================================

def consultar_clima_actual(ciudad: str) -> dict:
    """
    Llama al endpoint /weather de OpenWeatherMap.

    Este endpoint devuelve el clima EN ESTE EXACTO MOMENTO.
    Es diferente de /forecast, que solo tiene datos futuros
    (a partir del próximo intervalo de 3 horas). Por eso usamos
    DOS endpoints distintos según lo que pregunta el usuario.

    Retorna: dict con datos del clima, o None si falla la conexión.
    """
    url = (
        f"http://api.openweathermap.org/data/2.5/weather"
        f"?q={ciudad}&appid={WEATHER_API_KEY}&units=metric&lang=es"
        # units=metric → temperaturas en °C
        # lang=es      → descripciones en español ("lluvia ligera", etc.)
    )
    try:
        response = requests.get(url, timeout=5)  # timeout: no esperar más de 5 segundos
        return response.json() if response.status_code == 200 else None
    except requests.exceptions.RequestException:
        return None  # cualquier error de red devuelve None sin romper el bot


def consultar_pronostico_api(ciudad: str) -> dict:
    """
    Llama al endpoint /forecast de OpenWeatherMap.

    Devuelve el pronóstico de los próximos 5 días en intervalos
    de 3 horas (40 registros en total). Lo usamos para responder
    preguntas sobre días futuros ('mañana', 'el jueves', etc.).
    """
    url = (
        f"http://api.openweathermap.org/data/2.5/forecast"
        f"?q={ciudad}&appid={WEATHER_API_KEY}&units=metric&lang=es"
    )
    try:
        response = requests.get(url, timeout=5)
        return response.json() if response.status_code == 200 else None
    except requests.exceptions.RequestException:
        return None


def consultar_llm_groq(prompt: str, temp: float = 0.2) -> str:
    """
    Envía un prompt al LLM (LLaMA 3 vía Groq) y devuelve la respuesta.

    El LLM es quien genera el texto final que ve el usuario. Recibe
    los datos del clima ya formateados y los convierte en lenguaje natural.

    Parámetros:
        prompt: texto de instrucción con los datos del clima incluidos
        temp:   temperatura del modelo (0=determinista, 1=creativo)
                Usamos 0.2 para respuestas precisas y 0.4 para chitchat.

    Retorna: string con la respuesta, o None si falla la llamada.
    """
    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": MODELO_LLM,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": temp
    }
    try:
        res = requests.post(URL_GROQ, headers=headers, json=payload, timeout=8).json()
        return res["choices"][0]["message"]["content"]
    except Exception:
        return None  # si Groq falla, el bot responde con datos crudos como fallback


# ============================================================
# MODULO 3: PROCESAMIENTO DE DATOS
# ============================================================

def formatear_clima_actual(data: dict) -> str:
    """
    Convierte el JSON de /weather en una línea de texto legible.

    El JSON crudo tiene decenas de campos. Extraemos solo los
    relevantes y los ponemos en formato amigable para incluir
    en el prompt del LLM.

    Ejemplo de salida:
        "Ahora mismo: 13.0°C (sensación 11.2°C), humedad 48%, viento 8.0km/h, cielo claro."
    """
    try:
        temp      = data["main"]["temp"]
        sensacion = data["main"]["feels_like"]
        humedad   = data["main"]["humidity"]
        desc      = data["weather"][0]["description"]
        viento    = round(data["wind"]["speed"] * 3.6, 1)  # m/s → km/h (×3.6)
        return (
            f"Ahora mismo: {temp:.1f}°C (sensación {sensacion:.1f}°C), "
            f"humedad {humedad}%, viento {viento}km/h, {desc}."
        )
    except (KeyError, TypeError):
        return "No se pudo obtener el clima actual."


def formatear_pronostico(data: dict) -> str:
    """
    Convierte el JSON de /forecast en un bloque de texto con un
    resumen por día para los próximos 5 días.

    Estrategia: agrupa los 40 registros (cada 3hs) por fecha,
    luego calcula min/max/promedio de cada día. Esto es más preciso
    que el método anterior de saltar de 8 en 8, que dependía de
    que el primer registro fuese exactamente a las 00:00hs.

    También agrega el nombre del día de la semana en cada línea
    para que el LLM pueda calcular correctamente qué día es
    'mañana' o 'el miércoles' sin confundirse con fechas.

    Ejemplo de salida:
        "- sábado 2026-05-02: Min 7.0°C, Max 19.0°C, Viento 12.5km/h, cielo claro"
        "- domingo 2026-05-03: Min 9.0°C, Max 18.0°C, Viento 8.0km/h, lluvia ligera"
    """
    items = data.get("list", [])
    if not items:
        return "Sin datos de pronóstico disponibles."

    # Paso 1: agrupar todos los registros por fecha (YYYY-MM-DD)
    fechas: Dict[str, list] = {}
    for item in items:
        fecha = item.get("dt_txt", "").split(" ")[0]  # "2026-05-03 12:00:00" → "2026-05-03"
        if fecha:
            fechas.setdefault(fecha, []).append(item)

    resumen = ""
    # Paso 2: para cada fecha, calcular un resumen representativo
    for fecha, registros in list(fechas.items())[:5]:  # máximo 5 días
        try:
            fecha_dt   = datetime.date.fromisoformat(fecha)
            nombre_dia = DIAS_ES[fecha_dt.weekday()]  # 0=lunes, ..., 6=domingo

            temps   = [r["main"]["temp"] for r in registros]
            vientos = [r["wind"]["speed"] for r in registros]
            # La descripción del registro del mediodía es la más representativa del día
            desc    = registros[len(registros) // 2]["weather"][0]["description"]

            resumen += (
                f"- {nombre_dia} {fecha}: "
                f"Min {min(temps):.1f}°C, Max {max(temps):.1f}°C, "
                f"Viento {round(sum(vientos)/len(vientos)*3.6, 1)}km/h, {desc}\n"
            )
        except (KeyError, IndexError, ValueError, ZeroDivisionError):
            continue  # si un día falla, continuamos con el siguiente

    return resumen if resumen else "Sin datos de pronóstico disponibles."


# ============================================================
# MODULO 4: HELPERS DE SESIÓN
# ============================================================

def escribir_sesion(motivo_stop: str, tracker: Tracker) -> None:
    """
    Punto de comunicación entre actions.py y main.py.

    Cuando el bot detecta que se superó un límite (chitchat o errores),
    esta función hace DOS cosas:

    1. Escribe en .session.json el motivo del cierre y los contadores
       finales. main.py leerá este archivo cuando el proceso termine
       para saber qué acción tomar (bloquear usuario, registrar recurrente, etc.)

    2. Mata el proceso rasa shell usando el PID guardado por main.py.
       Esto es necesario porque Restarted() solo reinicia la conversación
       dentro de Rasa, pero no cierra la terminal. Al matar el proceso,
       main.py detecta que el subproceso terminó y continúa su flujo.
    """
    # --- Paso 1: escribir el resultado en session.json ---
    try:
        datos = {}
        if SESSION_FILE.exists():
            datos = json.loads(SESSION_FILE.read_text(encoding="utf-8"))

        datos["motivo_stop"]       = motivo_stop  # "chitchat" o "errores"
        datos["contador_chitchat"] = tracker.get_slot("contador_chitchat") or 0
        datos["contador_errores"]  = tracker.get_slot("contador_errores") or 0

        SESSION_FILE.write_text(
            json.dumps(datos, ensure_ascii=False),
            encoding="utf-8"
        )
    except (OSError, json.JSONDecodeError):
        pass  # si falla la escritura, main.py tratará la sesión como normal

    # --- Paso 2: terminar el proceso rasa shell ---
    try:
        if PIDFILE.exists():
            pid = int(PIDFILE.read_text().strip())
            os.kill(pid, signal.SIGTERM)  # SIGTERM: cierre limpio (no SIGKILL)
            PIDFILE.unlink(missing_ok=True)
    except (OSError, ValueError, ProcessLookupError):
        pass  # el proceso ya terminó o el PID es inválido


# ============================================================
# MODULO 5: ACCIONES DE RASA
# ============================================================
# Las clases de este módulo son las que Rasa invoca directamente.
# Cada clase representa una "acción" declarada en domain.yml.
# El método name() debe coincidir exactamente con el nombre en domain.yml.

class ActionGetWeather(Action):
    """
    Acción principal del bot: obtiene el clima y genera una respuesta.

    Rasa la ejecuta cuando detecta el intent 'consultar_clima'.
    Está definida en domain.yml como 'action_get_weather'.

    Flujo interno:
        1. Identificar la ciudad (mensaje actual → slot guardado)
        2. Llamar a los dos endpoints de clima
        3. Formatear los datos para el prompt
        4. Pedir al LLM que genere la respuesta final
        5. Enviar la respuesta al usuario y actualizar los slots
    """

    def name(self) -> Text:
        # Este string debe coincidir exactamente con domain.yml
        return "action_get_weather"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: Dict) -> List[Dict]:

        # ── Paso 1: Identificar ciudad ──────────────────────────────
        # Primero busca si el usuario mencionó una ciudad en ESTE mensaje
        # (entidad "city" detectada por el NLU).
        # Si no, usa la ciudad guardada en el slot de la sesión anterior
        # (así funciona "y hoy?" sin necesidad de repetir la ciudad).
        city = (
            next(tracker.get_latest_entity_values("city"), None)
            or tracker.get_slot("ciudad")
        )

        if not city:
            # No hay ciudad ni en el mensaje ni en memoria → reintento
            return self.gestionar_error_ciudad(dispatcher, tracker)

        # ── Paso 2: Obtener datos del clima ─────────────────────────
        # Llamamos a AMBOS endpoints porque sirven propósitos distintos:
        # /weather → temperatura actual (ahora mismo)
        # /forecast → próximos 5 días (para preguntas sobre el futuro)
        clima_actual   = consultar_clima_actual(city)
        pronostico_raw = consultar_pronostico_api(city)

        if not clima_actual and not pronostico_raw:
            # Ambos fallaron: ciudad inválida o sin conexión
            dispatcher.utter_message(
                text=f"No pude conectarme con el servicio del clima para '{city}'. "
                     f"Verificá que el nombre de la ciudad sea correcto."
            )
            return [SlotSet("ciudad", None)]  # limpiamos el slot para el próximo intento

        # ── Paso 3: Preparar el contexto para el LLM ────────────────
        ahora       = datetime.datetime.now()
        fecha_hoy   = ahora.strftime("%Y-%m-%d")
        hora_actual = ahora.strftime("%H:%M")
        dia_hoy     = DIAS_ES[ahora.weekday()]

        # Convertir los JSON crudos de las APIs a texto legible
        clima_actual_texto = (
            formatear_clima_actual(clima_actual)
            if clima_actual else "Clima actual no disponible."
        )
        pronostico_texto = (
            formatear_pronostico(pronostico_raw)
            if pronostico_raw else "Pronóstico no disponible."
        )

        # es_continuacion determina si el LLM debe saludar o no.
        # Si el slot ya tiene ciudad, el usuario está en medio de la conversación.
        es_continuacion = tracker.get_slot("ciudad") is not None

        # ── Paso 4: Construir el prompt y llamar al LLM ──────────────
        # El prompt le da al LLM toda la información que necesita:
        # - qué ciudad es, qué fecha y hora es
        # - datos del clima actual
        # - pronóstico con nombres de días (para resolver "mañana", "el jueves")
        # - el mensaje exacto del usuario
        # - instrucciones sobre qué fuente usar según la pregunta
        prompt = f"""
Eres un asistente meteorológico para {city}.
Fecha y hora actual: {dia_hoy} {fecha_hoy}, {hora_actual} hs.

CLIMA AHORA MISMO (usá esto para preguntas sobre 'hoy', 'ahora', 'temperatura actual'):
{clima_actual_texto}

PRONÓSTICO PRÓXIMOS DÍAS (cada línea incluye el nombre del día de la semana):
{pronostico_texto}

MENSAJE DEL USUARIO: "{tracker.latest_message.get('text')}"

INSTRUCCIONES:
- {"NO saludes, el usuario ya está en conversación." if es_continuacion else "Saluda cordialmente."}
- Para 'ahora', 'hoy', 'temperatura actual': usá CLIMA AHORA MISMO.
- Para días futuros ('mañana', días de la semana): usá PRONÓSTICO y calculá la fecha correcta desde {dia_hoy} {fecha_hoy}.
- Sé breve. No repitas datos que el usuario no pidió.
"""

        respuesta_final = consultar_llm_groq(prompt)

        # ── Paso 5: Enviar respuesta y actualizar estado ─────────────
        if respuesta_final:
            dispatcher.utter_message(text=respuesta_final)
        else:
            # Si Groq falla, respondemos con los datos crudos (fallback)
            dispatcher.utter_message(
                text=f"Clima en {city}:\n{clima_actual_texto}\n\nPronóstico:\n{pronostico_texto}"
            )

        # SlotSet actualiza los slots en el tracker de Rasa:
        # - reiniciamos el contador de errores (consulta exitosa)
        # - guardamos la ciudad para que persista en la sesión
        return [SlotSet("contador_errores", 0), SlotSet("ciudad", city)]

    def gestionar_error_ciudad(self, dispatcher, tracker):
        """
        Maneja el caso en que el bot no puede identificar una ciudad.

        Tiene un límite de LIMITE_ERRORES intentos fallidos.
        Cada error incrementa el contador guardado en el slot.
        Al llegar al límite, registra el evento en session.json
        y deriva al soporte humano.
        """
        errores = (tracker.get_slot("contador_errores") or 0) + 1

        if errores >= LIMITE_ERRORES:
            # Límite alcanzado: notifica a main.py y deriva a soporte
            escribir_sesion("errores", tracker)
            dispatcher.utter_message(response="utter_soporte")
            return [Restarted()]  # reinicia la conversación en Rasa

        # Todavía hay intentos disponibles: pide al usuario que reintente
        dispatcher.utter_message(
            text=f"No entendí la ciudad ({errores}/{LIMITE_ERRORES} intentos). "
                 f"¿Podés repetirla? Ejemplo: 'Clima en Rosario'."
        )
        return [SlotSet("contador_errores", errores)]


class ActionHandleChitchat(Action):
    """
    Maneja mensajes fuera del tema del clima (chitchat).

    Rasa la ejecuta cuando detecta el intent 'chitchat'.
    Usa el LLM para generar una respuesta amigable que redirija
    al usuario, y le avisa cuántos mensajes le quedan antes
    de que la sesión sea bloqueada.

    Al llegar al límite:
        1. Llama a escribir_sesion("chitchat") → registra en session.json
           y mata el proceso rasa shell.
        2. main.py detecta el cierre y activa el bool en la BD.
        3. El usuario no puede volver a iniciar sesión.
    """

    def name(self) -> Text:
        return "action_handle_chitchat"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: Dict) -> List[Dict]:

        # Incrementar el contador independiente de chitchat
        contador = (tracker.get_slot("contador_chitchat") or 0) + 1

        if contador >= LIMITE_CHITCHAT:
            # Límite alcanzado: notificar, registrar y cerrar sesión
            escribir_sesion("chitchat", tracker)
            dispatcher.utter_message(
                text="Has superado el límite de mensajes fuera de tema. "
                     "Tu sesión será cerrada y el acceso bloqueado."
            )
            return [Restarted()]

        # Todavía hay intentos disponibles: responder con el LLM
        # Le pedimos al LLM que informe cuántos mensajes quedan (LIMITE - usado)
        prompt = (
            f"El usuario dice '{tracker.latest_message.get('text')}'. "
            f"Respondé brevemente y con amabilidad que sos un asistente "
            f"especializado en clima y no podés ayudar con ese tema. "
            f"Indicale que le quedan {LIMITE_CHITCHAT - contador} mensaje(s) "
            f"fuera de tema antes de que se cierre la sesión."
        )
        respuesta = consultar_llm_groq(prompt, temp=0.4)  # temp más alta = respuesta más variada

        dispatcher.utter_message(
            text=respuesta or (
                f"Solo puedo ayudarte con temas climáticos. "
                f"Te quedan {LIMITE_CHITCHAT - contador} mensaje(s) fuera de tema."
            )
        )
        return [SlotSet("contador_chitchat", contador)]