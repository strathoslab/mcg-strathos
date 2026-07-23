#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Monitor de Conflictos Geopolíticos (MCG) — Strathos Lab
Briefing Diario Matutino vía Email + Gemini AI.
"""

import os
import sys
import json
import requests
import xml.etree.ElementTree as ET
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from google import genai

# ==========================================
# 1. RUTAS Y CONFIGURACIÓN DE ENTORNO
# ==========================================
RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RUTA_ESTADO = os.path.join(RAIZ, "docs", "mcg_estado.json")

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
SENDER_EMAIL = os.getenv("SENDER_EMAIL")
SENDER_PASSWORD = os.getenv("SENDER_PASSWORD")
RECIPIENT_EMAIL = os.getenv("RECIPIENT_EMAIL")

if not all([GEMINI_API_KEY, SENDER_EMAIL, SENDER_PASSWORD, RECIPIENT_EMAIL]):
    print("ERROR: Faltan variables de entorno.")
    sys.exit(1)


# ==========================================
# 2. BÚSQUEDA DE NOTICIAS EN GOOGLE NEWS RSS
# ==========================================
def obtener_noticias_rss(query):
    url = f"https://news.google.com/rss/search?q={query}+when:1d&hl=es-419&gl=AR&ceid=AR:es-419"
    noticias = []
    try:
        resp = requests.get(url, timeout=10)
        if resp.status_code == 200:
            root = ET.fromstring(resp.content)
            for item in root.findall('.//item')[:3]:
                title = item.find('title').text
                link = item.find('link').text
                noticias.append(f"- {title} | Link: {link}")
    except Exception as e:
        print(f"Aviso: Error en RSS para '{query}': {e}")
    return noticias


# ==========================================
# 3. LECTURA DEL ESTADO DEL MONITOR
# ==========================================
def cargar_estado():
    if not os.path.exists(RUTA_ESTADO):
        print(f"ERROR: No existe el archivo de estado en {RUTA_ESTADO}")
        sys.exit(1)
    with open(RUTA_ESTADO, "r", encoding="utf-8") as f:
        return json.load(f)


# ==========================================
# 4. ENVÍO DE EMAIL HTML
# ==========================================
def enviar_email(html_content, total_senales):
    asunto = f"🌍 [Strathos Lab] Briefing Geopolítico ({total_senales} señales activas)"
    
    msg = MIMEMultipart('alternative')
    msg['Subject'] = asunto
    msg['From'] = SENDER_EMAIL
    msg['To'] = RECIPIENT_EMAIL

    msg.attach(MIMEText(html_content, 'html'))

    try:
        server = smtplib.SMTP('smtp.gmail.com', 587)
        server.starttls()
        server.login(SENDER_EMAIL, SENDER_PASSWORD)
        server.sendmail(SENDER_EMAIL, RECIPIENT_EMAIL, msg.as_string())
        server.quit()
        print("Email enviado exitosamente.")
    except Exception as e:
        print(f"ERROR al enviar email vía SMTP: {e}")
        sys.exit(1)


# ==========================================
# 5. EJECUCIÓN PRINCIPAL
# ==========================================
def main():
    print("Cargando estado del monitor...")
    estado = cargar_estado()
    
    senales = estado.get("senales", [])
    meta = estado.get("meta", {})
    actualizado_utc = meta.get("actualizado_utc", "N/A")

    print(f"Señales detectadas hoy: {len(senales)}")

    noticias_contexto = []
    if senales:
        for s in senales[:5]:
            query = f"{s['nombre']} {s['conflicto']}"
            noticias_contexto.extend(obtener_noticias_rss(query))
    else:
        noticias_contexto.extend(obtener_noticias_rss("mercados geopolitica commodities"))

    noticias_str = "\n".join(noticias_contexto) if noticias_contexto else "No se encontraron noticias recientes específicas."

    prompt = f"""
    Actúa como un analista sénior de inteligencia geopolítica para Strathos Lab.
    Analiza las señales registradas hoy por el Monitor de Conflictos Geopolíticos (MCG) y el contexto de noticias:

    METADATOS DEL MONITOR:
    - Fecha/Hora de Actualización: {actualizado_utc}
    - Total de Señales Detectadas (z-score >= {meta.get('umbral_senal_sigma', 1.5)}): {len(senales)}

    SEÑALES DESTACADAS HOY (JSON):
    {json.dumps(senales, ensure_ascii=False, indent=2)}

    NOTICIAS DE CONTEXTO RECIENTES (RSS):
    {noticias_str}

    Instrucciones de formato y tono:
    1. Genera directamente el código HTML interno (empezando por un <div> contenedor) para un mail elegante de lectura rápida.
    2. Usa estilos CSS inline simples (font-family: Arial, sans-serif; color: #111; line-height: 1.5).
    3. Estructura recomendada:
       - <h2 style="color: #0d3b66;">🚨 Alertas Críticas y Variaciones Inusuales</h2>
         (Explica brevemente los movimientos más extremos y por qué llaman la atención).
       - <h2 style="color: #0d3b66;">📰 Noticias y Contexto Clave</h2>
         (Resume en 2 o 3 viñetas las noticias vinculadas e incluye los hipervínculos HTML a las fuentes proporcionadas).
       - <h2 style="color: #0d3b66;">📈 Tickers e Impacto en Mercado</h2>
         (Menciona los activos/tickers a vigilar hoy).
       - <h2 style="color: #0d3b66;">💡 Conclusión Estratégica</h2>
         (Una frase de síntesis estilo Strathos Lab).

    No incluyas etiquetas ```html de código al principio ni al final. Solo devuelve el HTML limpio.
    """

    print("Consultando a Gemini AI para redactar el brief...")
    try:
        client = genai.Client(api_key=GEMINI_API_KEY)
        respuesta = client.models.generate_content(
            model='gemini-2.5-flash',
            contents=prompt,
        )
        html_brief = respuesta.text.strip()
        
        if html_brief.startswith("```html"):
            html_brief = html_brief.replace("```html", "").replace("```", "")
        if html_brief.startswith("```"):
            html_brief = html_brief.replace("```", "")

    except Exception as e:
        print(f"ERROR consultando la API de Gemini: {e}")
        sys.exit(1)

    print("Enviando mail matutino...")
    enviar_email(html_brief, len(senales))


if __name__ == "__main__":
    main()
