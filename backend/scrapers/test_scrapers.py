"""Pruebas de los scrapers.

    python -m scrapers.test_scrapers                       # solo parseo con el fragmento guardado (sin red)
    SCRAPERS_EN_VIVO=1 python -m scrapers.test_scrapers    # además 1-2 peticiones reales a Avionio

Los sitios sin permiso (FR24, Airportia, FlightAware) solo se prueban con fixtures: nunca en vivo desde aquí.
"""
import json
import os
import tempfile
import warnings
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from scrapers import airportia, avionio, flightaware, flightradar24, sin_permiso

FIXTURE = Path(__file__).parent / "fixtures" / "avionio_iah_salidas.html"
CAMPOS = {"fuente", "numero", "aerolinea", "aerolinea_nombre", "destino_iata", "salida_local", "salida_utc",
          "estado", "es_codeshare", "operador_iata", "codeshares", "real"}


def test_parseo():
    vuelos = avionio.parsear_salidas(FIXTURE.read_text(encoding="utf-8"), "IAH")
    assert len(vuelos) == 7, len(vuelos)  # la fila de navegación "Previous flights" se ignora
    for v in vuelos:
        assert set(v) == CAMPOS, set(v) ^ CAMPOS
        assert v["real"]["llegada_programada_local"] is None and v["real"]["llegada_real_local"] is None

    ua = vuelos[0]
    assert ua["numero"] == "UA 1328" and ua["aerolinea"] == "UA" and ua["aerolinea_nombre"] == "United Airlines"
    assert ua["destino_iata"] == "ATL" and ua["salida_local"] == "2026-10-28T20:00"
    assert ua["salida_utc"] == "2026-10-29T01:00Z"  # CDT = UTC-5 el 28 de octubre (zoneinfo, no desfase fijo)
    assert ua["es_codeshare"] is False and ua["operador_iata"] == "UA"
    assert ua["codeshares"] == ["EK 6087", "NZ 6327"]
    assert ua["real"]["salida_real_local"] == "2026-10-28T20:08" and ua["estado"] == "Departed 20:08"

    ek = vuelos[1]
    assert ek["numero"] == "EK 6087" and ek["es_codeshare"] is True and ek["operador_iata"] == "UA"

    ua483 = vuelos[3]  # aún no sale: "Estimated" no es una hora real
    assert ua483["estado"] == "Estimated 21:15" and ua483["real"]["salida_real_local"] is None

    oo = vuelos[5]  # regional: Avionio usa el número del operador (SkyWest)
    assert oo["numero"] == "OO 4998" and oo["operador_iata"] == "OO" and oo["codeshares"] == ["AS 6376"]


def test_horario_invierno():
    # Tras el cambio de horario (1 nov 2026) el desfase pasa a UTC-6: debe salir de zoneinfo, no de una constante.
    html = FIXTURE.read_text(encoding="utf-8").replace('data-ts="1793217600000"', 'data-ts="1795032000000"')
    v = avionio.parsear_salidas(html, "IAH")[0]
    assert v["salida_local"] == "2026-11-18T20:00" and v["salida_utc"] == "2026-11-19T02:00Z", v


def test_robots_sin_query():
    assert avionio._permitido("https://www.avionio.com/en/airport/iah/departures?ts=1&page=1") is False


def test_comparar():
    vuelos = avionio.parsear_salidas(FIXTURE.read_text(encoding="utf-8"), "IAH")
    adb = {"departures": [
        {"number": "UA 1328", "movement": {"scheduledTime": {"local": "2026-10-28 20:00-05:00"}}},
        {"number": "UA 4998", "movement": {"scheduledTime": {"local": "2026-10-28 20:04-05:00"}}},
        {"number": "UA 9999", "movement": {"scheduledTime": {"local": "2026-10-28 20:02-05:00"}}},
    ]}
    with tempfile.TemporaryDirectory() as d:
        ruta = Path(d) / "adb.json"
        ruta.write_text(json.dumps(adb), encoding="utf-8")
        r = avionio.comparar_con_aerodatabox(vuelos, ruta)
    assert r["coinciden"] == 1 and r["coinciden_sin_prefijo"] == 2, r
    assert ("UA 9999", "2026-10-28T20:02") in r["solo_aerodatabox"], r


def test_en_vivo():
    ahora = datetime.now(ZoneInfo("America/Chicago")).replace(tzinfo=None)
    vuelos = avionio.obtener_salidas("IAH", ahora - timedelta(hours=3), ahora + timedelta(hours=3))
    assert vuelos, "la página no devolvió salidas en ±3 h"
    assert all(set(v) == CAMPOS for v in vuelos)
    assert any(not v["es_codeshare"] for v in vuelos)
    print(f"  en vivo: {len(vuelos)} salidas, de {vuelos[0]['salida_local']} a {vuelos[-1]['salida_local']}")


DESCARGA = datetime(2026, 10, 29, 1, 52, 51, tzinfo=timezone.utc)  # 20:52 CDT, cuando se guardaron los fixtures


def test_flightradar24():
    html = (FIXTURE.parent / "fr24_iah_salidas.html").read_text(encoding="utf-8")
    vuelos = flightradar24.parsear_salidas(html, "IAH", DESCARGA)
    assert [v["numero"] for v in vuelos] == ["Y4 731", "F9 6731", "N170XY", "UA 1024", "NZ 9940"], vuelos
    assert all(set(v) == CAMPOS for v in vuelos)
    y4, f9, priv, ua = vuelos[0], vuelos[1], vuelos[2], vuelos[3]
    assert y4["aerolinea"] == "Y4" and y4["aerolinea_nombre"] == "Volaris" and y4["destino_iata"] == "MEX"
    assert y4["salida_local"] == "2026-10-28T20:47" and y4["salida_utc"] == "2026-10-29T01:47Z"
    assert y4["codeshares"] == ["F9 6731"] and y4["es_codeshare"] is False and y4["operador_iata"] == "Y4"
    assert f9["es_codeshare"] is True and f9["aerolinea_nombre"] == "Frontier" and f9["operador_iata"] == "Y4"
    assert priv["aerolinea"] is None  # matrícula de un vuelo privado, no un número de vuelo
    # Sin fechas en la página: 7:35 AM después de 8:47 PM es el día siguiente.
    assert ua["salida_local"] == "2026-10-29T07:35" and ua["salida_utc"] == "2026-10-29T12:35Z"
    assert ua["estado"] == "Estimated dep. 7:35 AM" and ua["real"]["salida_real_local"] is None


def test_airportia():
    html = (FIXTURE.parent / "airportia_iah_salidas.html").read_text(encoding="utf-8")
    vuelos = airportia.parsear_salidas(html, "IAH", DESCARGA)
    assert [v["numero"] for v in vuelos] == ["UA 1716", "EK 6120", "OO 5476", "AC 4645"], vuelos
    assert all(set(v) == CAMPOS for v in vuelos)
    ua, ek, oo = vuelos[0], vuelos[1], vuelos[2]
    assert ua["salida_local"] == "2026-10-28T20:30" and ua["destino_iata"] == "LAS" and ua["estado"] == "Scheduled"
    assert ua["codeshares"] == ["EK 6120"] and ek["es_codeshare"] is True and ek["operador_iata"] == "UA"
    assert oo["aerolinea_nombre"] == "SkyWest Airlines" and oo["real"]["salida_real_local"] == "2026-10-28T20:40"


def test_sin_permiso_desactivado():
    os.environ.pop("SCRAPERS_SIN_PERMISO", None)
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        assert flightradar24.obtener_salidas() == [] and airportia.obtener_salidas() == []
        assert flightaware.obtener_salidas() == []
    assert len(w) == 3 and all("desactivado" in str(x.message) for x in w)


def test_bloqueo_regresa_vacio():
    # Simula una página de reto: el patrón común debe registrar "bloqueado" y regresar [] sin reintentar.
    os.environ["SCRAPERS_SIN_PERMISO"] = "1"
    original, registro, llamadas = sin_permiso.descargar, sin_permiso.REGISTRO, []
    sin_permiso.REGISTRO = Path(tempfile.gettempdir()) / "pia_bloqueos_prueba.log"  # no ensuciar el log real
    def reto(sitio, url):
        llamadas.append(url)
        raise sin_permiso.Bloqueado("página de reto anti-bot")
    sin_permiso.descargar = reto
    try:
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            assert flightradar24.obtener_salidas() == []
        assert len(llamadas) == 1 and "bloqueado" in str(w[0].message)
    finally:
        sin_permiso.descargar, sin_permiso.REGISTRO = original, registro
        os.environ.pop("SCRAPERS_SIN_PERMISO", None)


if __name__ == "__main__":
    pruebas = [test_parseo, test_horario_invierno, test_robots_sin_query, test_comparar,
               test_flightradar24, test_airportia, test_sin_permiso_desactivado, test_bloqueo_regresa_vacio]
    if os.environ.get("SCRAPERS_EN_VIVO") == "1":
        pruebas.append(test_en_vivo)
    for p in pruebas:
        p()
        print("ok", p.__name__)
    print(f"{len(pruebas)} pruebas pasaron" + ("" if test_en_vivo in pruebas else " (en vivo omitida: SCRAPERS_EN_VIVO=1)"))
