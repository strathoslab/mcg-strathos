#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Monitor de Conflictos Geopolíticos (MCG) — Strathos Lab
Job de precios.

Lee data/mapa_activos.csv, trae precios de mercado y escribe docs/mcg_estado.json,
que alimenta el dashboard y el informe mensual.

Encuadre: esto es un MONITOR DE EXPOSICIÓN. Registra movimientos de activos mapeados
a conflictos. No afirma causalidad. La interpretación es humana y va en el informe.
"""

import csv
import json
import os
import sys
import time
from datetime import datetime, timezone

import pandas as pd
import yfinance as yf

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MAPA = os.path.join(RAIZ, "data", "mapa_activos.csv")
SALIDA_DIR = os.path.join(RAIZ, "docs")
SALIDA = os.path.join(SALIDA_DIR, "mcg_estado.json")
HISTORIAL = os.path.join(SALIDA_DIR, "mcg_historial.json")

# Cuántos días de historial se conservan (algo más de un año hábil).
MAX_DIAS_HISTORIAL = 400

# Umbral para marcar un movimiento como "inusual" (a revisar en el informe).
# No implica causalidad: solo señala que el activo se movió fuera de su rango habitual.
UMBRAL_SIGMA = 1.5


def leer_mapa(ruta):
    with open(ruta, encoding="utf-8") as f:
        return list(csv.DictReader(f))


def metricas(hist):
    """Calcula métricas de un histórico de precios. Devuelve None si no hay datos."""
    if hist is None or hist.empty or "Close" not in hist:
        return None
    cierre = hist["Close"].dropna()
    if len(cierre) < 2:
        return None

    ultimo = float(cierre.iloc[-1])

    def variacion(dias):
        if len(cierre) <= dias:
            return None
        previo = float(cierre.iloc[-(dias + 1)])
        if previo == 0:
            return None
        return round((ultimo / previo - 1) * 100, 2)

    # Volatilidad anualizada sobre los últimos 30 días de retornos diarios
    retornos = cierre.pct_change().dropna()
    vol_30d = None
    z_score = None
    if len(retornos) >= 21:
        r30 = retornos.tail(30)
        desvio = float(r30.std())
        vol_30d = round(desvio * (252 ** 0.5) * 100, 2)
        # z-score del último movimiento diario contra su propio desvío
        if desvio > 0:
            z_score = round(float(retornos.iloc[-1]) / desvio, 2)

    # Posición en el rango de 52 semanas (0 = mínimo, 100 = máximo)
    c52 = cierre.tail(252)
    minimo, maximo = float(c52.min()), float(c52.max())
    pos_52s = None
    if maximo > minimo:
        pos_52s = round((ultimo - minimo) / (maximo - minimo) * 100, 1)

    return {
        "precio": round(ultimo, 2),
        "var_1d_pct": variacion(1),
        "var_7d_pct": variacion(5),
        "var_30d_pct": variacion(21),
        "var_90d_pct": variacion(63),
        "volatilidad_30d_anualizada_pct": vol_30d,
        "z_score_ultimo_dia": z_score,
        "pos_rango_52s_pct": pos_52s,
        "minimo_52s": round(minimo, 2),
        "maximo_52s": round(maximo, 2),
        "ultima_fecha": cierre.index[-1].strftime("%Y-%m-%d"),
    }


def dia_desde_estado(estado):
    """Extrae de un estado el registro compacto de un día."""
    meta = estado.get("meta", {})
    sello = meta.get("actualizado_utc", "")
    fecha = sello[:10] if len(sello) >= 10 else None
    if not fecha:
        return None
    return {
        "fecha": fecha,
        "actualizado_utc": sello,
        "senales": estado.get("senales", []),
    }


def backfill_desde_git():
    """
    Reconstruye historial a partir de los commits previos de mcg_estado.json.
    Se ejecuta una sola vez, cuando todavía no existe el archivo de historial.
    Si algo falla, devuelve lo que haya podido recuperar sin romper el job.
    """
    import subprocess
    dias = {}
    try:
        log = subprocess.run(
            ["git", "log", "--format=%H", "--", "docs/mcg_estado.json"],
            capture_output=True, text=True, cwd=RAIZ, timeout=90,
        )
        hashes = [h.strip() for h in log.stdout.split() if h.strip()]
        print(f"  backfill: {len(hashes)} versiones previas encontradas en el repositorio")
        for h in hashes:
            try:
                sh = subprocess.run(
                    ["git", "show", f"{h}:docs/mcg_estado.json"],
                    capture_output=True, text=True, cwd=RAIZ, timeout=30,
                )
                if not sh.stdout.strip():
                    continue
                d = dia_desde_estado(json.loads(sh.stdout))
                if d and d["fecha"] not in dias:
                    dias[d["fecha"]] = d
            except Exception:
                continue
    except Exception as e:
        print(f"  aviso: no se pudo reconstruir historial desde el repositorio ({e})")
    return dias


def actualizar_historial(estado):
    """Agrega el día actual al historial, sin duplicar fechas."""
    dias = {}
    if os.path.exists(HISTORIAL):
        try:
            with open(HISTORIAL, encoding="utf-8") as f:
                prev = json.load(f)
            for d in prev.get("dias", []):
                if d.get("fecha"):
                    dias[d["fecha"]] = d
        except Exception as e:
            print(f"  aviso: historial ilegible, se reconstruye ({e})")
    else:
        print("  Primera vez: reconstruyendo historial desde el repositorio...")
        dias = backfill_desde_git()

    hoy = dia_desde_estado(estado)
    if hoy:
        dias[hoy["fecha"]] = hoy  # el día actual pisa cualquier corrida previa del mismo día

    ordenados = sorted(dias.values(), key=lambda d: d["fecha"], reverse=True)[:MAX_DIAS_HISTORIAL]

    # Recurrencia: cuántas veces marcó señal cada activo en el período registrado.
    recuento = {}
    for d in ordenados:
        for s in d.get("senales", []):
            k = s.get("ticker")
            if not k:
                continue
            r = recuento.setdefault(k, {
                "ticker": k, "nombre": s.get("nombre", ""),
                "teatro": s.get("teatro", ""), "conflicto": s.get("conflicto", ""),
                "veces": 0, "ultima_fecha": d["fecha"],
            })
            r["veces"] += 1
            if d["fecha"] > r["ultima_fecha"]:
                r["ultima_fecha"] = d["fecha"]
    recurrentes = sorted(recuento.values(), key=lambda r: (-r["veces"], r["ticker"]))

    salida = {
        "meta": {
            "producto": "Monitor de Conflictos Geopolíticos (MCG) — historial de señales",
            "organizacion": "Strathos Lab",
            "actualizado_utc": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
            "dias_registrados": len(ordenados),
            "desde": ordenados[-1]["fecha"] if ordenados else None,
            "hasta": ordenados[0]["fecha"] if ordenados else None,
            "encuadre": (
                "Registro histórico de movimientos fuera de rango. La recurrencia indica "
                "persistencia de tensión en un activo, no causalidad respecto del conflicto."
            ),
        },
        "recurrentes": recurrentes,
        "dias": ordenados,
    }
    with open(HISTORIAL, "w", encoding="utf-8") as f:
        json.dump(salida, f, ensure_ascii=False, indent=2)
    print(f"Historial actualizado: {len(ordenados)} días registrados "
          f"({salida['meta']['desde']} a {salida['meta']['hasta']})")


def main():
    if not os.path.exists(MAPA):
        print(f"ERROR: no encuentro el mapa en {MAPA}", file=sys.stderr)
        sys.exit(1)

    filas = leer_mapa(MAPA)
    tickers = sorted({f["ticker"] for f in filas})
    print(f"Mapa: {len(filas)} vínculos · {len(tickers)} tickers únicos")

    # Descarga en bloque con reintentos. Yahoo a veces limita las IPs de GitHub
    # y devuelve vacío o corta la conexión; en ese caso reintentamos con pausa
    # en lugar de dejar caer el job.
    print("Descargando precios (1 año de histórico)...")
    datos = None
    intentos = 3
    for intento in range(1, intentos + 1):
        try:
            datos = yf.download(
                tickers,
                period="1y",
                interval="1d",
                group_by="ticker",
                auto_adjust=True,
                progress=False,
                threads=False,  # más suave con el límite de tasa de Yahoo
            )
            if datos is not None and not datos.empty:
                break
            print(f"  intento {intento}/{intentos}: Yahoo devolvió vacío.")
        except Exception as e:
            print(f"  intento {intento}/{intentos} falló: {e}")
        if intento < intentos:
            time.sleep(15 * intento)  # pausa creciente: 15s, 30s

    # Si tras los reintentos no hay nada, conservamos el último estado bueno.
    if datos is None or datos.empty:
        print("Yahoo no devolvió datos tras varios intentos. "
              "Se conserva el último estado y no se sobrescribe. Reintentá más tarde.")
        sys.exit(0)

    metricas_por_ticker = {}
    fallidos = []
    for t in tickers:
        try:
            hist = datos[t] if len(tickers) > 1 else datos
            m = metricas(hist)
        except Exception as e:  # ticker ausente o respuesta vacía
            m = None
            print(f"  aviso: {t} -> {e}")
        if m is None:
            fallidos.append(t)
            print(f"  SIN DATOS: {t}")
        else:
            metricas_por_ticker[t] = m

    print(f"OK: {len(metricas_por_ticker)} · Sin datos: {len(fallidos)}")

    # Si no se resolvió ni un solo ticker, no pisamos el estado bueno con vacío.
    if not metricas_por_ticker:
        print("Ningún ticker devolvió datos. Se conserva el último estado. Reintentá más tarde.")
        sys.exit(0)

    # ---- Armado del estado, agrupado por teatro y conflicto ----
    teatros = {}
    for f in filas:
        m = metricas_por_ticker.get(f["ticker"])
        activo = {
            "ticker": f["ticker"],
            "nombre": f["nombre"],
            "tipo_activo": f["tipo_activo"],
            "vector": f["vector"],
            "recurso": f["recurso"],
            "justificacion": f["justificacion"],
            "datos": m,
            "estado_dato": "ok" if m else "sin_datos",
        }
        t = teatros.setdefault(f["teatro"], {"teatro": f["teatro"], "conflictos": {}})
        c = t["conflictos"].setdefault(
            f["conflicto"], {"conflict_id": int(f["conflict_id"]), "conflicto": f["conflicto"], "activos": []}
        )
        c["activos"].append(activo)

    # dict -> list, ordenado
    teatros_lista = []
    for t in sorted(teatros.values(), key=lambda x: x["teatro"]):
        t["conflictos"] = sorted(t["conflictos"].values(), key=lambda x: x["conflict_id"])
        teatros_lista.append(t)

    # ---- Señales: movimientos fuera de rango habitual (para revisar en el informe) ----
    senales = []
    for f in filas:
        m = metricas_por_ticker.get(f["ticker"])
        if not m or m.get("z_score_ultimo_dia") is None:
            continue
        if abs(m["z_score_ultimo_dia"]) >= UMBRAL_SIGMA:
            senales.append({
                "ticker": f["ticker"],
                "nombre": f["nombre"],
                "teatro": f["teatro"],
                "conflicto": f["conflicto"],
                "var_1d_pct": m["var_1d_pct"],
                "var_30d_pct": m["var_30d_pct"],
                "z_score": m["z_score_ultimo_dia"],
            })
    # el movimiento más extremo primero
    senales.sort(key=lambda x: abs(x["z_score"]), reverse=True)
    # deduplicar por ticker (un ticker puede estar en varios conflictos)
    vistos, senales_unicas = set(), []
    for s in senales:
        if s["ticker"] in vistos:
            continue
        vistos.add(s["ticker"])
        senales_unicas.append(s)

    estado = {
        "meta": {
            "producto": "Monitor de Conflictos Geopolíticos (MCG)",
            "organizacion": "Strathos Lab",
            "actualizado_utc": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
            "fuente_precios": "Yahoo Finance vía yfinance",
            "encuadre": (
                "Monitor de exposición. Registra movimientos de activos mapeados a conflictos. "
                "No establece causalidad entre el conflicto y el movimiento de precio."
            ),
            "umbral_senal_sigma": UMBRAL_SIGMA,
            "vinculos": len(filas),
            "tickers_ok": len(metricas_por_ticker),
            "tickers_sin_datos": fallidos,
        },
        "senales": senales_unicas[:15],
        "teatros": teatros_lista,
    }

    os.makedirs(SALIDA_DIR, exist_ok=True)
    with open(SALIDA, "w", encoding="utf-8") as f:
        json.dump(estado, f, ensure_ascii=False, indent=2)

    print(f"\nEscrito: {SALIDA}")
    print(f"Señales detectadas: {len(senales_unicas)}")

    # Registro histórico: permite mirar hacia atrás qué señales se activaron.
    try:
        actualizar_historial(estado)
    except Exception as e:
        print(f"AVISO: no se pudo actualizar el historial ({e}). El estado del día quedó guardado igual.")

    if fallidos:
        print(f"REVISAR estos tickers: {', '.join(fallidos)}")


if __name__ == "__main__":
    main()
