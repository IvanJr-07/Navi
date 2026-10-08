"""
navi.py — El programa principal de Navi 🧚
En cada ejecución:
  1. Lee los botones 👍 / 👎 que tocaste desde la última vez.
  2. Recolecta publicaciones y filtra las nuevas con la memoria.
  3. Las evalúa con la IA (usando tus reacciones como ejemplos) y las guarda.
  4. Te avisa de lo que pase el umbral.
  5. Una vez al día, en la noche, te manda un resumen con lo "de frontera".
     Si esa noche no hubo ejecución, lo manda en la primera del día siguiente.

Uso:
    python navi.py                     -> ejecución real
    python navi.py --prueba            -> evalúa y guarda, pero NO manda nada ni lee botones
    python navi.py --prueba --resumen  -> además muestra cómo quedaría el resumen de hoy
"""
import sys
import time
from datetime import datetime, timedelta, timezone

from juez import MAX_AVISOS, UMBRAL_AVISO, evaluar
from memoria import (
    estadisticas,
    filtrar_nuevas,
    guardar_estado,
    guardar_evaluaciones,
    leer_estado,
    limpiar_antiguas,
    marcar_en_resumen,
    marcar_notificada,
    pendientes_de_aviso,
    pendientes_de_resumen,
)
from notificador import enviar_alerta, enviar_resumen
from recolector import recolectar
from retroalimentacion import procesar_retroalimentacion

# México dejó de usar horario de verano en 2022, así que siempre es UTC-6.
# (GitHub Actions corre en hora UTC; por eso hay que convertir.)
HORA_MEXICO = timezone(timedelta(hours=-6), "CST")

HORA_RESUMEN = 20       # A partir de las 8 PM (hora de México) se manda el resumen del día
MINIMO_RESUMEN = 5      # Calificación mínima para entrar al resumen (hasta UMBRAL_AVISO - 1)
MAX_RESUMEN = 8         # Máximo de publicaciones en el resumen
CLAVE_RESUMEN = "ultimo_resumen"        # Fecha (día) del último resumen enviado
CLAVE_EJECUCION = "ultima_ejecucion"    # Momento de la última ejecución completa (la lee guardia.py)


def avisar(pendientes, modo_prueba: bool) -> int:
    """Manda los avisos pendientes. Regresa cuántos se enviaron."""
    enviados = 0
    for fila in pendientes:
        print(f"🔔 {fila['puntuacion']:>2}/10  [{fila['fuente']}] {fila['titulo']}")
        if modo_prueba:
            continue

        ok = enviar_alerta(
            titulo=fila["resumen_es"] or fila["titulo"],
            detalle=f"{fila['razon']}\n\n⭐ {fila['puntuacion']}/10 · {fila['fuente']}",
            enlace=fila["enlace"],
            clave=fila["clave"],
        )
        if ok:
            # Solo se marca como notificada si de verdad llegó a Telegram;
            # si falló, se reintenta en la siguiente ejecución.
            marcar_notificada(fila["enlace"])
            enviados += 1
            time.sleep(1)
    return enviados


def que_resumen_toca(forzar: bool) -> tuple[str, str, int] | None:
    """Decide si hay que mandar un resumen ahora.

    Regresa (fecha_del_resumen, título, horas_a_revisar) o None si no toca.
      - Después de las 8 PM, si no se ha mandado el de hoy -> resumen de hoy.
      - Antes de las 8 PM, si quedó pendiente el de ayer (no hubo ejecución
        esa noche) -> resumen de ayer, para que no se pierda.
    """
    ahora = datetime.now(HORA_MEXICO)
    hoy = ahora.date()
    ayer = hoy - timedelta(days=1)
    ultimo = leer_estado(CLAVE_RESUMEN)

    if forzar or (ahora.hour >= HORA_RESUMEN and ultimo != hoy.isoformat()):
        return hoy.isoformat(), "Resumen del día", 24

    # Las fechas ISO (AAAA-MM-DD) se pueden comparar como texto
    if ultimo is not None and ultimo < ayer.isoformat():
        return ayer.isoformat(), "Resumen de ayer", 36

    return None


def resumen_diario(modo_prueba: bool, forzar: bool) -> None:
    """Manda (una vez al día) el resumen con las publicaciones de calificación media."""
    decision = que_resumen_toca(forzar)
    if decision is None:
        return
    fecha, titulo, horas = decision

    candidatas = pendientes_de_resumen(MINIMO_RESUMEN, UMBRAL_AVISO, limite=MAX_RESUMEN, horas=horas)
    print(f"\n📋 {titulo}: {len(candidatas)} publicaciones.")
    for fila in candidatas:
        print(f"   {fila['puntuacion']:>2}/10  [{fila['fuente']}] {fila['titulo']}")

    if modo_prueba:
        print("   (Modo prueba: el resumen no se envió.)")
        return

    if not candidatas:
        guardar_estado(CLAVE_RESUMEN, fecha)  # Nada que mandar; no volver a revisar ese día
        return

    if enviar_resumen(candidatas, titulo):
        marcar_en_resumen([fila["clave"] for fila in candidatas])
        guardar_estado(CLAVE_RESUMEN, fecha)
        print("   📨 Resumen enviado a Telegram.")


def ejecutar(modo_prueba: bool, forzar_resumen: bool) -> None:
    print("🧚 Navi despertó.\n")

    # 1. Botones 👍 / 👎 (en modo prueba no, para no "gastar" los de la nube)
    if not modo_prueba:
        reacciones = procesar_retroalimentacion()
        if reacciones:
            print(f"👍👎 {reacciones} reacciones nuevas guardadas.\n")

    # 2. Recolectar y filtrar
    print("Revisando las fuentes...\n")
    publicaciones = recolectar()
    nuevas = filtrar_nuevas(publicaciones)
    print(f"\n📥 {len(publicaciones)} publicaciones encontradas; {len(nuevas)} son nuevas.\n")

    # 3. Evaluar y guardar
    if nuevas:
        evaluadas = evaluar(nuevas)
        guardar_evaluaciones(evaluadas)
        print(f"\n💾 {len(evaluadas)} evaluaciones guardadas en la memoria.")
        # Las que no se pudieron evaluar (lote fallido) NO se guardan,
        # así que se volverán a intentar en la siguiente ejecución.

    # 4. Avisos
    pendientes = pendientes_de_aviso(UMBRAL_AVISO, limite=MAX_AVISOS)
    print()
    if not pendientes:
        print("😴 Nada nuevo que merezca un Hey! Listen!")
    else:
        enviados = avisar(pendientes, modo_prueba)
        if modo_prueba:
            print("\n(Modo prueba: no se envió nada. Quedan pendientes para la próxima ejecución real.)")
        else:
            print(f"\n📨 {enviados} avisos enviados a Telegram.")

    # 5. Resumen diario
    resumen_diario(modo_prueba, forzar_resumen)

    # 6. Mantenimiento
    borradas = limpiar_antiguas()
    if not modo_prueba:
        guardar_estado(CLAVE_EJECUCION, datetime.now(timezone.utc).isoformat())

    datos = estadisticas()
    print(f"\n🧠 Memoria: {datos['total']} publicaciones, {datos['notificadas']} avisadas, "
          f"{datos['likes']} 👍 / {datos['dislikes']} 👎"
          + (f", {borradas} antiguas borradas." if borradas else "."))


if __name__ == "__main__":
    ejecutar(modo_prueba="--prueba" in sys.argv, forzar_resumen="--resumen" in sys.argv)
