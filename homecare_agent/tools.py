"""Herramientas de BigQuery para el agente de atención domiciliaria.

Solo lectura y solo sobre homecare_mart. Columnas verificadas contra
INFORMATION_SCHEMA el 2026-11-02.
"""
import datetime
import decimal
import os
import re

from google.cloud import bigquery

PROJECT = os.getenv("GOOGLE_CLOUD_PROJECT", "qidas-513417")
MART = f"{PROJECT}.homecare_mart"
BQ_LOCATION = "EU"
MAX_ROWS = 50
MAX_BYTES_BILLED = 100 * 1024 * 1024  # 100 MB por consulta

# Columnas de identificación (verificadas).
SENIOR_ID = "senior_id"
CAREGIVER_ID = "caregiver_id"

ALLOWED_TABLES = {
    "caregiver_performance",
    "senior_risk",
    "weekly_trends",
    "district_shift",
    "senior_risk_enriched",
}

_client = bigquery.Client(project=PROJECT, location=BQ_LOCATION)


def _clean(v):
    """Convierte fechas y decimales a tipos serializables en JSON."""
    if isinstance(v, (datetime.date, datetime.datetime, datetime.time)):
        return v.isoformat()
    if isinstance(v, decimal.Decimal):
        return float(v)
    return v


def _run(sql: str, params: list | None = None) -> dict:
    cfg = bigquery.QueryJobConfig(
        query_parameters=params or [],
        maximum_bytes_billed=MAX_BYTES_BILLED,
    )
    try:
        rows = _client.query(sql, job_config=cfg).result(max_results=MAX_ROWS)
        return {
            "status": "ok",
            "rows": [{k: _clean(v) for k, v in dict(r).items()} for r in rows],
        }
    except Exception as e:  # el agente verá el error y podrá corregir
        return {"status": "error", "message": str(e)}


def describe_table(table: str) -> dict:
    """Devuelve las columnas y tipos de una tabla del mart.

    Args:
        table: nombre de la tabla (p. ej. 'senior_risk_enriched').
    """
    if table not in ALLOWED_TABLES:
        return {"status": "error", "message": f"Tablas válidas: {sorted(ALLOWED_TABLES)}"}
    t = _client.get_table(f"{MART}.{table}")
    return {
        "status": "ok",
        "columns": [{"name": f.name, "type": f.field_type} for f in t.schema],
        "rows": t.num_rows,
    }


def seniors_needing_followup(limit: int = 10) -> dict:
    """Personas mayores con más visitas que requieren seguimiento,
    ordenadas por gravedad máxima y número de visitas de gravedad alta.
    Las cifras se calculan sobre visitas con nota, no sobre las programadas.

    Args:
        limit: número máximo de personas (1 a 50).
    """
    limit = max(1, min(int(limit), MAX_ROWS))
    sql = f"""
        SELECT *
        FROM `{MART}.senior_risk_enriched`
        WHERE followup_visits > 0
        ORDER BY max_severity_score DESC, high_severity_visits DESC,
                 followup_visits DESC
        LIMIT @n
    """
    return _run(sql, [bigquery.ScalarQueryParameter("n", "INT64", limit)])


def senior_risk_summary(senior_id: str) -> dict:
    """Resumen de riesgo de una persona mayor concreta.

    Args:
        senior_id: identificador de la persona mayor.
    """
    sql = f"""
        SELECT *
        FROM `{MART}.senior_risk_enriched`
        WHERE CAST({SENIOR_ID} AS STRING) = @id
        LIMIT 1
    """
    res = _run(sql, [bigquery.ScalarQueryParameter("id", "STRING", str(senior_id))])
    if res["status"] == "ok" and not res["rows"]:
        return {"status": "not_found", "message": f"No existe {senior_id}"}
    return res


def top_cancelling_caregivers(limit: int = 10) -> dict:
    """Cuidadores ordenados por tasa de cancelación (de mayor a menor).

    Args:
        limit: número máximo de cuidadores (1 a 50).
    """
    limit = max(1, min(int(limit), MAX_ROWS))
    sql = f"""
        SELECT *
        FROM `{MART}.caregiver_performance`
        ORDER BY cancel_rate DESC, scheduled_visits DESC
        LIMIT @n
    """
    return _run(sql, [bigquery.ScalarQueryParameter("n", "INT64", limit)])


def weekly_trend(weeks: int = 12) -> dict:
    """Tendencia semanal de visitas, cancelaciones e incidencias.
    Excluye la última semana, que está incompleta.

    Args:
        weeks: número de semanas recientes a devolver (1 a 50).
    """
    weeks = max(1, min(int(weeks), MAX_ROWS))
    sql = f"""
        SELECT * FROM `{MART}.weekly_trends`
        ORDER BY week_start DESC
        LIMIT @n OFFSET 1  -- OFFSET 1 descarta la última semana, incompleta
    """
    return _run(sql, [bigquery.ScalarQueryParameter("n", "INT64", weeks)])


def run_select(sql: str) -> dict:
    """Consulta libre de solo lectura sobre tablas del mart, para preguntas
    que las demás herramientas no cubren. Debe ser un único SELECT sobre
    tablas de homecare_mart. Usa describe_table antes para conocer columnas.

    Args:
        sql: sentencia SELECT en GoogleSQL.
    """
    s = sql.strip().rstrip(";")
    if not re.match(r"(?is)^(select|with)\b", s) or ";" in s:
        return {"status": "error", "message": "Solo se permite un único SELECT."}
    if re.search(r"(?i)\b(insert|update|delete|merge|drop|create|alter|truncate|grant|call|export)\b", s):
        return {"status": "error", "message": "Sentencia no permitida."}
    refs = set(re.findall(r"`?[\w-]+\.(\w+)\.(\w+)`?", s))
    for dataset, _tbl in refs:
        if dataset != "homecare_mart":
            return {"status": "error", "message": "Solo se puede consultar homecare_mart."}
    if "homecare_raw" in s or "homecare_staging" in s:
        return {"status": "error", "message": "Solo se puede consultar homecare_mart."}
    return _run(f"SELECT * FROM ({s}) LIMIT {MAX_ROWS}")
