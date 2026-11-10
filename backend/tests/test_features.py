"""Prueba de las reglas delicadas de src/features.py. Ejecutar: python -m tests.test_features"""
import pandas as pd

from src.features import VARS_CLIMA, DuracionMediana, agregar_contexto, construir_variables

T = pd.Timestamp


def main():
    vuelos = pd.DataFrame({
        "salida_local": [T("2026-03-07 10:00"), T("2026-03-09 10:00"), T("2026-03-09 10:30")],
        "origen": "IAH", "destino": "DEN", "aerolinea": "UA", "duracion_prog": [150, 160, 170]})

    horas = pd.date_range("2026-03-06", "2026-03-11", freq="h")
    clima = pd.concat([pd.DataFrame({"time": horas, "aeropuerto": a, **{v: range(len(horas)) for v in VARS_CLIMA},
                                     "weather_code": 0}) for a in ("IAH", "DEN")])
    spc = pd.DataFrame({  # Día 2 válido 2026-03-09 12Z a 03-10 12Z: uno emitido a tiempo, otro tarde
        "aeropuerto": "IAH", "threshold": ["SLGT", "HIGH"], "utc_issue": T("2026-03-09 12:00"),
        "utc_expire": T("2026-03-10 12:00"), "utc_product_issue": [T("2026-03-08 06:00"), T("2026-03-08 17:00")]})
    nws = pd.DataFrame({  # aviso tropical emitido 30 h antes (cuenta) y aviso de tornado 1 h antes (no cuenta)
        "aeropuerto": "IAH", "phenomena": ["TR", "TO"], "significance": "W", "name": "",
        "issue": [T("2026-03-08 09:00"), T("2026-03-09 14:00")], "expire": T("2026-03-10 00:00")})

    df = agregar_contexto(vuelos, clima, spc, nws)
    # CST (-6) antes del 8 de marzo, CDT (-5) después; 10:30 local redondea hacia arriba (15:30Z -> 16Z).
    assert list(df["hora_utc"]) == [T("2026-03-07 16:00"), T("2026-03-09 15:00"), T("2026-03-09 16:00")]
    assert df.loc[0, "o_temperature_2m"] == (T("2026-03-07 16:00") - horas[0]) / pd.Timedelta("1h")
    # Salida 2026-03-09 15Z: el HIGH se emitió 22 h antes (no cuenta); el SLGT 33 h antes (cuenta).
    assert list(df["o_spc"]) == [0, 3, 3] and list(df["d_spc"]) == [0, 0, 0]
    assert list(df["o_nws_tropical"]) == [0, 1, 1] and list(df["o_nws_tormenta"]) == [0, 0, 0]

    X = construir_variables(df)
    assert abs(X.loc[0, "distancia"] - 862) < 10  # IAH-DEN ≈ 862 millas (BTS)
    dur = DuracionMediana().fit(X.iloc[:2])  # la mediana solo usa las filas de ajuste
    assert dur.transform(X.drop(columns="duracion_prog"))["duracion"].tolist() == [155, 155, 155]
    print("OK: features")


if __name__ == "__main__":
    main()
