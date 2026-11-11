"""Factores de riesgo por vuelo: cuánto sube o baja cada variable la estimación del modelo.

Solo aplica a la regresión logística: su puntaje es una suma de peso × valor, así que la aportación de cada
variable es peso × (valor del vuelo − valor promedio del entrenamiento), sumada sobre sus columnas one-hot.
La calibración sigmoide es monótona, así que el orden de los factores no cambia.
No es la causa del retraso (que no se conoce antes del vuelo): es lo que más mueve la estimación.
"""
from pathlib import Path

import numpy as np
import pandas as pd

from src.features import CAT, NUM

RAIZ = Path(__file__).resolve().parents[1]
DATA = RAIZ.parent / "data"  # data/ vive fuera de backend/
FONDO = RAIZ / "models" / "fondo.parquet"  # muestra del entrenamiento para el valor promedio
MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre", "octubre",
         "noviembre", "diciembre"]
DIAS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
SPC_ES = {1: "general", 2: "marginal", 3: "ligero", 4: "aumentado", 5: "moderado", 6: "alto"}
GRUPO_ES = {"tropical": "tormenta tropical", "invierno": "invierno", "inundacion": "inundación",
            "tormenta": "tormenta severa", "otro": "otro tipo"}
WMO = {0: "despejado", 1: "mayormente despejado", 2: "parcialmente nublado", 3: "nublado", 45: "niebla",
       48: "niebla con escarcha", 51: "llovizna ligera", 53: "llovizna", 55: "llovizna intensa",
       56: "llovizna helada", 57: "llovizna helada", 61: "lluvia ligera", 63: "lluvia moderada", 65: "lluvia fuerte",
       66: "lluvia helada", 67: "lluvia helada fuerte", 71: "nieve ligera", 73: "nieve", 75: "nieve fuerte",
       77: "granos de nieve", 80: "chubascos ligeros", 81: "chubascos", 82: "chubascos fuertes",
       85: "chubascos de nieve", 86: "chubascos de nieve fuertes", 95: "tormenta eléctrica",
       96: "tormenta con granizo", 99: "tormenta con granizo fuerte"}
N_FONDO = 20_000  # vuelos del entrenamiento para el valor promedio
MIN_PESO = 0.03   # aportaciones menores (en log-momios) no se muestran


def _pipeline(bundle):
    """El pipeline de regresión logística dentro del modelo calibrado, o None si el modelo es otro."""
    try:
        p = bundle["modelo"].calibrated_classifiers_[0].estimator.estimator
    except (AttributeError, IndexError):
        p = bundle["modelo"]
    return p if "logisticregression" in getattr(p, "named_steps", {}) else None


def _denso(m):
    return m.toarray() if hasattr(m, "toarray") else np.asarray(m)


_FONDO = {}  # id(bundle) -> fondo; el bundle se recarga solo si cambia el archivo


def _fondo(bundle):
    """(grupo de cada columna transformada, pesos, promedio de cada columna en el entrenamiento)."""
    if id(bundle) not in _FONDO:
        _FONDO.clear()
        _FONDO[id(bundle)] = _calcular_fondo(bundle)
    return _FONDO[id(bundle)]


def _calcular_fondo(bundle):
    pipe = _pipeline(bundle)
    nombres = pipe.named_steps["columntransformer"].get_feature_names_out()
    cats = sorted(CAT, key=len, reverse=True)

    def grupo(n):
        tipo, resto = n.split("__", 1)
        return resto if tipo == "num" else next(c for c in cats if resto.startswith(c + "_"))

    grupos = np.array([grupo(n) for n in nombres])
    dataset = DATA / "processed" / "dataset.parquet"
    if dataset.exists():  # local: muestra fresca, y se guarda para el deploy (data/ no se versiona)
        df = pd.read_parquet(dataset, columns=[*bundle["entrada"]])
        df = df[df["salida_local"] < pd.Timestamp(bundle["entrenado_hasta"])]
        muestra = df.sample(min(N_FONDO, len(df)), random_state=42)
        muestra.to_parquet(FONDO, index=False)
    else:  # ponytail: en Render se usa la muestra versionada; si reentrenas, corre la API local una vez y haz commit
        muestra = pd.read_parquet(FONDO)
    media = _denso(pipe[:-1].transform(muestra)).mean(axis=0)
    return grupos, pipe.named_steps["logisticregression"].coef_[0], media


def _etiqueta(var, f, origen, destino):
    """Texto en español del factor, con el valor de este vuelo."""
    ap = origen if var.startswith("o_") else destino
    base = var[2:] if var[:2] in ("o_", "d_") else var
    v = f[var]
    textos = {
        "origen": lambda: f"Sale de {origen}", "aerolinea": lambda: f"Aerolínea {v}", "destino": lambda: f"Destino {destino}",
        "mes": lambda: f"Mes de {MESES[int(v) - 1]}", "dia_semana": lambda: f"Vuela en {DIAS[int(v)]}",
        "hora": lambda: f"Sale a las {int(v)}:00", "dia_mes": lambda: f"Día {int(v)} del mes",
        "distancia": lambda: f"Distancia de {v:,.0f} millas", "duracion": lambda: f"Duración estimada de {v:.0f} min",
        "temperature_2m": lambda: f"Temperatura de {v:.0f} °C en {ap}", "precipitation": lambda: f"Lluvia de {v:.1f} mm en {ap}",
        "snowfall": lambda: f"Nieve de {v:.1f} cm en {ap}", "cloud_cover": lambda: f"Nubosidad de {v:.0f} % en {ap}",
        "wind_speed_10m": lambda: f"Viento de {v:.0f} km/h en {ap}", "wind_gusts_10m": lambda: f"Ráfagas de {v:.0f} km/h en {ap}",
        "cape": lambda: f"Energía de tormenta (CAPE) de {v:.0f} J/kg en {ap}",
    }
    if base == "weather_code":
        return f"Pronóstico en {ap}: {WMO.get(int(float(v)), f'código {v}')}"
    if base == "spc":
        return f"Riesgo {SPC_ES[int(v)]} de tormentas (SPC) en {ap}" if v > 0 else f"Sin riesgo de tormentas en {ap}"
    if base.startswith("nws_"):
        g = GRUPO_ES[base[4:]]
        return f"{int(v)} aviso(s) de {g} en {ap}" if v > 0 else f"Sin avisos de {g} en {ap}"
    return textos[base]()


def tipo(var):
    return "clima" if var[:2] in ("o_", "d_") else "vuelo"


N_DETALLE = 14  # variables (por |peso|) que recibe la explicación por IA


def calcular(bundle, X, n_suben=4, n_bajan=2):
    """Lista (una por fila de X) con {'suben', 'bajan', 'detalle'}; None si el modelo no es lineal.

    'suben'/'bajan' son los principales (los de la tarjeta). 'detalle' son las N_DETALLE variables que más
    mueven la estimación en cualquier sentido, incluidas las de efecto pequeño: así la explicación por IA puede
    hablar de más parámetros sin inventar su efecto."""
    pipe = _pipeline(bundle)
    if pipe is None:
        return [None] * len(X)
    grupos, pesos, media = _fondo(bundle)
    aporte = (_denso(pipe[:-1].transform(X)) - media) * pesos       # vuelos × columnas
    vars_ = list(dict.fromkeys(grupos))
    por_var = np.stack([aporte[:, grupos == g].sum(axis=1) for g in vars_], axis=1)
    valores = pipe[:2].transform(X).reset_index(drop=True)          # las 36 variables ya construidas
    salida = []
    for i in range(len(X)):
        f = valores.iloc[i]
        orden = np.argsort(-por_var[i])
        item = lambda j: {"texto": _etiqueta(vars_[j], f, f["origen"], f["destino"]), "tipo": tipo(vars_[j]),
                          "peso": round(float(por_var[i, j]), 3)}
        salida.append({"suben": [item(j) for j in orden[:n_suben] if por_var[i, j] >= MIN_PESO],
                       "bajan": [item(j) for j in orden[::-1][:n_bajan] if por_var[i, j] <= -MIN_PESO],
                       "detalle": [item(j) for j in np.argsort(-np.abs(por_var[i]))[:N_DETALLE]]})
    return salida


assert set(CAT + NUM) >= {"hora", "o_spc"}  # las etiquetas dependen de estos nombres


if __name__ == "__main__":
    # python -m api.factores: la suma de aportaciones debe igualar puntaje(vuelo) − puntaje(promedio).
    import joblib
    b = joblib.load(RAIZ / "models" / "modelo.joblib")
    pipe = _pipeline(b)
    X = pd.read_parquet(DATA / "processed" / "dataset.parquet", columns=b["entrada"]).tail(200)
    grupos, pesos, media = _fondo(b)
    z = pipe.decision_function(X)
    aporte = ((_denso(pipe[:-1].transform(X)) - media) * pesos).sum(axis=1)
    lr = pipe.named_steps["logisticregression"]
    assert np.allclose(aporte, z - (media @ pesos + lr.intercept_[0])), "las aportaciones no cuadran"
    assert all(f["suben"] or f["bajan"] for f in calcular(b, X))
    print("OK: factores")
