"""
main.py — Punto de entrada del chatbot meteorológico
Flujo:
  1. Inicializa la BD (crea el archivo si no existe)
  2. Muestra menú: registrarse o iniciar sesión
  3. Verifica credenciales y bool de chitchat
  4. Si pasa, lanza `rasa shell` como subproceso
  5. Al terminar la sesión, aplica la lógica de contadores
     (los contadores viven en actions.py vía slots de Rasa,
      pero main.py los lee del archivo de sesión temporal)

NOTA: La comunicación entre main.py y actions.py se hace a través
de un pequeño archivo session.json que actions.py escribe al finalizar,
y main.py lee para saber qué ocurrió (chitchat o errores).
"""

import json
import subprocess
import sys
from pathlib import Path

import db

SESSION_FILE = Path(__file__).parent / ".session.json"
PIDFILE      = Path(__file__).parent / ".rasa_shell.pid"
LIMITE_CHITCHAT = 3
LIMITE_ERRORES  = 3


# ──────────────────────────────────────────────
# Helpers de UI (consola)
# ──────────────────────────────────────────────

def limpiar_sesion() -> None:
    """Borra los archivos temporales de sesión si existen."""
    if SESSION_FILE.exists():
        SESSION_FILE.unlink()
    if PIDFILE.exists():
        PIDFILE.unlink()


def leer_sesion() -> dict:
    """Lee el resultado de la sesión escrito por actions.py."""
    if not SESSION_FILE.exists():
        return {}
    try:
        return json.loads(SESSION_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def mostrar_separador() -> None:
    print("\n" + "─" * 50)


# ──────────────────────────────────────────────
# Flujo de autenticación
# ──────────────────────────────────────────────

def menu_principal() -> tuple[str, bool]:
    """
    Muestra el menú de login/registro.
    Devuelve (nombre_usuario, es_nuevo).
    Hace un loop hasta que el usuario ingrese credenciales válidas.
    """
    db.inicializar_bd()

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
            nombre = input("  Usuario: ").strip()
            contrasena = input("  Contraseña: ").strip()

            ok, mensaje = db.login(nombre, contrasena)
            if not ok:
                print(f"\n  ✗ {mensaje}")
                continue

            # Verificar si está bloqueado por chitchat
            if db.esta_bloqueado(nombre):
                print(
                    "\n  ✗ Tu acceso fue bloqueado por uso reiterado de "
                    "conversaciones fuera del tema (chitchat).\n"
                    "  Contactá al soporte para rehabilitar tu cuenta."
                )
                sys.exit(0)

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

            confirmacion = input("  Confirmá la contraseña: ").strip()
            if contrasena != confirmacion:
                print("\n  ✗ Las contraseñas no coinciden.")
                continue

            ok, mensaje = db.registrar_usuario(nombre, contrasena)
            print(f"\n  {'✓' if ok else '✗'} {mensaje}")
            if not ok:
                continue  # nombre ya existe, volver al menú

            print(f"  Ahora podés iniciar sesión, {nombre}.")

        else:
            print("\n  ✗ Opción no válida.")


# ──────────────────────────────────────────────
# Lógica post-sesión
# ──────────────────────────────────────────────

def procesar_resultado_sesion(nombre: str) -> None:
    """
    Lee session.json que actions.py escribió al terminar la sesión
    y aplica la lógica de contadores:
      - chitchat >= LIMITE → bloquear + /stop (ya ejecutado por actions.py)
      - errores >= LIMITE  → ver si es recurrente → derivar o registrar
    """
    sesion = leer_sesion()
    limpiar_sesion()

    contador_chitchat = sesion.get("contador_chitchat", 0)
    contador_errores  = sesion.get("contador_errores", 0)
    motivo_stop       = sesion.get("motivo_stop", None)
    # motivo_stop puede ser "chitchat", "errores" o None (salida normal)

    mostrar_separador()

    if motivo_stop == "chitchat":
        # Bloquear al usuario en la BD
        db.bloquear_por_chitchat(nombre)
        print(
            f"  Sesión de '{nombre}' finalizada por exceso de chitchat.\n"
            f"  El acceso fue bloqueado. (bool chitchat = true)"
        )

    elif motivo_stop == "errores":
        if db.es_recurrente(nombre):
            # Ya fue registrado antes → derivar a soporte
            print(
                f"  '{nombre}' es un usuario recurrente con problemas de "
                f"comprensión.\n"
                f"  → Derivando a soporte humano."
            )
        else:
            # Primera vez que llega a 3 errores → registrar como recurrente
            db.registrar_recurrente(nombre)
            print(
                f"  '{nombre}' alcanzó el límite de errores.\n"
                f"  → Registrado como usuario recurrente en la BD."
            )

    else:
        print(f"  Sesión de '{nombre}' finalizada normalmente.")

    mostrar_separador()


# ──────────────────────────────────────────────
# Lanzador del bot
# ──────────────────────────────────────────────

def lanzar_bot(nombre: str) -> None:
    """
    Escribe el nombre de usuario en session.json para que actions.py
    lo pueda leer, luego lanza `rasa shell` como subproceso.
    Guarda el PID del proceso en .rasa_shell.pid para que actions.py
    pueda matarlo cuando detecte chitchat o errores excesivos.
    """
    SESSION_FILE.write_text(
        json.dumps({"usuario": nombre, "contador_chitchat": 0, "contador_errores": 0}),
        encoding="utf-8"
    )

    print("\n  Iniciando el chatbot... (escribí /stop para salir)\n")
    mostrar_separador()

    try:
        # Popen en lugar de run para poder capturar el PID
        proceso = subprocess.Popen(["rasa", "shell"])

        # Guardar el PID para que actions.py pueda terminar el proceso
        PIDFILE.write_text(str(proceso.pid))

        # Esperar a que el proceso termine (por /stop del usuario o por SIGTERM de actions.py)
        proceso.wait()

    except FileNotFoundError:
        print(
            "\n  ✗ No se encontró el comando 'rasa'.\n"
            "  Asegurate de tener el entorno virtual activado."
        )
        sys.exit(1)
    finally:
        # Limpiar el PID file siempre, sin importar cómo terminó
        if PIDFILE.exists():
            PIDFILE.unlink()


# ──────────────────────────────────────────────
# Entry point
# ──────────────────────────────────────────────

if __name__ == "__main__":
    limpiar_sesion()  # limpiar sesiones anteriores colgadas

    nombre_usuario, es_nuevo = menu_principal()
    lanzar_bot(nombre_usuario)
    procesar_resultado_sesion(nombre_usuario)