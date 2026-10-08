"""
memoria.py — La memoria de Navi.
Guarda en una base de datos SQLite (archivo navi.db):
  - qué publicaciones ya se evaluaron y con qué calificación,
  - cuáles ya se te avisaron o fueron al resumen diario,
  - tus reacciones 👍 / 👎,
  - datos de estado (último resumen, última ejecución, etc.).

Uso:
    python memoria.py   -> muestra estadísticas de la memoria
"""
import hashlib
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta, timezone

ARCHIVO_DB = "navi.db"

# "enlace" es la llave primaria (PRIMARY KEY): no puede haber dos filas
# con el mismo enlace, así identificamos cada publicación.
CREAR_TABLAS = """
CREATE TABLE IF NOT EXISTS publicaciones (
    enlace       TEXT PRIMARY KEY,
    titulo       TEXT,
    resumen_es   TEXT,
    fuente       TEXT,
    puntuacion   INTEGER,
    razon        TEXT,
    evaluada_en  TEXT NOT NULL,
    notificada   INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS estado (
    clave  TEXT PRIMARY KEY,
    valor  TEXT
);
"""

# Columnas que se agregaron después de la primera versión.
# Si la base de datos ya existe con la estructura vieja, en lugar de borrarla
# la "migramos": le agregamos las columnas que falten.
COLUMNAS_NUEVAS = {
    "feedback": "INTEGER",                       # 1 = 👍, -1 = 👎, NULL = sin reacción
    "en_resumen": "INTEGER NOT NULL DEFAULT 0",  # 1 = ya salió en un resumen diario
    "clave": "TEXT",                             # Identificador corto para los botones
}


def calcular_clave(enlace: str) -> str:
    """Identificador corto y estable de una publicación (12 caracteres).

    Se calcula con SHA-1, una función "hash": convierte cualquier texto en
    una huella de tamaño fijo. El mismo enlace siempre da la misma clave,
    aunque la base de datos se cree de nuevo. Cabe en los 64 caracteres
    que permite el callback_data de los botones de Telegram.
    """
    return hashlib.sha1(enlace.encode("utf-8")).hexdigest()[:12]


def _migrar(conexion: sqlite3.Connection) -> None:
    """Agrega las columnas nuevas que falten y rellena las claves vacías."""
    existentes = {fila["name"] for fila in conexion.execute("PRAGMA table_info(publicaciones)")}
    for nombre, tipo in COLUMNAS_NUEVAS.items():
        if nombre not in existentes:
            conexion.execute(f"ALTER TABLE publicaciones ADD COLUMN {nombre} {tipo}")

    sin_clave = conexion.execute("SELECT enlace FROM publicaciones WHERE clave IS NULL").fetchall()
    conexion.executemany(
        "UPDATE publicaciones SET clave = ? WHERE enlace = ?",
        [(calcular_clave(fila["enlace"]), fila["enlace"]) for fila in sin_clave],
    )
    conexion.commit()


def _conectar() -> sqlite3.Connection:
    """Abre la base de datos (la crea si no existe) y la deja al día."""
    conexion = sqlite3.connect(ARCHIVO_DB)
    conexion.row_factory = sqlite3.Row  # Permite leer columnas por nombre: fila["titulo"]
    conexion.executescript(CREAR_TABLAS)
    _migrar(conexion)
    return conexion


def _hace(horas: float = 0, dias: float = 0) -> str:
    """Fecha y hora (UTC, formato ISO) de hace cierto tiempo. Sirve para comparar fechas."""
    momento = datetime.now(timezone.utc) - timedelta(hours=horas, days=dias)
    return momento.isoformat()


# ---------------------------------------------------------------- Publicaciones

def filtrar_nuevas(publicaciones: list[dict]) -> list[dict]:
    """Regresa solo las publicaciones que Navi nunca ha evaluado."""
    with closing(_conectar()) as conexion:
        ya_vistas = {fila["enlace"] for fila in conexion.execute("SELECT enlace FROM publicaciones")}

    nuevas = []
    enlaces_en_esta_tanda = set()  # Por si la misma publicación vino dos veces
    for p in publicaciones:
        enlace = p.get("enlace")
        if not enlace or enlace in ya_vistas or enlace in enlaces_en_esta_tanda:
            continue
        enlaces_en_esta_tanda.add(enlace)
        nuevas.append(p)
    return nuevas


def guardar_evaluaciones(evaluadas: list[dict]) -> None:
    """Guarda las publicaciones ya calificadas por el juez."""
    filas = [
        (p["enlace"], p["titulo"], p.get("resumen_es", ""), p["fuente"],
         p["puntuacion"], p.get("razon", ""), _hace(), calcular_clave(p["enlace"]))
        for p in evaluadas if p.get("enlace")
    ]
    with closing(_conectar()) as conexion:
        with conexion:  # Este bloque hace "commit" (guarda) al terminar sin errores
            conexion.executemany(
                """INSERT OR IGNORE INTO publicaciones
                   (enlace, titulo, resumen_es, fuente, puntuacion, razon, evaluada_en, clave)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                filas,
            )


def pendientes_de_aviso(umbral: int, limite: int = 10, horas: int = 72) -> list[sqlite3.Row]:
    """Publicaciones que pasaron el umbral y todavía no se han notificado.

    Solo considera las evaluadas en las últimas `horas`, para no mandarte
    avisos viejos si Navi estuvo apagada varios días.
    """
    with closing(_conectar()) as conexion:
        return conexion.execute(
            """SELECT * FROM publicaciones
               WHERE puntuacion >= ? AND notificada = 0 AND evaluada_en >= ?
               ORDER BY puntuacion DESC, evaluada_en DESC
               LIMIT ?""",
            (umbral, _hace(horas=horas), limite),
        ).fetchall()


def marcar_notificada(enlace: str) -> None:
    """Registra que ya se te avisó de esta publicación."""
    with closing(_conectar()) as conexion:
        with conexion:
            conexion.execute("UPDATE publicaciones SET notificada = 1 WHERE enlace = ?", (enlace,))


# ---------------------------------------------------------------- Resumen diario

def pendientes_de_resumen(minimo: int, maximo: int, limite: int = 8, horas: int = 24) -> list[sqlite3.Row]:
    """Publicaciones "de frontera" (minimo <= calificación < maximo) para el resumen diario."""
    with closing(_conectar()) as conexion:
        return conexion.execute(
            """SELECT * FROM publicaciones
               WHERE puntuacion >= ? AND puntuacion < ?
                 AND notificada = 0 AND en_resumen = 0 AND evaluada_en >= ?
               ORDER BY puntuacion DESC, evaluada_en DESC
               LIMIT ?""",
            (minimo, maximo, _hace(horas=horas), limite),
        ).fetchall()


def marcar_en_resumen(claves: list[str]) -> None:
    """Registra que estas publicaciones ya salieron en un resumen."""
    with closing(_conectar()) as conexion:
        with conexion:
            conexion.executemany(
                "UPDATE publicaciones SET en_resumen = 1 WHERE clave = ?", [(c,) for c in claves]
            )


# ---------------------------------------------------------------- Retroalimentación 👍 / 👎

def registrar_feedback(clave: str, valor: int) -> bool:
    """Guarda tu reacción (1 = 👍, -1 = 👎). Regresa True si la publicación existía."""
    with closing(_conectar()) as conexion:
        with conexion:
            cursor = conexion.execute(
                "UPDATE publicaciones SET feedback = ? WHERE clave = ?", (valor, clave)
            )
            return cursor.rowcount > 0


def ejemplos_feedback(por_tipo: int = 8) -> list[sqlite3.Row]:
    """Tus reacciones más recientes: hasta `por_tipo` 👍 y `por_tipo` 👎.

    Se piden por separado para que el juez vea ejemplos de ambos tipos,
    aunque hayas dado muchos más de uno que del otro.
    """
    consulta = """SELECT titulo, puntuacion, feedback FROM publicaciones
                  WHERE feedback = ? ORDER BY evaluada_en DESC LIMIT ?"""
    with closing(_conectar()) as conexion:
        positivos = conexion.execute(consulta, (1, por_tipo)).fetchall()
        negativos = conexion.execute(consulta, (-1, por_tipo)).fetchall()
    return positivos + negativos


# ---------------------------------------------------------------- Estado general

def leer_estado(clave: str, por_defecto: str | None = None) -> str | None:
    """Lee un dato guardado (por ejemplo, la fecha del último resumen)."""
    with closing(_conectar()) as conexion:
        fila = conexion.execute("SELECT valor FROM estado WHERE clave = ?", (clave,)).fetchone()
    return fila["valor"] if fila else por_defecto


def guardar_estado(clave: str, valor: str) -> None:
    """Guarda o actualiza un dato de estado."""
    with closing(_conectar()) as conexion:
        with conexion:
            conexion.execute(
                "INSERT INTO estado (clave, valor) VALUES (?, ?) "
                "ON CONFLICT(clave) DO UPDATE SET valor = excluded.valor",
                (clave, valor),
            )


# ---------------------------------------------------------------- Mantenimiento

def limpiar_antiguas(dias: int = 14) -> int:
    """Borra registros viejos para que la base de datos no crezca para siempre.

    Es seguro porque el recolector ignora publicaciones de más de 48 horas,
    así que las de hace 14 días nunca van a volver a aparecer.
    Las que tienen tu 👍 o 👎 NO se borran: son ejemplos para el juez.
    """
    with closing(_conectar()) as conexion:
        with conexion:
            cursor = conexion.execute(
                "DELETE FROM publicaciones WHERE evaluada_en < ? AND feedback IS NULL",
                (_hace(dias=dias),),
            )
            return cursor.rowcount


def estadisticas() -> dict:
    """Cuenta publicaciones guardadas, avisos enviados y reacciones."""
    with closing(_conectar()) as conexion:
        def contar(condicion: str = "1") -> int:
            return conexion.execute(f"SELECT COUNT(*) FROM publicaciones WHERE {condicion}").fetchone()[0]

        return {
            "total": contar(),
            "notificadas": contar("notificada = 1"),
            "en_resumen": contar("en_resumen = 1"),
            "likes": contar("feedback = 1"),
            "dislikes": contar("feedback = -1"),
        }


if __name__ == "__main__":
    datos = estadisticas()
    print(f"🧠 Memoria de Navi ({ARCHIVO_DB})")
    print(f"   Publicaciones recordadas: {datos['total']}")
    print(f"   Avisos enviados:          {datos['notificadas']}")
    print(f"   En resúmenes diarios:     {datos['en_resumen']}")
    print(f"   Tus reacciones:           {datos['likes']} 👍  /  {datos['dislikes']} 👎")
