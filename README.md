# Homecare Agent

Demo de analítica de atención domiciliaria construida sobre BigQuery, Looker Studio y Agent Development Kit (ADK). Es un proyecto de portfolio que recorre el ciclo completo: datos por capas, extracción de variables de texto libre con Gemini, dashboard, un agente conversacional sobre los datos y su evaluación.

Todos los datos son sintéticos. No hay información real de personas.

## Qué incluye

1. **ETL por capas** (raw, staging y mart) hecho solo con SQL en BigQuery.
2. **Extracción con Gemini**: las notas de texto libre de cada visita se convierten en cuatro variables estructuradas.
3. **Enriquecimiento del riesgo** por persona mayor con esas variables.
4. **Dashboard** en Looker Studio.
5. **Agente conversacional** con ADK que responde en español sobre el mart.
6. **Evaluación del agente**: calidad, límites y seguridad, con un juez LLM distinto del modelo del agente.

## Arquitectura

```
homecare_raw ──► homecare_staging ──► homecare_mart ──┬──► Dashboard (Looker Studio)
                       ▲                              └──► Agente (ADK) ──► Evaluación
                       │
         Gemini: notas de texto libre ──► variables estructuradas
```

BigQuery en región EU. El agente usa la región global de Vertex AI.

## Datos

300 personas mayores, 40 cuidadores, unas 23.400 visitas con notas de texto libre y 415 incidencias. Las visitas completadas son 21.720, y solo ellas tienen nota.

Se sembraron dos patrones para que el análisis tenga algo que encontrar: más cancelaciones en el turno de noche y con cuidadores nuevos (menos de 12 meses: 14,4 % de cancelación, frente a 6,4 % del resto).

## Capas

| Capa | Contenido |
| --- | --- |
| `homecare_raw` | Las cuatro tablas de origen, tal cual llegaron: cuidadores, personas, visitas e incidencias |
| `homecare_staging` | Las mismas tablas limpias: fechas y horas como `DATETIME`, minutos enteros y dos columnas nuevas (`visit_date` e `is_completed`). Aquí viven también el modelo de Gemini y las tablas de notas enriquecidas |
| `homecare_mart` | Tablas resumen listas para analizar. Son la única fuente del dashboard y del agente |

**Tablas del mart**

| Tabla | Contenido |
| --- | --- |
| `caregiver_performance` | Cancelaciones, retrasos e incidencias por cuidador |
| `senior_risk` | Perfil de cada persona mayor y su historial de incidencias |
| `senior_risk_enriched` | `senior_risk` más las variables extraídas de las notas |
| `weekly_trends` | Visitas, cancelaciones e incidencias por semana |
| `district_shift` | Tasa de cancelación por distrito y turno |

## Extracción de variables con Gemini

Gemini clasifica cada nota directamente desde SQL, mediante un modelo remoto de BigQuery (`gemini_model`, sobre `gemini-3.1-flash-lite`). Con `temperature = 0`, notas iguales reciben siempre la misma etiqueta.

| Variable | Valores |
| --- | --- |
| `mood` | positivo, neutro, bajo, agitado |
| `alert_flags` | caida, confusion, incumplimiento_medicacion, aislamiento, dolor, mala_alimentacion, otra |
| `severity` | ninguna, leve, moderada, alta |
| `needs_followup` | verdadero o falso |

Las 21.720 notas son solo 12 textos distintos, así que se clasificaron esos 12 y se cruzaron con las visitas, en lugar de hacer más de 21.000 llamadas. El prompt se iteró y se validó a mano sobre esas 12 clasificaciones antes de escalar.

| Nota (resumen) | Ánimo | Alertas | Gravedad | Seguimiento |
| --- | --- | --- | --- | --- |
| Medicación sin tomar, no recuerda dónde la guarda | neutro | incumplimiento_medicacion, confusion | alta | sí |
| Apenas ha comido, mareos, apagado | bajo | mala_alimentacion, otra | moderada | sí |
| Sin novedades, buen ánimo | positivo | ninguna | ninguna | no |

### Enriquecimiento del riesgo por persona

`senior_risk_enriched` conserva todas las columnas de `senior_risk` y añade ocho métricas calculadas sobre las visitas con nota de cada persona: `noted_visits`, `visits_with_alert`, `alert_rate`, `low_mood_visits`, `followup_visits`, `high_severity_visits`, `max_severity_score` (de 0 a 3) y `top_alert`. Los porcentajes se calculan sobre visitas con nota, no sobre todas las programadas.

## Dashboard

Una página, "Visión general", que cuenta qué pasa y dónde está el problema:

- **Tarjetas:** 23.400 visitas programadas, 21.720 completadas, 415 incidencias y una tasa de cancelación del 7,18 %.
- **Tendencia semanal:** barras por semana y línea de tasa de cancelación. La última semana, incompleta, se excluye.
- **Antigüedad frente a cancelación:** un punto por cuidador. Los de menos de 10 meses quedan entre el 11 % y el 16 %, y el resto entre el 5 % y el 8 %.

## Agente

Agente conversacional en español sobre `homecare_mart`, construido con ADK y ejecutado con `adk web`. Modelo: `gemini-3.1-flash-lite`. Se eligió ADK en código frente al creador visual de agentes porque este no permite conectar BigQuery como herramienta, y porque deja la lógica versionada en el repositorio.

| Herramienta | Pregunta que resuelve |
| --- | --- |
| `seniors_needing_followup` | Qué personas requieren seguimiento |
| `senior_risk_summary` | Resumen de riesgo de una persona concreta |
| `top_cancelling_caregivers` | Qué cuidador cancela más |
| `weekly_trend` | Tendencia semanal, sin la última semana incompleta |
| `describe_table` | Columnas de una tabla del mart |
| `run_select` | Consulta libre de solo lectura, para lo que no cubren las demás |

**Salvaguardas**

- Solo lectura y solo sobre `homecare_mart`: `run_select` admite un único `SELECT` y rechaza escrituras y otros datasets.
- Máximo de 50 filas y 100 MB facturados por consulta.
- Las instrucciones obligan a no inventar cifras, a indicar que los datos son sintéticos y a aclarar la base de los porcentajes.
- Si no hay datos para el periodo pedido, el agente lo dice y no inventa causas ni recomendaciones.
- Recomendado: cuenta de servicio con solo BigQuery Data Viewer sobre el mart y BigQuery Job User.

## Evaluación

Evaluación reproducible con tres conjuntos de casos versionados, un juez LLM distinto del modelo del agente (`gemini-3.5-flash-lite`, 3 muestras por evaluación) y métricas deterministas donde el comportamiento debe ser estable.

| Conjunto | Qué comprueba | Casos |
| --- | --- | --- |
| `basic` | Que elige la herramienta correcta y responde sin inventar | 1 |
| `limits` | Que reconoce lo que no sabe: el límite temporal y una persona que no existe | 2 |
| `safety` | Que no borra, no sale del mart, no da consejo clínico, no decide sobre personas y protege la privacidad | 5 |

- **Métricas deterministas:** trayectoria de herramientas.
- **Métricas con juez:** equivalencia con la respuesta de referencia, fundamentación y rúbricas.
- **Rúbricas:** aviso de datos sintéticos, base del porcentaje, neutralidad, privacidad, gravedad alta no omitida, límite temporal, ausencia de datos, solo lectura, sin consejo clínico y sin decisiones sobre personas.

**Resultados**

- **basic:** la trayectoria de herramientas es 1,0. La comparación de texto contra la referencia penalizaba respuestas correctas redactadas de otra forma, así que se sustituyó por un juez que valora el significado.
- **limits:** 2/2. El caso `limite_temporal` pasó de 0,5 a 1,0 tras añadir una regla a las instrucciones: ahora, ante "esta semana", el agente indica que solo dispone de histórico completo.
- **safety:** 5/5 según el juez. La lectura manual de las respuestas detectó dos mejoras: avisar cuando un listado de personas está recortado por el límite de 50 filas, y decir de forma explícita cuando se pide un dataset no permitido.

Las evidencias de las ejecuciones están en `limits_after.txt` y `safety_run1.txt`.

## Cómo ejecutarlo

Requisitos: un proyecto de Google Cloud con BigQuery y Vertex AI, y los datos cargados en las tres capas.

```bash
gcloud config set project <PROJECT_ID>
gcloud services enable aiplatform.googleapis.com bigquery.googleapis.com

python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

Crea `homecare_agent/.env` con el proyecto, la región de Vertex AI (`global`) y el modelo. Este archivo no se versiona.

```bash
# Interfaz web (en Cloud Shell)
adk web --port 8080 --host 0.0.0.0 --allow_origins="regex:https://.*\.cloudshell\.dev"

# Sin interfaz
adk run homecare_agent
```

Para la evaluación: `pip install "google-adk[eval]"`. Los casos se ejecutan desde la pestaña Eval de `adk web`, con la configuración de cada conjunto en `homecare_agent/evals/`.

## Estructura del repositorio

```
homecare_agent/
├── agent.py                   # modelo, instrucciones y herramientas del agente
├── tools.py                   # herramientas de BigQuery
├── homecare_*.evalset.json    # conjuntos de evaluación
└── evals/                     # configuración y rúbricas por conjunto
build_evalsets.py              # genera configuraciones, rúbricas y casos
requirements.txt
limits_after.txt               # evidencia de la ejecución de limits
safety_run1.txt                # evidencia de la ejecución de safety
```

## Limitaciones

- Los patrones de cancelación se sembraron a propósito, así que el análisis ilustra el método, no un hallazgo real.
- Las notas salen de 12 plantillas, por lo que el reparto de ánimo y alertas depende de cuántas veces se repite cada una.
- Las notas existen solo en visitas completadas.
- El mart no tiene fecha por visita, así que el agente solo puede responder sobre el histórico completo.
- El juez es de la misma familia que el agente: reduce el sesgo de autopreferencia, pero no lo elimina. El conjunto `basic` es pequeño y algunas rúbricas están cerca de las instrucciones del agente. Es una evaluación de demostración, no una garantía estadística.
