"""
db.py — Capa de acceso a datos (SQLite)
Maneja usuarios, contraseñas hasheadas y registro de recurrentes.
No requiere ningún servidor externo. Genera un único archivo: usuarios.db
"""

import sqlite3
import bcrypt
from pathlib import Path

DB_PATH = Path(__file__).parent / "usuarios.db"


def _connect() -> sqlite3.Connection:
    """Abre (o crea) la base de datos y devuelve la conexión."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row  # permite acceder columnas por nombre
    return conn


def inicializar_bd() -> None:
    """
    Crea las tablas si no existen.
    Se llama una sola vez al inicio del programa.

    Tabla usuarios:
        id          — clave primaria autoincremental
        nombre      — nombre de usuario (único)
        contrasena  — hash bcrypt de la contraseña
        chitchat    — 0 = normal, 1 = bloqueado por chitchat

    Tabla recurrentes:
        id          — clave primaria autoincremental
        nombre      — nombre de usuario (único, FK lógica a usuarios)
    """
    with _connect() as conn:
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


# ──────────────────────────────────────────────
# Operaciones sobre usuarios
# ──────────────────────────────────────────────

def registrar_usuario(nombre: str, contrasena: str) -> tuple[bool, str]:
    """
    Registra un usuario nuevo.
    Devuelve (True, mensaje_ok) o (False, mensaje_error).
    La contraseña se hashea con bcrypt antes de guardar.
    """
    hash_pwd = bcrypt.hashpw(contrasena.encode(), bcrypt.gensalt()).decode()
    try:
        with _connect() as conn:
            conn.execute(
                "INSERT INTO usuarios (nombre, contrasena) VALUES (?, ?)",
                (nombre, hash_pwd)
            )
        return True, f"Usuario '{nombre}' registrado correctamente."
    except sqlite3.IntegrityError:
        return False, f"El usuario '{nombre}' ya existe. Iniciá sesión."


def login(nombre: str, contrasena: str) -> tuple[bool, str]:
    """
    Verifica credenciales.
    Devuelve (True, "ok") o (False, motivo_del_error).
    """
    with _connect() as conn:
        row = conn.execute(
            "SELECT contrasena FROM usuarios WHERE nombre = ?", (nombre,)
        ).fetchone()

    if row is None:
        return False, "Usuario no encontrado."

    if not bcrypt.checkpw(contrasena.encode(), row["contrasena"].encode()):
        return False, "Contraseña incorrecta."

    return True, "ok"


def esta_bloqueado(nombre: str) -> bool:
    """Devuelve True si el usuario tiene chitchat = 1."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT chitchat FROM usuarios WHERE nombre = ?", (nombre,)
        ).fetchone()
    return bool(row and row["chitchat"] == 1)


def bloquear_por_chitchat(nombre: str) -> None:
    """Pone chitchat = 1 para el usuario dado."""
    with _connect() as conn:
        conn.execute(
            "UPDATE usuarios SET chitchat = 1 WHERE nombre = ?", (nombre,)
        )


# ──────────────────────────────────────────────
# Operaciones sobre recurrentes
# ──────────────────────────────────────────────

def es_recurrente(nombre: str) -> bool:
    """Devuelve True si el usuario ya está en la tabla de recurrentes."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT id FROM recurrentes WHERE nombre = ?", (nombre,)
        ).fetchone()
    return row is not None


def registrar_recurrente(nombre: str) -> None:
    """Agrega al usuario a la tabla de recurrentes (ignora si ya existe)."""
    with _connect() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO recurrentes (nombre) VALUES (?)", (nombre,)
        )