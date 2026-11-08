# Caché FAQ curada: T41–T44

La caché es una optimización previa al RAG, con la misma moderación, contexto y evidencia.
El chat no aprende ni persiste automáticamente respuestas. La carga es explícita y genera
respuestas con el recuperador y los gestores existentes; el archivo de entrada contiene
preguntas, nunca respuestas hardcodeadas.

## Aplicación manual en dos etapas

1. Revisar y aplicar **solo** `backend/migrations/0007_cache_faq_consistente.sql` después de
   0006. Conserva la credencial publishable actual para probar funcionalidad. No cambia RLS
   ni ACL de tablas: las RPC FAQ que necesitan acceder a `pregunta_fuente` (RLS ya activo
   sin policies) usan DEFINER acotado con objetos cualificados y permisos de ejecución
   explícitos. Esto no soluciona las ACL amplias preexistentes; el endurecimiento es 0008.
2. La FAQ anterior se conserva inactiva porque no tiene procedencia. Cargar de nuevo la
   pregunta desde una lista curada permite actualizarla con fuentes verificadas.
3. Probar Bloque 6 con 0007 antes del cambio de seguridad.
4. Configurar manualmente `SUPABASE_BACKEND_KEY` **solo en FastAPI y CLI**, comprobarla y
   revisar/aplicar posteriormente `0008_seguridad_supabase.sql`. Ver
   [la transición de credencial](seguridad-supabase.md). Nunca copiarla a React/VITE.

Ningún arranque, script o test aplica estas migraciones automáticamente.

## Carga explícita

Desde `backend/`, con el venv existente:

```powershell
.\venv\Scripts\python.exe scripts/cargar_faq.py cargar --archivo examples/faq_curadas.example.json
```

El archivo de ejemplo sirve como formato; **no sustituye el relevamiento de asesoría**.
Usar ese relevamiento como entrada final. La materia admite ID, código o nombre exacto
del catálogo. Comisión se normaliza (`COM-GENERAL`/`GENERAL` → NULL, `COM-F` → F), período
admite `YYYYC1`/`YYYYC2`, y `vigencia_hasta` opcional admite `YYYY-MM-DD`. Sin vencimiento
explícito no se inventa uno semestral: la validez depende también de todas las fuentes.

Cada pregunta se informa por número e ID, sin imprimir su texto ni la respuesta. Un error
en una entrada no impide procesar las siguientes; el proceso termina distinto de cero
si alguna falló o quedó sin evidencia/contexto. Los proveedores se cierran en `finally`.

La carga usa una única transacción para upsert y fuentes. Deduplica pregunta normalizada
y contexto, preservando ID y aciertos. Si la documentación utilizada es específica, esa
especificidad queda en el registro; no se convierte una fuente particular en FAQ general.

## HIT y MISS

El orquestador consulta FAQ una vez, después de resolver contexto y aclaraciones necesarias.
Genera un embedding Gemini/768 y lo reutiliza en RAG si hay MISS. Un HIT exige respuesta,
similitud, contexto, estado activo, vigencia, versiones actuales y fuentes completas.
Los IDs citados se comprueban y las fuentes se reconstruyen desde documentos/fragmentos,
con enlace real de Drive cuando está disponible. No se inventan enlaces.

La similitud FAQ se devuelve como `confianza`; `umbral` es `UMBRAL_CACHE_FAQ` (default 0.75).
La confianza primaria con la que se generó la FAQ también debe superar el umbral RAG
actual del tipo. El umbral RAG general vuelve a 0.70; los overrides de cronograma y
programa siguen siendo independientes. No se modifica `.env` para imponer valores.

Sin materia/comisión no compiten FAQ específicas arbitrarias. Un período explícito
exige coincidencia; sin período se buscan FAQ sin período. Si la fuente hizo necesario
guardar un período, enviarlo en el contexto del chat para obtener HIT. Cronograma exige
materia y comisión exactas. Programa mantiene el fallback común del RAG existente.

El contador se incrementa después de construir/validar `RespuestaChat`. Un fallo métrico
no impide entregar una respuesta válida. Los logs técnicos no incluyen preguntas,
respuestas, prompts, vectores ni claves.

## Regeneración T43

Un trigger invalida las FAQ relacionadas en la misma transacción que cambia el documento.
Reutiliza `indexado_en`, versión Drive y metadata; no agrega un contador documental nuevo.
La carga captura esas versiones antes del LLM y el upsert las compara al publicar,
bloqueando brevemente las filas fuente en orden estable. Si cambiaron, la FAQ no se activa.
El HIT vincula además la lectura de fuentes con el `generada_en` devuelto por el matcher,
evitando mezclar una respuesta antigua con las fuentes de una regeneración posterior.

Después de completar el sync y liberar su lease, se procesan como máximo
`FAQ_REGENERACION_MAXIMO=2` pendientes durante `FAQ_REGENERACION_PRESUPUESTO_SEGUNDOS=5`.
Un fallo o timeout conserva pendientes y no cambia el resultado documental ya confirmado.
El embedding de la pregunta se reutiliza durante regeneración. Las bajas nunca eliminan
físicamente el documento ni sus referencias.

Para procesar pendientes en otro cron o manualmente, sin sincronizar Drive:

```powershell
.\venv\Scripts\python.exe scripts/cargar_faq.py regenerar --limite 10 --presupuesto-segundos 60
```

`FAQ_REGENERACION_MAXIMO=0` o presupuesto 0 desactiva la fase automática. El presupuesto
es asíncrono: no puede detener físicamente un hilo HTTP ya iniciado. El transporte
Supabase tiene timeout configurable (`SUPABASE_TIMEOUT_SEGUNDOS`, default 5); el upsert
recibe además un límite UTC y revierte si se agotó antes de publicar. **T29 sigue pendiente:**
hay que medir el límite y la duración total en el entorno serverless antes de programarlo.

## Prueba HTTP posterior a 0007

```powershell
.\venv\Scripts\python.exe -m uvicorn app.main:app --reload
```

En otra terminal:

```powershell
$body = @{
  mensaje = '¿Cuándo es el primer parcial de Técnicas de Programación comisión F?'
  contexto = @{ periodo = '2026C2' }
} | ConvertTo-Json -Depth 4
Invoke-RestMethod -Method Post -Uri 'http://127.0.0.1:8000/chat' `
  -Headers @{ 'X-Sesion-Id' = '11111111-1111-4111-8111-111111111111' } `
  -ContentType 'application/json; charset=utf-8' -Body ([Text.Encoding]::UTF8.GetBytes($body))
```

Comprobar fuentes no vacías, contexto, advertencia temporal y `umbral=0.75` si hubo HIT.
Repetir con una pregunta no cargada para comprobar MISS → RAG, y con
`¿Cuándo empiezan las clases?` para verificar alcance GENERAL sin materia obligatoria.

## Validación y límites

La advertencia temporal se calcula por consulta con la misma regla para FAQ HIT y
RAG MISS: aparece ante temporalidad explícita, parciales, recuperatorios o intención
documental `cronograma`. Las consultas no temporales sobre contenidos, descripción
de materias o plan de estudios devuelven `null`. No se almacena dentro de la FAQ.

El incidente original de `cualcomienza` no se reprodujo en el recorrido actual. La
regresión HTTP verifica que un FAQ HIT conserve `la cual comienza`, responda 200 y
no invoque al LLM; no se introdujo una sustitución específica ni se alteraron datos.

```powershell
.\venv\Scripts\python.exe -m pytest -m "not integracion"
git diff --check
```

Los tests usan dobles de servicios y revisión estática de SQL. No constituyen una prueba
de atomicidad, permisos efectivos ni planificación de consultas en PostgreSQL. La
aplicación y comprobación reales de 0007/0008 son pasos manuales separados.
No se agrega HNSW/IVFFlat para una FAQ inicial; se priorizan exactitud e índices B-tree.
