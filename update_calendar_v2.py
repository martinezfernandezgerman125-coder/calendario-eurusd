#!/usr/bin/env python3
"""
Actualiza el calendario EUR-USD (FED + BCE):
- Anade reuniones nuevas que Fed/BCE publiquen en sus paginas oficiales.
- Anade discursos de Nivel 1 (Chair/President) y Nivel 2 (Vice Chair/
  Vice-President) filtrando por CARGO, no por nombre, para que no haga
  falta tocar el codigo cuando cambie la persona.
- Actualiza un evento "testigo" con la fecha de la ultima comprobacion
  con exito, para que se pueda ver a simple vista en el calendario si
  el robot sigue vivo.

IMPORTANTE: este script depende de la estructura HTML actual de
federalreserve.gov y ecb.europa.eu. Si alguna de esas webs cambia de
diseno, las funciones de scraping pueden dejar de encontrar datos sin
lanzar un error visible -- por eso el evento "testigo" es la senal de
que algo puede haberse roto (si lleva 3+ dias sin actualizarse).
"""

import re
import datetime
import requests
from bs4 import BeautifulSoup
from icalendar import Calendar, Event, Alarm
import pytz

ICS_PATH = "calendario_eurusd_NUEVO.ics"
UTC = pytz.UTC

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; EURUSD-Calendario-Personal/1.0)"}

# ----------------------------------------------------------------------
# NIVEL 1 / NIVEL 2 -- por cargo, no por nombre
# ----------------------------------------------------------------------
FED_NIVEL_1 = ["chairman", "chair "]  # "Chair " con espacio evita falsos
                                        # positivos con "Vice Chair"
FED_NIVEL_2 = ["vice chair"]           # cubre "Vice Chair" y
                                        # "Vice Chair for Supervision"

ECB_NIVEL_1 = ["president of the ecb", "ecb president"]
ECB_NIVEL_2 = ["vice-president", "vice president"]


def es_nivel_fed(titulo_linea: str):
    t = titulo_linea.lower()
    if any(k in t for k in FED_NIVEL_1) and "vice" not in t:
        return "Nivel 1 (Chair)"
    if any(k in t for k in FED_NIVEL_2):
        return "Nivel 2 (Vice Chair)"
    return None


def es_nivel_ecb(titulo_linea: str):
    t = titulo_linea.lower()
    if any(k in t for k in ECB_NIVEL_1):
        return "Nivel 1 (President)"
    if any(k in t for k in ECB_NIVEL_2):
        return "Nivel 2 (Vice-President)"
    return None


# ----------------------------------------------------------------------
# REUNIONES -- Fed
# ----------------------------------------------------------------------
def fetch_fed_meetings():
    """
    Lee el calendario oficial de reuniones del FOMC y devuelve una lista
    de tuplas (fecha_decision: date, tiene_sep: bool).
    Estructura esperada de la pagina: bloques de texto con el mes y un
    rango de dias, y una marca de "*" o similar en los meses con SEP.
    NOTA: esta funcion es la mas expuesta a romperse si la Fed rediseña
    su web -- revisar el testigo tras el primer despliegue.
    """
    url = "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm"
    resp = requests.get(url, headers=HEADERS, timeout=20)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")
    text = soup.get_text("\n")

    meetings = []
    # Patron: "January 27-28" o "March 17-18*" (asterisco = con SEP)
    pattern = re.compile(
        r"(January|February|March|April|May|June|July|August|September|"
        r"October|November|December)\s+(\d{1,2})-(\d{1,2})(\*?)"
    )
    year_pattern = re.compile(r"\b(20\d{2})\b")

    current_year = datetime.datetime.now(UTC).year
    for match in pattern.finditer(text):
        month_name, _, day_end, star = match.groups()
        month_num = datetime.datetime.strptime(month_name, "%B").month
        # Aproximacion de año: si el mes ya paso hace mucho respecto a
        # "ahora", asumimos que es del año siguiente (la pagina suele
        # publicar 1-2 años vista sin repetir el año en cada linea).
        year = current_year
        try:
            meeting_date = datetime.date(year, month_num, int(day_end))
        except ValueError:
            continue
        if meeting_date < datetime.date.today() - datetime.timedelta(days=200):
            meeting_date = datetime.date(year + 1, month_num, int(day_end))
        meetings.append((meeting_date, bool(star)))

    return meetings


# ----------------------------------------------------------------------
# REUNIONES -- BCE
# ----------------------------------------------------------------------
def fetch_ecb_meetings():
    """
    Lee el calendario oficial de reuniones del Consejo de Gobierno y
    devuelve una lista de tuplas (fecha_decision: date, tiene_proyecciones:
    bool). Solo se quedan las reuniones DE POLITICA MONETARIA (se
    descartan las "non-monetary policy meeting" y las del General Council).
    """
    url = "https://www.ecb.europa.eu/press/calendars/mgcgc/html/index.en.html"
    resp = requests.get(url, headers=HEADERS, timeout=20)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")
    text = soup.get_text("\n")

    meetings = []
    date_pattern = re.compile(r"(\d{2})/(\d{2})/(\d{4})")
    lines = text.split("\n")
    for i, line in enumerate(lines):
        m = date_pattern.match(line.strip())
        if not m:
            continue
        day, month, year = map(int, m.groups())
        desc = " ".join(lines[i + 1 : i + 3]).lower()
        if "day 2" in desc and "press conference" in desc and "monetary policy meeting" in desc:
            fecha = datetime.date(year, month, day)
            meetings.append(fecha)

    # Proyecciones: marzo, junio, septiembre, diciembre
    result = []
    for fecha in meetings:
        tiene_proy = fecha.month in (3, 6, 9, 12)
        result.append((fecha, tiene_proy))
    return result


# ----------------------------------------------------------------------
# DISCURSOS -- Fed
# ----------------------------------------------------------------------
def fetch_fed_speeches(meses_adelante=2):
    """
    Recorre las paginas mensuales de eventos de la Fed (mes actual y los
    siguientes 'meses_adelante') y devuelve discursos de Nivel 1/2 como
    tuplas (fecha: date, nivel: str, titulo_evento: str).
    """
    resultados = []
    hoy = datetime.date.today()
    for offset in range(meses_adelante + 1):
        mes_objetivo = (hoy.month - 1 + offset) % 12 + 1
        anio_objetivo = hoy.year + (hoy.month - 1 + offset) // 12
        nombre_mes = datetime.date(anio_objetivo, mes_objetivo, 1).strftime("%Y-%B").lower()
        url = f"https://www.federalreserve.gov/newsevents/{nombre_mes}.htm"
        try:
            resp = requests.get(url, headers=HEADERS, timeout=20)
            resp.raise_for_status()
        except requests.RequestException:
            continue
        soup = BeautifulSoup(resp.text, "html.parser")
        text = soup.get_text("\n")
        lines = [l.strip() for l in text.split("\n") if l.strip()]

        for i, line in enumerate(lines):
            if line.startswith(("Speech", "Discussion", "Panel Discussion")):
                nivel = es_nivel_fed(line)
                if nivel:
                    dia = None
                    for j in range(i, min(i + 6, len(lines))):
                        if lines[j].isdigit() and int(lines[j]) <= 31:
                            dia = int(lines[j])
                            break
                    if dia:
                        try:
                            fecha = datetime.date(anio_objetivo, mes_objetivo, dia)
                        except ValueError:
                            continue
                        if fecha >= hoy:
                            resultados.append((fecha, f"FED - {nivel}", line))
    return resultados


# ----------------------------------------------------------------------
# DISCURSOS -- BCE
# ----------------------------------------------------------------------
def fetch_ecb_speeches():
    """
    Lee el listado de discursos del BCE y devuelve discursos de Nivel 1/2
    como tuplas (fecha: date, nivel: str, titulo_evento: str).
    """
    url = "https://www.ecb.europa.eu/press/key/html/index.en.html"
    resultados = []
    try:
        resp = requests.get(url, headers=HEADERS, timeout=20)
        resp.raise_for_status()
    except requests.RequestException:
        return resultados
    soup = BeautifulSoup(resp.text, "html.parser")
    text = soup.get_text("\n")
    lines = [l.strip() for l in text.split("\n") if l.strip()]

    date_pattern = re.compile(r"^(\d{1,2})\s+(January|February|March|April|May|June|July|"
                               r"August|September|October|November|December)\s+(\d{4})$")
    hoy = datetime.date.today()
    for i, line in enumerate(lines):
        m = date_pattern.match(line)
        if not m:
            continue
        day, month_name, year = m.groups()
        contexto = " ".join(lines[max(0, i - 3):i]).lower()
        nivel = es_nivel_ecb(contexto)
        if nivel:
            fecha = datetime.date(int(year), datetime.datetime.strptime(month_name, "%B").month, int(day))
            if fecha >= hoy:
                resultados.append((fecha, f"BCE - {nivel}", contexto[:80]))
    return resultados


# ----------------------------------------------------------------------
# CONSTRUCCION DEL .ICS
# ----------------------------------------------------------------------
def uid_reunion(banco, fecha):
    return f"{banco.lower()}-{fecha.strftime('%Y%m%d')}@eurusd-calendario"


def uid_discurso(banco, fecha, titulo):
    slug = re.sub(r"[^a-z0-9]+", "-", titulo.lower())[:40]
    return f"discurso-{banco.lower()}-{fecha.strftime('%Y%m%d')}-{slug}@eurusd-calendario"


def cargar_calendario_existente():
    try:
        with open(ICS_PATH, "rb") as f:
            return Calendar.from_ical(f.read())
    except FileNotFoundError:
        cal = Calendar()
        cal.add("prodid", "-//Proyecto EURUSD Institucional//Calendario//ES")
        cal.add("version", "2.0")
        cal.add("x-wr-calname", "EUR-USD Calendario (FED-BCE)")
        return cal


def uids_existentes(cal):
    return {str(e.get("UID")) for e in cal.walk("VEVENT")}


def anadir_evento(cal, uid, dt_utc, summary, description):
    ev = Event()
    ev.add("uid", uid)
    ev.add("dtstamp", datetime.datetime.now(UTC))
    ev.add("dtstart", dt_utc)
    ev.add("summary", summary)
    ev.add("description", description)
    for dias, texto in [(14, "Empieza la investigacion"), (1, "Manana hay evento")]:
        alarm = Alarm()
        alarm.add("action", "DISPLAY")
        alarm.add("description", texto)
        alarm.add("trigger", datetime.timedelta(days=-dias))
        ev.add_component(alarm)
    cal.add_component(ev)


def actualizar_testigo(cal):
    uid = "testigo-ultima-comprobacion@eurusd-calendario"
    # eliminar el testigo anterior si existe
    nuevos_componentes = [c for c in cal.subcomponents if str(c.get("UID", "")) != uid]
    cal.subcomponents = nuevos_componentes

    ahora = datetime.datetime.now(UTC)
    ev = Event()
    ev.add("uid", uid)
    ev.add("dtstamp", ahora)
    ev.add("dtstart", ahora.date())
    ev.add("summary", f"Ultima comprobacion automatica: {ahora.strftime('%d/%m/%Y')}")
    ev.add("description", "Si esta fecha lleva 3 o mas dias sin cambiar, el robot puede haberse roto.")
    cal.add_component(ev)


def main():
    cal = cargar_calendario_existente()
    existentes = uids_existentes(cal)

    # --- Reuniones Fed y BCE: DESACTIVADO ---
    # El 16/09/2026 se detecto que fetch_fed_meetings() interpretaba mal
    # el texto de la pagina oficial y creaba una reunion casi cada dia.
    # Las reuniones de Fed y BCE cambian muy pocas veces al año y ya
    # estan verificadas hasta finales de 2027 en el archivo base, asi
    # que se mantienen manuales (revision periodica) en vez de
    # automaticas. Si en el futuro se quiere reactivar, arreglar antes
    # fetch_fed_meetings()/fetch_ecb_meetings() y probarlas SIN escribir
    # en el calendario real (imprimir resultados primero).

    # --- Discursos Fed ---
    try:
        for fecha, nivel, titulo in fetch_fed_speeches():
            uid = uid_discurso("fed", fecha, titulo)
            if uid not in existentes:
                dt_utc = datetime.datetime.combine(fecha, datetime.time(17, 0), tzinfo=UTC)
                anadir_evento(cal, uid, dt_utc, f"Discurso {nivel}", titulo)
                existentes.add(uid)
    except Exception as e:
        print(f"[AVISO] fallo al leer discursos Fed: {e}")

    # --- Discursos BCE ---
    try:
        for fecha, nivel, titulo in fetch_ecb_speeches():
            uid = uid_discurso("ecb", fecha, titulo)
            if uid not in existentes:
                dt_utc = datetime.datetime.combine(fecha, datetime.time(12, 0), tzinfo=UTC)
                anadir_evento(cal, uid, dt_utc, f"Discurso {nivel}", titulo)
                existentes.add(uid)
    except Exception as e:
        print(f"[AVISO] fallo al leer discursos BCE: {e}")

    # --- Testigo (siempre se actualiza si el script llega hasta aqui) ---
    actualizar_testigo(cal)

    with open(ICS_PATH, "wb") as f:
        f.write(cal.to_ical())

    print("Calendario actualizado correctamente.")


if __name__ == "__main__":
    main()
