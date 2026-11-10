"""Prueba del backend sin gastar cuota. Ejecutar desde la raíz: python -m api.test_api

Usa el JSON real guardado de AeroDataBox (AERODATABOX_DESDE_ARCHIVO), un modelo falso entrenado con filas
sintéticas (en un directorio temporal, nunca en models/) y contexto de clima/SPC/NWS simulado.
Requiere httpx (lo usa fastapi.testclient).
"""
import os
import sys
import tempfile
from pathlib import Path

import joblib
import numpy as np
import pandas as pd

RAIZ = Path(__file__).resolve().parents[1]
DATA = RAIZ.parent / "data"  # data/ vive fuera de backend/
T = pd.Timestamp


def clima_falso(codigos, t0, t1):
    horas = pd.date_range(t0.floor("D") - pd.Timedelta(days=1), t1.ceil("D") + pd.Timedelta(days=1), freq="h")
    rng = np.random.default_rng(0)
    return pd.concat([pd.DataFrame({
        "time": horas, "aeropuerto": c, "temperature_2m": rng.normal(20, 5, len(horas)),
        "precipitation": rng.exponential(0.3, len(horas)), "snowfall": 0.0, "cloud_cover": rng.uniform(0, 100, len(horas)),
        "wind_speed_10m": rng.uniform(0, 30, len(horas)), "wind_gusts_10m": rng.uniform(5, 60, len(horas)),
        "cape": rng.uniform(0, 1500, len(horas)), "weather_code": rng.choice([0, 3, 61, 95], len(horas))})
        for c in sorted(set(codigos))], ignore_index=True)


SPC_VACIO = pd.DataFrame({"aeropuerto": pd.Series(dtype=str), "threshold": pd.Series(dtype=str),
                          **{c: pd.Series(dtype="datetime64[ns]") for c in ("utc_issue", "utc_expire", "utc_product_issue")}})
NWS_VACIO = pd.DataFrame({**{c: pd.Series(dtype=str) for c in ("aeropuerto", "phenomena", "significance", "name")},
                          "issue": pd.Series(dtype="datetime64[ns]"), "expire": pd.Series(dtype="datetime64[ns]")})


def bundle_falso(ruta):
    """Bundle con las mismas claves que src.entrenar, ajustado sobre filas sintéticas que pasan por agregar_contexto."""
    from sklearn.linear_model import LogisticRegression

    from src.entrenar import ENTRADA, pipeline
    from src.features import agregar_contexto
    rng = np.random.default_rng(42)
    n = 600
    aerolineas = ["AA", "AS", "B6", "DL", "F9", "NK", "UA", "WN"]
    vuelos = pd.DataFrame({
        "salida_local": (T("2026-01-01") + pd.to_timedelta(rng.integers(0, 50 * 24, n), "h")).astype("datetime64[ns]"),
        "origen": "IAH", "destino": rng.choice(["DEN", "LAX", "ORD", "ATL", "DFW", "MIA", "SFO", "LGA"], n),
        "aerolinea": rng.choice(aerolineas, n), "duracion_prog": rng.integers(60, 300, n).astype(float)})
    df = agregar_contexto(vuelos, clima_falso([*vuelos["destino"], "IAH"], T("2026-01-01"), T("2026-02-21")),
                          SPC_VACIO, NWS_VACIO)
    y = (rng.uniform(size=n) < 0.1 + 0.02 * df["salida_local"].dt.hour / 2).astype(int)
    modelo = pipeline(LogisticRegression(max_iter=2000)).fit(df[ENTRADA], y)
    joblib.dump({"modelo": modelo, "nombre": "falso_logistica", "umbral": 0.3, "entrada": ENTRADA,
                 "entrenado_hasta": "2026-03-01", "aerolineas": aerolineas,
                 "destinos": sorted(vuelos["destino"].unique())}, ruta)


def contexto_falso(codigos, utc_min, utc_max):
    """Clima aleatorio; en IAH un SLGT y un aviso tropical emitidos a tiempo, y un aviso de tornado tardío."""
    # Panorama Día 2: válido de 12Z a 12Z del día siguiente, emitido ~30 h antes de su inicio.
    inicio = pd.date_range(utc_min.floor("D") - pd.Timedelta(hours=12), utc_max, freq="24h")
    spc = pd.DataFrame({"aeropuerto": "IAH", "threshold": "SLGT", "utc_issue": inicio,
                        "utc_expire": inicio + pd.Timedelta(hours=24),
                        "utc_product_issue": inicio - pd.Timedelta(hours=30)})
    nws = pd.DataFrame({"aeropuerto": "IAH", "phenomena": ["TR", "TO"], "significance": "W",
                        "name": ["Tropical Storm Warning", "Tornado Warning"],
                        "issue": [utc_min - pd.Timedelta(hours=48), utc_min - pd.Timedelta(hours=1)],
                        "expire": [utc_max + pd.Timedelta(hours=6)] * 2})
    for df, cols in ((spc, ["utc_issue", "utc_expire", "utc_product_issue"]), (nws, ["issue", "expire"])):
        for c in cols:
            df[c] = df[c].astype("datetime64[ns]")
    return clima_falso(codigos, utc_min, utc_max), spc, nws


def main():
    tmp = Path(tempfile.mkdtemp(prefix="pia_api_"))
    archivo = sorted((DATA / "raw" / "api").glob("*.json"))[-1]
    os.environ.update({"CACHE_DIR": str(tmp / "cache"), "MODELO_PATH": str(tmp / "modelo.joblib"),
                       "AERODATABOX_DESDE_ARCHIVO": str(archivo)})
    os.environ.pop("MODELO_PROVISIONAL", None)

    from fastapi.testclient import TestClient

    from api import contexto, main as m
    contexto.obtener = contexto_falso  # sin red
    c = TestClient(m.app)
    q = {"aeropuerto": "IAH", "desde": "2026-10-29T06:00", "hasta": "2026-10-29T17:59"}

    r = c.get("/api/vuelos", params=q)
    assert r.status_code == 503 and r.json() == {"detail": "modelo no disponible: ejecuta python -m src.entrenar"}, r.text

    bundle_falso(tmp / "modelo.joblib")
    assert c.get("/api/vuelos", params={**q, "hasta": "2026-10-29T18:01"}).status_code == 422
    r = c.get("/api/vuelos", params={**q, "hasta": "2026-10-29T05:00"})
    assert r.status_code == 422 and isinstance(r.json()["detail"], str)
    assert c.get("/api/vuelos", params={**q, "aeropuerto": "DFW"}).status_code == 400

    r = c.get("/api/vuelos", params=q)
    assert r.status_code == 200, r.text
    d = r.json()
    assert set(d) == {"aeropuerto", "generado", "fuente", "clima", "modelo", "vuelos"} and d["clima"] == "ok"
    assert d["aeropuerto"]["iata"] == "IAH" and d["aeropuerto"]["tz"] == "America/Chicago"
    assert d["fuente"] in {"api", "cache"} and d["modelo"] == {"nombre": "falso_logistica", "umbral": 0.3,
                                                               "provisional": False}
    vs = d["vuelos"]
    claves = {"id", "numero", "aerolinea", "aerolinea_nombre", "destino", "salida_local", "salida_utc", "estado",
              "probabilidad", "prediccion", "factores", "excluido", "contexto", "clima_aproximado", "real"}
    assert vs and all(set(v) == claves for v in vs)
    assert len({v["id"] for v in vs}) == len(vs), "ids repetidos"
    motivos = pd.Series([v["excluido"] for v in vs]).value_counts(dropna=False)
    print(f"{len(vs)} vuelos; exclusiones:\n{motivos.to_string()}")

    por_num = {v["numero"]: v for v in vs}
    assert por_num["UA 6114"]["excluido"] == "codeshare duplicado"  # IsCodeshared en el JSON real
    assert all(v["excluido"] for v in vs if v["destino"]["iata"] in {"PVR", "CUN", "NRT", "LHR"})
    assert "vuelo internacional" in motivos and "destino sin coordenadas" in motivos
    for v in vs:
        if v["excluido"]:
            assert v["probabilidad"] is None and v["prediccion"] is None
        else:
            p = v["probabilidad"]
            assert 0 <= p <= 1 and v["prediccion"] == ("retrasado" if p >= 0.3 else "a_tiempo")
            o = v["contexto"]["origen"]
            assert o["spc"] == "SLGT" and o["avisos_nws"] == ["Tropical Storm Warning"], o
            assert v["contexto"]["destino"]["temperatura"] is not None
        assert v["real"] == {"llegada_retraso_min": None, "retrasado": None}
    assert sum(not v["excluido"] for v in vs) > 200

    uno = next(v for v in vs if not v["excluido"])
    assert c.get(f"/api/vuelos/{uno['id']}").json() == uno
    assert c.get("/api/vuelos/XX0-2000-01-01").status_code == 404

    s = c.get("/api/salud").json()
    assert s["modelo"]["disponible"] and s["cache"]["predicciones"]["archivos"] == 1, s

    # Si Open-Meteo falla se avisa qué consulta falló: con predicción guardada se sirve esa, sin ella 503.
    def clima_caido(*_):
        raise OSError("Open-Meteo no responde")
    contexto.obtener = clima_caido
    d = c.get("/api/vuelos", params=q).json()
    assert d["fallo"] == "clima" and d["fuente"] == "cache" and "Open-Meteo" in d["fallo_detalle"], d.keys()
    r = c.get("/api/vuelos", params={**q, "desde": "2026-10-29T07:00", "hasta": "2026-10-29T18:59"})  # sin predicción guardada
    assert r.status_code == 503 and r.json()["fallo"] == "clima", r.text
    contexto.obtener = contexto_falso

    # Simulador: valida entradas y devuelve un vuelo con el mismo formato que la lista.
    sim = {"origen": "IAH", "destino": "ORD", "aerolinea": uno["aerolinea"], "salida_local": "2026-07-15T17:00"}
    r = c.post("/api/simular", json=sim)
    assert r.status_code == 200, r.text
    assert 0 <= r.json()["probabilidad"] <= 1 and r.json()["destino"]["iata"] == "ORD"
    assert c.post("/api/simular", json={**sim, "aerolinea": "ZZ"}).status_code == 400
    assert c.post("/api/simular", json={**sim, "clima_destino": {"spc": 9}}).status_code == 422
    assert c.get("/api/opciones").json()["origenes"]

    # Aviación privada sin "number" (visto en vivo: N500WA) se excluye en vez de tronar con KeyError.
    from datetime import datetime
    from api import vuelos as av
    priv = {"departures": [{"callSign": "N500WA", "codeshareStatus": "IsOperator", "airline": {"name": "Unknown/Private owner"},
                            "movement": {"airport": {"iata": "SAT", "countryCode": "US"},
                                         "scheduledTime": {"local": "2026-11-04 09:00-06:00", "utc": "2026-11-04 15:00Z"}}}]}
    [p] = av.poblacion(priv, "IAH", {"UA"}, datetime(2026, 11, 4, 8), datetime(2026, 11, 4, 20))
    assert p["excluido"] == "sin número de vuelo" and p["numero"] == "N500WA", p
    print("OK: api")


if __name__ == "__main__":
    sys.exit(main())
