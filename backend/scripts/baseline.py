"""Fase 0, prueba 4: baseline "siempre sin retraso" vs regresión logística con variables permitidas.

División temporal dentro del mes: días 1-23 entrenamiento, 24 en adelante prueba.
Uso: python scripts/baseline.py --datos data/interim/IAH_2026_03.parquet
"""
import argparse

import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score, f1_score, precision_score, recall_score, roc_auc_score
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler

SEMILLA = 42
PROHIBIDAS = {"DepTime", "DepDelay", "DepDel15", "TaxiOut", "WheelsOff", "WheelsOn", "TaxiIn", "ArrTime",
              "ArrDelay", "AirTime", "ActualElapsedTime", "CarrierDelay", "WeatherDelay", "NASDelay",
              "SecurityDelay", "LateAircraftDelay", "Cancelled", "Diverted", "ArrDel15"}
CLIMA = [f"{l}{v}" for l in ("o_", "d_") for v in
         ("temperature_2m", "precipitation", "snowfall", "cloud_cover", "wind_speed_10m", "wind_gusts_10m")]
CAT = ["DayOfWeek", "hora_salida", "Reporting_Airline", "Dest"]
NUM = ["Distance", "CRSElapsedTime"]
# ponytail: weather_code (categoría WMO) se omite en el baseline; precipitación/nieve/nubes ya lo cubren en parte.
# Month y DayofMonth no aplican: con un solo mes y división temporal, el día del mes de prueba nunca se vio.


def evaluar(nombre, y, prob):
    pred = (prob >= 0.5).astype(int)
    return {"modelo": nombre,
            "AUC": roc_auc_score(y, prob) if np.ptp(prob) else 0.5,
            "PR-AUC": average_precision_score(y, prob),
            "precision": precision_score(y, pred, zero_division=0),
            "recall": recall_score(y, pred),
            "F1": f1_score(y, pred)}


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--datos", default="data/interim/IAH_2026_03.parquet")
    p.add_argument("--corte", type=int, default=24, help="primer día del mes usado como prueba")
    args = p.parse_args()
    df, corte = pd.read_parquet(args.datos), args.corte
    df["hora_salida"] = df["CRSDepTime"] // 100

    tr, te = df[df["DayofMonth"] < corte], df[df["DayofMonth"] >= corte]
    print(f"Entrenamiento: {len(tr)} vuelos (retraso {tr['ArrDel15'].mean():.3f}) | "
          f"Prueba: {len(te)} vuelos (retraso {te['ArrDel15'].mean():.3f})")

    filas = [evaluar("siempre sin retraso", te["ArrDel15"], np.zeros(len(te)))]
    for nombre, num in [("logística sin clima", NUM), ("logística con clima", NUM + CLIMA)]:
        assert not PROHIBIDAS & set(CAT + num), "variable prohibida en las features"
        modelo = make_pipeline(
            ColumnTransformer([("cat", OneHotEncoder(handle_unknown="ignore", min_frequency=10), CAT),
                               ("num", StandardScaler(), num)]),
            LogisticRegression(class_weight="balanced", max_iter=2000, random_state=SEMILLA))
        modelo.fit(tr[CAT + num], tr["ArrDel15"])
        filas.append(evaluar(nombre, te["ArrDel15"], modelo.predict_proba(te[CAT + num])[:, 1]))

    print("\nMétricas en prueba (clase positiva = retrasado, umbral 0.5):")
    print(pd.DataFrame(filas).set_index("modelo").round(3).to_string())


if __name__ == "__main__":
    main()
