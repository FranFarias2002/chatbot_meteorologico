"""
╔══════════════════════════════════════════════════════════════╗
║                         main.py                              ║
║            Punto de entrada del Chatbot Meteorológico        ║
╠══════════════════════════════════════════════════════════════╣
║ Este es el script que el usuario ejecuta para usar el bot.   ║
║ Reemplaza el comando `rasa shell` porque agrega la capa de   ║
║ autenticación antes de iniciar la conversación.              ║
║                                                              ║
║ Flujo completo:                                              ║
║   1. Limpiar archivos temporales de sesiones anteriores      ║
║   2. Mostrar menú: registrarse o iniciar sesión              ║
║   3. Verificar si el usuario está bloqueado (bool chitchat)  ║
║   4. Lanzar rasa shell como subproceso                       ║
║   5. Cuando el bot termina, leer session.json                ║
║   6. Aplicar la lógica: bloquear / registrar recurrente      ║
║                                                              ║
║ Comunicación con actions.py:                                 ║
║   - main.py escribe el PID en .rasa_shell.pid                ║
║   - actions.py escribe el resultado en .session.json         ║
║   - actions.py mata el proceso usando el PID                 ║
╚══════════════════════════════════════════════════════════════╝
"""

import json
import subprocess
import sys
from pathlib import Path

import db  # módulo propio: toda la lógica de la base de datos SQLite

# ── Rutas a archivos temporales de sesión ──────────────────────
# Estos archivos se crean y eliminan automáticamente durante cada sesión.
# NO deben subirse a Git (están en .gitignore).
SESSION_FILE = Path(__file__).parent / ".session.json"   # datos del resultado de sesión
PIDFILE      = Path(__file__).parent / ".rasa_shell.pid" # PID del proceso rasa shell

# Deben coincidir con los límites definidos en actions.py
LIMITE_CHITCHAT = 3
LIMITE_ERRORES  = 3


# ============================================================
# HELPERS DE UI (consola)
# ============================================================

def limpiar_sesion() -> None:
    """
    Elimina los archivos temporales de sesiones anteriores.

    Se llama al inicio del programa para asegurarse de que no
    queden archivos de sesiones colgadas (por ejemplo, si el
    proceso fue interrumpido abruptamente con Ctrl+C).
    """
    if SESSION_FILE.exists():
        SESSION_FILE.unlink()
    if PIDFILE.exists():
        PIDFILE.unlink()


def leer_sesion() -> dict:
    """
    Lee el archivo .session.json escrito por actions.py.

    Devuelve un diccionario con:
        - motivo_stop: "chitchat", "errores" o None (salida normal)
        - contador_chitchat: número de mensajes fuera de tema
        - contador_errores: número de errores de ciudad
        - usuario: nombre del usuario de la sesión

    Si el archivo no existe o está corrupto, devuelve un dict vacío
    (la sesión se trata como finalizada normalmente).
    """
    if not SESSION_FILE.exists():
        return {}
    try:
        return json.loads(SESSION_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def mostrar_separador() -> None:
    """Imprime una línea divisoria para mejorar la legibilidad del menú."""
    print("\n" + "─" * 50)


# ============================================================
# FLUJO DE AUTENTICACIÓN
# ============================================================

def menu_principal() -> tuple[str, bool]:
    """
    Muestra el menú de login/registro y maneja la autenticación.

    Loop infinito que solo termina cuando:
        a) El usuario inicia sesión correctamente (devuelve nombre)
        b) El usuario elige salir (sys.exit)

    La función verifica DOS condiciones antes de dejar pasar:
        1. Que las credenciales sean correctas (bcrypt en db.py)
        2. Que el bool 'chitchat' no sea True en la BD
           (usuarios bloqueados por abuso de chitchat no pueden entrar)

    Retorna: (nombre_usuario, es_nuevo)
        - es_nuevo es siempre False en login (True reservado para futuro uso)
    """
    db.inicializar_bd()  # crea las tablas si es la primera vez que se ejecuta

    while True:
        mostrar_separador()
        print("  Chatbot Meteorológico")
        mostrar_separador()
        print("  [1] Iniciar sesión")
        print("  [2] Registrarse")
        print("  [0] Salir")
        mostrar_separador()

        opcion = input("  Opción: ").strip()

        if opcion == "0":
            print("\nHasta luego.")
            sys.exit(0)

        elif opcion == "1":
            nombre    = input("  Usuario: ").strip()
            contrasena = input("  Contraseña: ").strip()

            # db.login verifica la contraseña usando bcrypt (hash comparado con hash)
            ok, mensaje = db.login(nombre, contrasena)
            if not ok:
                print(f"\n  ✗ {mensaje}")
                continue  # vuelve al inicio del loop

            # Segunda verificación: ¿el usuario fue bloqueado por chitchat?
            # Esta es la "puerta de entrada" que implementa el bool de la BD.
            if db.esta_bloqueado(nombre):
                print(
                    "\n  ✗ Tu acceso fue bloqueado por uso reiterado de "
                    "conversaciones fuera del tema (chitchat).\n"
                    "  Contactá al soporte para rehabilitar tu cuenta."
                )
                sys.exit(0)  # salida definitiva, no puede reintentar

            print(f"\n  ✓ Bienvenido, {nombre}.")
            return nombre, False

        elif opcion == "2":
            nombre = input("  Nombre de usuario nuevo: ").strip()
            if not nombre:
                print("\n  ✗ El nombre no puede estar vacío.")
                continue

            contrasena = input("  Contraseña: ").strip()
            if len(contrasena) < 4:
                print("\n  ✗ La contraseña debe tener al menos 4 caracteres.")
                continue

            # Doble confirmación para evitar errores de tipeo
            confirmacion = input("  Confirmá la contraseña: ").strip()
            if contrasena != confirmacion:
                print("\n  ✗ Las contraseñas no coinciden.")
                continue

            # db.registrar_usuario hashea la contraseña con bcrypt antes de guardar
            ok, mensaje = db.registrar_usuario(nombre, contrasena)
            print(f"\n  {'✓' if ok else '✗'} {mensaje}")
            if not ok:
                continue  # el nombre ya existe, volver al menú

            print(f"  Ahora podés iniciar sesión, {nombre}.")
            # No iniciamos sesión automáticamente: el usuario debe hacer login
            # Esto mantiene el flujo claro y consistente

        else:
            print("\n  ✗ Opción no válida.")


# ============================================================
# LÓGICA POST-SESIÓN
# ============================================================

def procesar_resultado_sesion(nombre: str) -> None:
    """
    Evalúa qué pasó durante la sesión y aplica las consecuencias.

    Esta función corre DESPUÉS de que rasa shell terminó.
    Lee session.json (escrito por actions.py) y decide:

        motivo_stop = "chitchat":
            → Activa el bool chitchat=1 en la BD (usuario bloqueado)

        motivo_stop = "errores":
            → Si el usuario ya era recurrente: mensaje de derivación a soporte
            → Si es la primera vez: lo registra en la tabla 'recurrentes'

        motivo_stop = None (salida normal con /stop):
            → No hace nada, solo muestra mensaje de cierre
    """
    sesion       = leer_sesion()
    limpiar_sesion()  # eliminamos los archivos temporales

    motivo_stop = sesion.get("motivo_stop", None)

    mostrar_separador()

    if motivo_stop == "chitchat":
        # Escribir chitchat=1 en la tabla usuarios
        # La próxima vez que intente loguearse, esta_bloqueado() devolverá True
        db.bloquear_por_chitchat(nombre)
        print(
            f"  Sesión de '{nombre}' finalizada por exceso de chitchat.\n"
            f"  El acceso fue bloqueado. (bool chitchat = true)"
        )

    elif motivo_stop == "errores":
        if db.es_recurrente(nombre):
            # El usuario ya estaba en la tabla recurrentes
            # → segunda vez que falla: derivar a soporte humano
            print(
                f"  '{nombre}' es un usuario recurrente con problemas de comprensión.\n"
                f"  → Derivando a soporte humano."
            )
        else:
            # Primera vez que llega al límite de errores
            # → agregar a recurrentes para rastrearlo en el futuro
            db.registrar_recurrente(nombre)
            print(
                f"  '{nombre}' alcanzó el límite de errores.\n"
                f"  → Registrado como usuario recurrente en la BD."
            )

    else:
        # Salida normal: el usuario escribió /stop o cerró la sesión limpiamente
        print(f"  Sesión de '{nombre}' finalizada normalmente.")

    mostrar_separador()


# ============================================================
# LANZADOR DEL BOT
# ============================================================

def lanzar_bot(nombre: str) -> None:
    """
    Lanza rasa shell como subproceso y espera a que termine.

    Usamos subprocess.Popen (en lugar de subprocess.run) porque
    necesitamos acceder al PID del proceso para que actions.py
    pueda terminarlo cuando detecte chitchat o errores excesivos.

    Flujo:
        1. Escribe los datos iniciales en session.json
        2. Lanza rasa shell con Popen
        3. Guarda el PID en .rasa_shell.pid
        4. Espera con proceso.wait() hasta que el proceso termine
           (ya sea por /stop del usuario o SIGTERM de actions.py)
        5. Limpia el PID file en el bloque finally
    """
    # Escribir datos iniciales de sesión.
    # actions.py los completará con el motivo de cierre cuando corresponda.
    SESSION_FILE.write_text(
        json.dumps({"usuario": nombre, "contador_chitchat": 0, "contador_errores": 0}),
        encoding="utf-8"
    )

    print("\n  Iniciando el chatbot... (escribí /stop para salir)\n")
    mostrar_separador()

    try:
        # Popen lanza el proceso sin bloquearse (a diferencia de run)
        proceso = subprocess.Popen(["rasa", "shell"])

        # Guardamos el PID para que actions.py pueda matar este proceso
        # cuando el usuario supere un límite
        PIDFILE.write_text(str(proceso.pid))

        # wait() bloquea este script hasta que rasa shell termine.
        # Puede terminar de dos formas:
        #   a) El usuario escribe /stop → rasa shell cierra normalmente
        #   b) actions.py envía SIGTERM usando el PID → cierre forzado
        proceso.wait()

    except FileNotFoundError:
        # rasa no está instalado o el entorno virtual no está activado
        print(
            "\n  ✗ No se encontró el comando 'rasa'.\n"
            "  Asegurate de tener el entorno virtual activado."
        )
        sys.exit(1)

    finally:
        # Limpiamos el PID file siempre, incluso si hubo un error.
        # El bloque finally se ejecuta sin importar cómo terminó el try.
        if PIDFILE.exists():
            PIDFILE.unlink()


# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":
    # Este bloque solo corre cuando ejecutás `python main.py` directamente.
    # Si otro script importara main.py, este bloque NO correría.

    limpiar_sesion()            # 1. limpiar sesiones anteriores colgadas
    nombre_usuario, _ = menu_principal()  # 2. autenticación
    lanzar_bot(nombre_usuario)  # 3. bot (bloqueante hasta que termine)
    procesar_resultado_sesion(nombre_usuario)  # 4. lógica post-sesión