"""
recolector.py — Etapa 2 del bot de alertas "Navi".
Lee todas las fuentes de fuentes.yaml y junta sus publicaciones
recientes en una lista con el mismo formato.

Uso:
    python recolector.py   -> revisa las fuentes y muestra un reporte
"""
import html
import re
import time
from collections import Counter
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

import feedparser
import requests
import yaml

ARCHIVO_FUENTES = "fuentes.yaml"

# Algunos sitios (como Reddit) bloquean programas que no se identifican.
# Este encabezado le dice al sitio quién está pidiendo la página.
HEADERS = {"User-Agent": "Navi-AlertBot/1.0 (proyecto personal de estudiante)"}

# Segundos mínimos entre dos peticiones al mismo sitio.
# Reddit es estricto, así que le damos más espacio.
ESPERA_POR_DOMINIO = {"www.reddit.com": 6}
ESPERA_DEFAULT = 1

MAX_INTENTOS = 3       # Veces que se reintenta si el sitio responde 429
MAX_ESPERA_429 = 60    # Nunca esperar más de esto entre reintentos

# Detecta la comunidad de Reddit dentro de un enlace: reddit.com/r/<comunidad>/...
PATRON_SUBREDDIT = re.compile(r"reddit\.com/r/([^/]+)/", re.IGNORECASE)

# Guarda el momento de la última petición a cada dominio
_ultima_peticion: dict[str, float] = {}


def cargar_fuentes() -> dict:
    """Lee fuentes.yaml y lo regresa como diccionario."""
    with open(ARCHIVO_FUENTES, encoding="utf-8") as archivo:
        return yaml.safe_load(archivo)


def esperar_turno(url: str) -> None:
    """Si pedimos algo al mismo sitio hace poco, espera lo necesario."""
    dominio = urlparse(url).netloc
    espera = ESPERA_POR_DOMINIO.get(dominio, ESPERA_DEFAULT)
    ultima = _ultima_peticion.get(dominio)

    if ultima is not None:
        restante = espera - (time.monotonic() - ultima)
        if restante > 0:
            time.sleep(restante)

    _ultima_peticion[dominio] = time.monotonic()


def segundos_retry_after(respuesta: requests.Response, intento: int) -> int:
    """Lee cuántos segundos pide esperar el sitio; si no dice, usa un valor creciente."""
    valor = respuesta.headers.get("Retry-After", "")
    try:
        segundos = int(valor)
    except ValueError:
        segundos = 10 * intento  # 10 s, luego 20 s...
    return min(segundos, MAX_ESPERA_429)


def descargar(url: str) -> requests.Response:
    """Descarga una URL respetando los límites de velocidad del sitio."""
    for intento in range(1, MAX_INTENTOS + 1):
        esperar_turno(url)
        respuesta = requests.get(url, headers=HEADERS, timeout=15)

        if respuesta.status_code == 429 and intento < MAX_INTENTOS:
            segundos = segundos_retry_after(respuesta, intento)
            print(f"   ⏳ {urlparse(url).netloc} pidió esperar; reintento en {segundos} s...")
            time.sleep(segundos)
            continue

        respuesta.raise_for_status()  # Lanza error si respondió 4xx/5xx
        return respuesta

    # No debería llegar aquí, pero por si acaso
    respuesta.raise_for_status()
    return respuesta


def limpiar_texto(texto: str, max_caracteres: int = 300) -> str:
    """Quita etiquetas HTML, espacios de más y recorta el texto."""
    sin_html = re.sub(r"<[^>]+>", " ", texto or "")    # Quita <etiquetas>
    sin_html = html.unescape(sin_html)                 # &amp; -> &, etc.
    sin_html = re.sub(r"\s+", " ", sin_html).strip()   # Junta espacios
    if len(sin_html) > max_caracteres:
        sin_html = sin_html[:max_caracteres].rsplit(" ", 1)[0] + "…"
    return sin_html


def obtener_fecha(entrada) -> datetime | None:
    """Saca la fecha de publicación de una entrada, si la tiene."""
    fecha = entrada.get("published_parsed") or entrada.get("updated_parsed")
    if fecha:
        return datetime(*fecha[:6], tzinfo=timezone.utc)
    return None


def nombre_de_fuente(fuente: dict, enlace: str) -> str:
    """Para Reddit, agrega la comunidad al nombre (ej. 'Reddit r/fpv')."""
    coincidencia = PATRON_SUBREDDIT.search(enlace)
    if "reddit.com" in fuente["url"] and coincidencia:
        return f"Reddit r/{coincidencia.group(1)}"
    return fuente["nombre"]


def leer_fuente(fuente: dict, limite_fecha: datetime) -> list[dict]:
    """Descarga un feed y regresa sus publicaciones recientes."""
    respuesta = descargar(fuente["url"])
    feed = feedparser.parse(respuesta.content)
    publicaciones = []

    for entrada in feed.entries:
        fecha = obtener_fecha(entrada)
        if fecha and fecha < limite_fecha:
            continue  # Demasiado vieja, la saltamos

        enlace = entrada.get("link", "")
        publicaciones.append({
            "titulo": limpiar_texto(entrada.get("title", ""), 200),
            "resumen": limpiar_texto(entrada.get("summary", "")),
            "enlace": enlace,
            "fecha": fecha.isoformat() if fecha else None,
            "fuente": nombre_de_fuente(fuente, enlace),
            "categoria": fuente.get("categoria", "general"),
        })

    return publicaciones


def recolectar(mostrar_reporte: bool = True) -> list[dict]:
    """Revisa todas las fuentes y regresa todas las publicaciones juntas."""
    config = cargar_fuentes()
    limite_fecha = datetime.now(timezone.utc) - timedelta(hours=config.get("max_horas", 48))
    todas = []

    for fuente in config["fuentes"]:
        try:
            publicaciones = leer_fuente(fuente, limite_fecha)
            todas.extend(publicaciones)
            if mostrar_reporte:
                print(f"✅ {fuente['nombre']:<28} {len(publicaciones):>3} publicaciones")
                # Si la fuente mezcla varias comunidades, muestra el desglose
                conteo = Counter(p["fuente"] for p in publicaciones)
                if len(conteo) > 1:
                    for nombre, cantidad in conteo.most_common():
                        print(f"     └ {nombre:<25} {cantidad:>3}")
        except Exception as error:
            # Si una fuente falla, avisamos y seguimos con las demás
            if mostrar_reporte:
                print(f"❌ {fuente['nombre']:<28} Error: {error}")

    return todas


if __name__ == "__main__":
    print("🧚 Navi está revisando las fuentes...\n")
    resultados = recolectar()

    print(f"\nTotal: {len(resultados)} publicaciones recientes.\n")
    print("Ejemplos:")
    for p in resultados[:5]:
        print(f"\n[{p['fuente']}] {p['titulo']}")
        print(f"   {p['resumen'][:120]}")
        print(f"   {p['enlace']}")
