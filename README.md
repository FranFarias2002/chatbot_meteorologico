# Chatbot Meteorológico

Asistente conversacional basado en **Rasa Open Source**, **Python** y **Groq (LLaMA 3)** que proporciona información meteorológica en tiempo real usando la API de OpenWeatherMap. Incluye un sistema de autenticación de usuarios con SQLite, control de sesiones y detección de comportamiento fuera de tema (chitchat).

---

## Características principales

- Consulta del clima actual y pronóstico extendido de 5 días
- Respuestas generadas por LLM (LLaMA 3 vía Groq) con contexto meteorológico
- Sistema de login y registro con contraseñas hasheadas (bcrypt)
- Control de chitchat: límite independiente por sesión con bloqueo automático
- Derivación a soporte humano tras errores reiterados (simulado)
- Base de datos local SQLite (sin servidor externo)

---

## Requisitos previos

Antes de comenzar, asegurate de tener instalado en tu sistema:

- **Python 3.10** (version estable para Rasa)
- **Git** para clonar el repositorio

---

## Instalación y configuración

### 1. Clonar el repositorio

```bash
git clone https://github.com/FranFarias2002/chatbot_meteorologico.git
cd chatbot_meteorologico
```

### 2. Crear y activar el entorno virtual

**Windows (PowerShell):**
```powershell
python -m venv .venv
Set-ExecutionPolicy -Scope Process -ExecutionPolicy RemoteSigned
.venv\Scripts\Activate.ps1
```

**Linux / macOS:**
```bash
python3 -m venv .venv
source .venv/bin/activate
```

### 3. Instalar dependencias

Con el entorno virtual activado:

```bash
pip install --upgrade pip
pip install -r requirements.txt
```

### 4. Configurar variables de entorno

Creá un archivo `.env` en la raíz del proyecto con el siguiente contenido:

```env
GROQ_API_KEY=tu_clave_groq_aqui
WEATHER_API_KEY=tu_clave_openweathermap_aqui
```

> Podés obtener tu clave de Groq en [console.groq.com](https://console.groq.com) y la de OpenWeatherMap en [openweathermap.org](https://openweathermap.org/api).  

---

## Cómo ejecutar el proyecto

Necesitás **dos terminales** con el entorno virtual activado en cada una.

### Terminal 1 — Servidor de acciones personalizadas

```bash
rasa run actions
```

Dejá esta terminal abierta. Rasa la usa para ejecutar las llamadas a las APIs de clima y Groq.

### Terminal 2 — Aplicación principal

Si es la **primera vez** que ejecutás el proyecto, entrenás el modelo primero:

```bash
rasa train
```

Luego, en lugar de `rasa shell`, usás el script principal que maneja el login:

```bash
python main.py
```

`main.py` se encarga del registro/login, verifica el estado del usuario en la base de datos y luego lanza el bot automáticamente.

---

## Estructura del proyecto

```
chatbot_meteorologico/
│
├── actions/
│   ├── actions.py          # Acciones personalizadas de Rasa (clima + Groq + control de sesión)
│   └── __init__.py
│
├── data/
│   ├── nlu.yml             # Ejemplos de entrenamiento NLU (intents y entidades)
│   ├── rules.yml           # Reglas de diálogo deterministas
│   └── stories.yml         # Flujos de conversación para entrenamiento
│
├── db.py                   # Capa de acceso a SQLite (usuarios, contraseñas, recurrentes)
├── main.py                 # Punto de entrada: login → bot → lógica post-sesión
├── domain.yml              # Intents, entidades, slots y respuestas del bot
├── config.yml              # Pipeline NLU y políticas de diálogo de Rasa
├── endpoints.yml           # URL del servidor de acciones
├── credentials.yml         # Canales de comunicación (REST habilitado)
│
├── .env                    # Claves privadas
├── usuarios.db             # Base de datos SQLite (se crea automáticamente)
├── .session.json           # Temporal de sesión (se crea/borra automáticamente)
├── requirements.txt        # Dependencias del proyecto
└── .gitignore
```

---

## Archivos ignorados por Git

Estos archivos no están en el repositorio pero se generan automáticamente:

| Archivo / Carpeta | Cómo se genera |
|---|---|
| `.venv/` | Manual: `python -m venv .venv` |
| `models/` | Automático al correr `rasa train` |
| `.rasa/` | Automático al entrenar o correr el bot |
| `__pycache__/` | Automático cuando Python compila los `.py` |
| `usuarios.db` | Automático al primer uso de `main.py` |
| `.env` | Manual: crearlo con tus claves (ver paso 4) |
| `.session.json` | Automático durante la sesión del bot |

---

## Sistema de usuarios

El bot tiene un sistema de autenticación integrado que corre antes de iniciar la conversación:

- **Registro:** nombre de usuario + contraseña (mínimo 4 caracteres, hasheada con bcrypt)
- **Login:** verifica credenciales y el estado del usuario en la base de datos
- **Bloqueo:** si un usuario supera el límite de chitchat, su acceso queda bloqueado (`bool chitchat = true`) y no puede volver a ingresar
- **Usuarios recurrentes:** si un usuario supera el límite de errores de comprensión más de una vez, se lo deriva a soporte humano

---

## Variables de configuración

Los límites de comportamiento se pueden ajustar en `actions/actions.py`:

```python
LIMITE_CHITCHAT = 3   # Mensajes fuera de tema antes del bloqueo
LIMITE_ERRORES  = 3   # Errores de no entendimiento antes de derivar a soporte
```
