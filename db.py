"""
╔══════════════════════════════════════════════════════════════╗
║                          db.py                               ║
║            Capa de acceso a datos (SQLite)                   ║
╠══════════════════════════════════════════════════════════════╣
║ Este módulo centraliza TODO lo relacionado con la base de    ║
║ datos. main.py lo importa y llama sus funciones sin saber    ║
║ nada de SQL. Esto se llama "separación de responsabilidades".║
║                                                              ║
║ Base de datos: archivo usuarios.db (SQLite)                  ║
║   - No requiere servidor externo                             ║
║   - Se crea automáticamente si no existe                     ║
║   - Un solo archivo portable                                 ║
║                                                              ║
║ Tablas:                                                      ║
║   usuarios    → todos los que se registraron                 ║
║   recurrentes → los que llegaron al límite de errores        ║
╚══════════════════════════════════════════════════════════════╝
"""

import sqlite3
import bcrypt         # para hashear y verificar contraseñas de forma segura
from pathlib import Path

# Ruta al archivo de base de datos.
# __file__ es este archivo (db.py), .parent es su carpeta.
# El archivo usuarios.db se crea en la misma carpeta que db.py.
DB_PATH = Path(__file__).parent / "usuarios.db"


def _connect() -> sqlite3.Connection:
    """
    Abre la conexión a la base de datos (o la crea si no existe).

    row_factory = sqlite3.Row permite acceder a las columnas por nombre
    en lugar de por índice:
        row["nombre"]  ← con Row (más legible)
        row[1]         ← sin Row (más difícil de mantener)

    El guión bajo al inicio del nombre (_connect) es una convención
    de Python para indicar que es una función "privada" de este módulo,
    no pensada para usarse desde afuera.
    """
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def inicializar_bd() -> None:
    """
    Crea las tablas de la base de datos si todavía no existen.

    Se llama una única vez al inicio de main.py. Si las tablas ya
    existen (segunda ejecución en adelante), el comando
    CREATE TABLE IF NOT EXISTS las ignora silenciosamente.

    Estructura de la tabla 'usuarios':
        id         → número único autoincremental (clave primaria)
        nombre     → nombre de usuario, debe ser único en toda la tabla
        contrasena → hash bcrypt de la contraseña (NUNCA texto plano)
        chitchat   → 0 = acceso normal, 1 = bloqueado por abuso de chitchat

    Estructura de la tabla 'recurrentes':
        id     → número único autoincremental
        nombre → nombre del usuario que llegó al límite de errores
                 (referencia lógica a usuarios.nombre)
    """
    with _connect() as conn:
        # executescript ejecuta múltiples sentencias SQL separadas por ";"
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS usuarios (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                nombre     TEXT    NOT NULL UNIQUE,
                contrasena TEXT    NOT NULL,
                chitchat   INTEGER NOT NULL DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS recurrentes (
                id     INTEGER PRIMARY KEY AUTOINCREMENT,
                nombre TEXT    NOT NULL UNIQUE
            );
        """)


# ══════════════════════════════════════════════
# OPERACIONES SOBRE USUARIOS
# ══════════════════════════════════════════════

def registrar_usuario(nombre: str, contrasena: str) -> tuple[bool, str]:
    """
    Registra un nuevo usuario en la base de datos.

    Seguridad: la contraseña NUNCA se guarda en texto plano.
    bcrypt.hashpw() genera un hash con un "salt" aleatorio incluido,
    lo que significa que el mismo password genera hashes diferentes
    cada vez. Esto protege contra ataques de rainbow table.

    Ejemplo:
        "mipassword" → "$2b$12$eImiTXuWVxfM37uY4JANjQuur0To..."

    Retorna:
        (True, "mensaje ok")  si el registro fue exitoso
        (False, "mensaje error")  si el nombre ya existe
    """
    # Paso 1: hashear la contraseña antes de guardarla
    hash_pwd = bcrypt.hashpw(contrasena.encode(), bcrypt.gensalt()).decode()
    # .encode() → convierte string a bytes (bcrypt trabaja con bytes)
    # .decode() → convierte el hash de bytes a string para guardar en SQLite

    try:
        with _connect() as conn:
            conn.execute(
                "INSERT INTO usuarios (nombre, contrasena) VALUES (?, ?)",
                (nombre, hash_pwd)
                # Los "?" son placeholders seguros contra SQL injection
                # NUNCA hacer: f"INSERT ... VALUES ('{nombre}', ...)"
            )
        return True, f"Usuario '{nombre}' registrado correctamente."
    except sqlite3.IntegrityError:
        # IntegrityError ocurre cuando se viola la restricción UNIQUE del campo nombre
        return False, f"El usuario '{nombre}' ya existe. Iniciá sesión."


def login(nombre: str, contrasena: str) -> tuple[bool, str]:
    """
    Verifica las credenciales de un usuario existente.

    El proceso de verificación:
        1. Busca el hash guardado en la BD para ese nombre
        2. bcrypt.checkpw compara la contraseña ingresada con el hash
           (bcrypt sabe cómo extraer el salt del propio hash)
        3. Si coinciden → login exitoso

    No se puede "descifrar" el hash para obtener la contraseña original.
    Solo se puede verificar si una contraseña dada coincide con el hash.

    Retorna:
        (True, "ok")           si las credenciales son correctas
        (False, "motivo")      si el usuario no existe o la contraseña es incorrecta
    """
    with _connect() as conn:
        row = conn.execute(
            "SELECT contrasena FROM usuarios WHERE nombre = ?", (nombre,)
        ).fetchone()  # fetchone() devuelve la primera fila, o None si no hay resultados

    if row is None:
        return False, "Usuario no encontrado."

    # checkpw compara la contraseña en texto plano con el hash guardado
    if not bcrypt.checkpw(contrasena.encode(), row["contrasena"].encode()):
        return False, "Contraseña incorrecta."

    return True, "ok"


def esta_bloqueado(nombre: str) -> bool:
    """
    Verifica si el usuario fue bloqueado por abuso de chitchat.

    Consulta el campo 'chitchat' en la tabla usuarios.
    Si es 1, el usuario no puede iniciar sesión.

    Esta es la verificación que implementa el "bool" de la consigna.
    Se llama en main.py después de verificar las credenciales.
    """
    with _connect() as conn:
        row = conn.execute(
            "SELECT chitchat FROM usuarios WHERE nombre = ?", (nombre,)
        ).fetchone()
    # bool() convierte: None → False, 0 → False, 1 → True
    return bool(row and row["chitchat"] == 1)


def bloquear_por_chitchat(nombre: str) -> None:
    """
    Activa el bloqueo del usuario poniendo chitchat = 1.

    Se llama desde main.py cuando session.json indica
    que el motivo del cierre fue "chitchat".

    Después de esta llamada, la próxima vez que el usuario
    intente loguearse, esta_bloqueado() devolverá True
    y no podrá acceder al bot.
    """
    with _connect() as conn:
        conn.execute(
            "UPDATE usuarios SET chitchat = 1 WHERE nombre = ?", (nombre,)
        )


# ══════════════════════════════════════════════
# OPERACIONES SOBRE RECURRENTES
# ══════════════════════════════════════════════

def es_recurrente(nombre: str) -> bool:
    """
    Verifica si el usuario ya está en la tabla de recurrentes.

    Un usuario es "recurrente" si ya llegó al límite de errores
    al menos una vez antes. Si llega de nuevo, se lo deriva
    a soporte humano en lugar de simplemente registrarlo.

    Retorna True si el nombre ya existe en la tabla 'recurrentes'.
    """
    with _connect() as conn:
        row = conn.execute(
            "SELECT id FROM recurrentes WHERE nombre = ?", (nombre,)
        ).fetchone()
    return row is not None  # None significa que no encontró resultados


def registrar_recurrente(nombre: str) -> None:
    """
    Agrega al usuario a la tabla de recurrentes.

    Se llama cuando un usuario llega al límite de errores POR PRIMERA VEZ.
    Si el usuario ya está en la tabla (INSERT OR IGNORE), la operación
    se ignora silenciosamente sin lanzar un error.

    La próxima sesión, si vuelve a llegar al límite, es_recurrente()
    devolverá True y main.py lo derivará a soporte.
    """
    with _connect() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO recurrentes (nombre) VALUES (?)", (nombre,)
            # OR IGNORE evita el IntegrityError si el nombre ya existe (UNIQUE)
        )