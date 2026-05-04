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

load_dotenv()

# ============================================================
# MODULO 1: CONFIGURACIÓN Y CONSTANTES
# ============================================================
GROQ_API_KEY    = os.getenv("GROQ_API_KEY")
WEATHER_API_KEY = os.getenv("WEATHER_API_KEY")
MODELO_LLM      = "llama-3.1-8b-instant"
URL_GROQ        = "https://api.groq.com/openai/v1/chat/completions"
SESSION_FILE    = Path(__file__).parent.parent / ".session.json"
PIDFILE         = Path(__file__).parent.parent / ".rasa_shell.pid"

LIMITE_CHITCHAT = 3
LIMITE_ERRORES  = 3

DIAS_ES = {
    0: "lunes", 1: "martes", 2: "miércoles",
    3: "jueves", 4: "viernes", 5: "sábado", 6: "domingo"
}

# ============================================================
# MODULO 2: CONECTORES DE API
# ============================================================

def consultar_clima_actual(ciudad: str) -> dict:
    """Endpoint /weather — clima en este momento."""
    url = (
        f"http://api.openweathermap.org/data/2.5/weather"
        f"?q={ciudad}&appid={WEATHER_API_KEY}&units=metric&lang=es"
    )
    try:
        response = requests.get(url, timeout=5)
        return response.json() if response.status_code == 200 else None
    except requests.exceptions.RequestException:
        return None

def consultar_pronostico_api(ciudad: str) -> dict:
    """Endpoint /forecast — próximos 5 días en intervalos de 3 horas."""
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
    """Consulta al LLM de Groq."""
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
        return None

# ============================================================
# MODULO 3: PROCESAMIENTO DE DATOS
# ============================================================

def formatear_clima_actual(data: dict) -> str:
    """Formatea la respuesta del endpoint /weather."""
    try:
        temp      = data["main"]["temp"]
        sensacion = data["main"]["feels_like"]
        humedad   = data["main"]["humidity"]
        desc      = data["weather"][0]["description"]
        viento    = round(data["wind"]["speed"] * 3.6, 1)
        return (
            f"Ahora mismo: {temp:.1f}°C (sensación {sensacion:.1f}°C), "
            f"humedad {humedad}%, viento {viento}km/h, {desc}."
        )
    except (KeyError, TypeError):
        return "No se pudo obtener el clima actual."

def formatear_pronostico(data: dict) -> str:
    """Agrupa por fecha e incluye el nombre del día para que el LLM
    pueda resolver 'mañana', 'el miércoles', etc. correctamente."""
    items = data.get("list", [])
    if not items:
        return "Sin datos de pronóstico disponibles."

    fechas: Dict[str, list] = {}
    for item in items:
        fecha = item.get("dt_txt", "").split(" ")[0]
        if fecha:
            fechas.setdefault(fecha, []).append(item)

    resumen = ""
    for fecha, registros in list(fechas.items())[:5]:
        try:
            fecha_dt   = datetime.date.fromisoformat(fecha)
            nombre_dia = DIAS_ES[fecha_dt.weekday()]
            temps      = [r["main"]["temp"] for r in registros]
            vientos    = [r["wind"]["speed"] for r in registros]
            desc       = registros[len(registros) // 2]["weather"][0]["description"]
            resumen += (
                f"- {nombre_dia} {fecha}: "
                f"Min {min(temps):.1f}°C, Max {max(temps):.1f}°C, "
                f"Viento {round(sum(vientos)/len(vientos)*3.6, 1)}km/h, {desc}\n"
            )
        except (KeyError, IndexError, ValueError, ZeroDivisionError):
            continue

    return resumen if resumen else "Sin datos de pronóstico disponibles."

# ============================================================
# MODULO 4: HELPERS DE SESIÓN
# ============================================================

def escribir_sesion(motivo_stop: str, tracker: Tracker) -> None:
    """
    Actualiza session.json con los contadores finales y el motivo del stop.
    Luego mata el proceso rasa shell usando el PID guardado por main.py,
    para que la terminal se cierre en lugar de reiniciar la conversación.
    """
    try:
        datos = {}
        if SESSION_FILE.exists():
            datos = json.loads(SESSION_FILE.read_text(encoding="utf-8"))

        datos["motivo_stop"]       = motivo_stop
        datos["contador_chitchat"] = tracker.get_slot("contador_chitchat") or 0
        datos["contador_errores"]  = tracker.get_slot("contador_errores") or 0

        SESSION_FILE.write_text(
            json.dumps(datos, ensure_ascii=False),
            encoding="utf-8"
        )
    except (OSError, json.JSONDecodeError):
        pass

    # Matar el proceso rasa shell para que main.py pueda continuar.
    # main.py guardó el PID en .rasa_shell.pid al lanzar el subproceso.
    try:
        if PIDFILE.exists():
            pid = int(PIDFILE.read_text().strip())
            os.kill(pid, signal.SIGTERM)
            PIDFILE.unlink(missing_ok=True)
    except (OSError, ValueError, ProcessLookupError):
        pass  # El proceso ya terminó o el PID es inválido

# ============================================================
# MODULO 5: ACCIONES DE RASA
# ============================================================

class ActionGetWeather(Action):
    def name(self) -> Text:
        return "action_get_weather"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: Dict) -> List[Dict]:

        # 1. Identificar ciudad (entidad nueva > slot guardado)
        city = (
            next(tracker.get_latest_entity_values("city"), None)
            or tracker.get_slot("ciudad")
        )

        if not city:
            return self.gestionar_error_ciudad(dispatcher, tracker)

        # 2. Llamar a ambos endpoints
        clima_actual   = consultar_clima_actual(city)
        pronostico_raw = consultar_pronostico_api(city)

        if not clima_actual and not pronostico_raw:
            dispatcher.utter_message(
                text=f"No pude conectarme con el servicio del clima para '{city}'. "
                     f"Verificá que el nombre de la ciudad sea correcto."
            )
            return [SlotSet("ciudad", None)]

        # 3. Preparar contexto
        ahora       = datetime.datetime.now()
        fecha_hoy   = ahora.strftime("%Y-%m-%d")
        hora_actual = ahora.strftime("%H:%M")
        dia_hoy     = DIAS_ES[ahora.weekday()]

        clima_actual_texto = (
            formatear_clima_actual(clima_actual)
            if clima_actual else "Clima actual no disponible."
        )
        pronostico_texto = (
            formatear_pronostico(pronostico_raw)
            if pronostico_raw else "Pronóstico no disponible."
        )

        es_continuacion = tracker.get_slot("ciudad") is not None

        # 4. Prompt
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

        if respuesta_final:
            dispatcher.utter_message(text=respuesta_final)
        else:
            dispatcher.utter_message(
                text=f"Clima en {city}:\n{clima_actual_texto}\n\nPronóstico:\n{pronostico_texto}"
            )

        return [SlotSet("contador_errores", 0), SlotSet("ciudad", city)]

    def gestionar_error_ciudad(self, dispatcher, tracker):
        """Reintentos cuando el bot no entiende la ciudad.
        Límite independiente: LIMITE_ERRORES (3 por defecto)."""
        errores = (tracker.get_slot("contador_errores") or 0) + 1

        if errores >= LIMITE_ERRORES:
            escribir_sesion("errores", tracker)
            dispatcher.utter_message(response="utter_soporte")
            return [Restarted()]

        dispatcher.utter_message(
            text=f"No entendí la ciudad ({errores}/{LIMITE_ERRORES} intentos). "
                 f"¿Podés repetirla? Ejemplo: 'Clima en Rosario'."
        )
        return [SlotSet("contador_errores", errores)]


class ActionHandleChitchat(Action):
    def name(self) -> Text:
        return "action_handle_chitchat"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: Dict) -> List[Dict]:
        """Maneja chitchat con límite independiente (LIMITE_CHITCHAT).
        Al llegar al límite: escribe en session.json y ejecuta /stop."""
        contador = (tracker.get_slot("contador_chitchat") or 0) + 1

        if contador >= LIMITE_CHITCHAT:
            escribir_sesion("chitchat", tracker)
            dispatcher.utter_message(
                text="Has superado el límite de mensajes fuera de tema. "
                     "Tu sesión será cerrada y el acceso bloqueado."
            )
            return [Restarted()]

        prompt = (
            f"El usuario dice '{tracker.latest_message.get('text')}'. "
            f"Respondé brevemente y con amabilidad que sos un asistente "
            f"especializado en clima y no podés ayudar con ese tema. "
            f"Indicale que le quedan {LIMITE_CHITCHAT - contador} mensaje(s) "
            f"fuera de tema antes de que se cierre la sesión."
        )
        respuesta = consultar_llm_groq(prompt, temp=0.4)

        dispatcher.utter_message(
            text=respuesta or (
                f"Solo puedo ayudarte con temas climáticos. "
                f"Te quedan {LIMITE_CHITCHAT - contador} mensaje(s) fuera de tema."
            )
        )
        return [SlotSet("contador_chitchat", contador)]