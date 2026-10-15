# Proveedores de modelos de lenguaje

Capa que genera texto con un modelo de lenguaje y pasa a un proveedor de respaldo cuando el principal falla. Vive en `backend/app/providers/`. La lógica de negocio nunca llama a un proveedor directamente: pide un `GestorProveedores` y usa `generar_con_respaldo`.

## Arquitectura

| Archivo | Contenido |
| --- | --- |
| `base.py` | Contrato: `ProveedorLLM`, `SolicitudLLM`, `RespuestaLLM` y la jerarquía de excepciones. |
| `formato.py` | `armar_mensaje_usuario`: une contexto y consulta. Provisorio hasta T33. |
| `_http.py` | `ProveedorHTTP`: base común de los proveedores que usan `httpx` (cliente, tiempo máximo, fallas de transporte y mapeo general de estados). |
| `gemini.py` | `ProveedorGemini`: API REST `generateContent` de Google. |
| `groq.py` | `ProveedorGroq`: API REST `chat/completions` de Groq, compatible con OpenAI. |
| `gestor.py` | `GestorProveedores`: recorre los proveedores en orden, con presupuesto de tiempo. |
| `falso.py` | `ProveedorFalso`: resultados fijados de antemano, solo para pruebas y scripts. |
| `dependencias.py` | Registro nombre → clase y `obtener_gestor_proveedores()`, apta para `Depends`. |

Los dos proveedores hacen llamadas HTTP directas con `httpx`, sin los SDK oficiales. Cada uno:

- envía las instrucciones del sistema en el campo dedicado de su API (`systemInstruction` en Gemini, mensaje con rol `system` en Groq) y el contexto con la consulta como mensaje de usuario;
- aplica `temperatura` y `max_tokens` solo si la solicitud los trae;
- envía la clave por encabezado (`x-goog-api-key` en Gemini, `Authorization: Bearer` en Groq), nunca en la URL;
- corta la llamada al llegar a su tiempo máximo (`GEMINI_TIMEOUT_SEGUNDOS`, `GROQ_TIMEOUT_SEGUNDOS`);
- no reintenta: de eso se ocupa el gestor.

### Uso desde un endpoint

```python
from typing import Annotated
from fastapi import Depends
from app.providers import GestorProveedores, SolicitudLLM
from app.providers.dependencias import obtener_gestor_proveedores

async def responder(gestor: Annotated[GestorProveedores, Depends(obtener_gestor_proveedores)]):
    respuesta = await gestor.generar_con_respaldo(
        SolicitudLLM(instrucciones_sistema="...", contexto="...", consulta="...")
    )
```

El gestor se construye una sola vez por proceso, con los proveedores que tienen clave, en el orden de `LLM_ORDEN_PROVEEDORES`. Sin ninguna clave queda vacío y la aplicación arranca igual. En las pruebas se reemplaza con `app.dependency_overrides[obtener_gestor_proveedores]`.

## Traducción de errores

De un proveedor solo salen excepciones de la jerarquía de `base.py`. El atributo `recuperable` decide si el gestor prueba con el siguiente.

| Situación | Excepción | ¿Pasa al respaldo? |
| --- | --- | --- |
| Estado 429 o límite de uso informado en el cuerpo | `CuotaAgotada` | Sí |
| Tiempo agotado, falla de conexión, 408 o 5xx | `ErrorTemporal` | Sí |
| Respuesta de éxito sin texto o con estructura inesperada | `ErrorTemporal` | Sí |
| Rechazo de los filtros de seguridad del proveedor | `ContenidoBloqueado` | No |
| Otros 4xx: solicitud inválida, clave incorrecta, modelo inexistente | `ErrorNoRecuperable` | No |

Casos particulares:

- **Gemini, bloqueo:** se detecta por `promptFeedback.blockReason` (filtro sobre el prompt) o por un `finishReason` del candidato igual a `SAFETY`, `BLOCKLIST`, `PROHIBITED_CONTENT` o `SPII`.
- **Gemini, `RECITATION`, `LANGUAGE`, `OTHER` y `MAX_TOKENS` sin texto:** no son filtros de seguridad; se tratan como `ErrorTemporal` para que responda el respaldo.
- **Gemini, 400 `FAILED_PRECONDITION`** (nivel gratuito no disponible en la región o facturación sin habilitar) **y 403:** son problemas de la cuenta, no de cuota. Quedan como `ErrorNoRecuperable`. La cuota agotada llega como 429.
- **Gemini, clave inválida:** la API responde 400, no 401. Queda como `ErrorNoRecuperable`.
- **Groq, 413 con código `rate_limit_exceeded`:** la solicitud sola supera el límite de tokens por minuto del plan. Se trata como `CuotaAgotada`. Este caso no figura en la página de errores de Groq; es comportamiento observado.
- **Groq, 498** (capacidad agotada, código propio de Groq): `ErrorTemporal`.
- **Groq, bloqueo:** `finish_reason` igual a `content_filter`.

**Privacidad.** Los mensajes de las excepciones incluyen solo el nombre del proveedor y la categoría o el código de estado. No incluyen la clave, el texto de la solicitud, el de la respuesta ni el cuerpo de error del proveedor. Las excepciones de `httpx` no viajan encadenadas.

## Comportamiento del respaldo

`GestorProveedores.generar_con_respaldo(solicitud)`:

1. Recorre los proveedores en orden.
2. Antes de cada intento calcula cuánto queda de `LLM_PRESUPUESTO_SEGUNDOS`. Si no queda nada, deja de intentar. Cada llamada se limita al tiempo restante; si se vence, cuenta como `ErrorTemporal` de ese proveedor.
3. Ante un error recuperable, lo anota y sigue con el siguiente.
4. Ante un error no recuperable (`ContenidoBloqueado`, `ErrorNoRecuperable`), lo propaga sin probar más proveedores.
5. Si ninguno responde, lanza `TodosLosProveedoresFallaron` con la lista de errores. Sin proveedores configurados lanza `SinProveedoresConfigurados`, que es una subclase con el mensaje «No hay proveedores de modelo de lenguaje configurados.».

Otros detalles:

- No guarda estado entre llamadas: no hay circuit breaker ni caché de respuestas.
- Una excepción fuera del contrato lanzada por un proveedor se trata como `ErrorTemporal`.
- Registra con `logging` (logger `app.providers.gestor`) el proveedor, la categoría del error y la duración. Nunca el contenido del prompt ni de la respuesta.
- Una clave inválida en el proveedor principal es un `ErrorNoRecuperable`: no se usa el respaldo. Es una decisión del contrato: un error de configuración debe verse, no quedar tapado.

## Script de verificación manual

`backend/scripts/probar_llm.py` hace una generación real con la configuración de `.env`. Consume cuota. Se ejecuta desde `backend/`, porque el `.env` se busca en el directorio actual.

```bash
python scripts/probar_llm.py --consulta "¿Qué es un IFTS?"
python scripts/probar_llm.py --consulta "¿Cuándo empiezan?" --contexto "Empiezan el 16 de marzo." --instrucciones "Respondé solo con el contexto."
python scripts/probar_llm.py --consulta "Hola" --simular-fallo gemini:cuota
python scripts/probar_llm.py --consulta "Hola" --simular-fallo gemini:temporal --simular-fallo groq:cuota
```

`--simular-fallo NOMBRE:TIPO` reemplaza ese proveedor por un `ProveedorFalso` que siempre falla. `TIPO` es `cuota`, `temporal`, `bloqueado` o `invalido`. Se puede repetir.

Imprime el proveedor que respondió, el modelo, el tiempo total y el texto. Si falla, imprime el tipo de error y los proveedores intentados. Nunca imprime claves. Códigos de salida: `0` hubo respuesta, `1` la generación falló, `2` no se pudo intentar (sin claves, configuración inválida o argumentos incorrectos).

### Error «no se pudo verificar el certificado TLS»

Aparece cuando un antivirus o un proxy inspecciona el tráfico HTTPS con un certificado propio (por ejemplo, el «análisis HTTPS» de Avast). `httpx` valida contra los certificados de `certifi` y no contra el almacén de Windows, así que no reconoce ese certificado. Opciones, solo para desarrollo local:

- desactivar el análisis HTTPS del antivirus, o
- apuntar la variable de entorno `SSL_CERT_FILE` a un archivo PEM que incluya el certificado raíz del antivirus además de los de `certifi`.

No desactivar la verificación de certificados.

## Pruebas

```bash
cd backend
pytest                    # unitarias; no usan red ni claves
pytest -m integracion     # llamadas reales; necesitan claves y consumen cuota
```

- Las unitarias están en `tests/unit/providers/`. Los proveedores se prueban con `httpx.MockTransport` y el gestor con `ProveedorFalso`.
- Las de integración están en `tests/integration/providers/` y llevan el marcador `integracion`. Quedan excluidas por defecto con `addopts = "-m 'not integracion'"` en `pyproject.toml`. Leen las claves del entorno o de `backend/.env`, y cada una se omite si falta la clave que necesita. Son tres llamadas en total.

## Cómo agregar un tercer proveedor

1. **Implementar la interfaz.** Crear `app/providers/<nombre>.py`. Si la API es HTTP con JSON, heredar de `ProveedorHTTP` y definir `nombre`, `_url`, `_encabezados`, `_cuerpo` e `_interpretar`. Si no, implementar `ProveedorLLM` directamente, con un constructor que acepte `api_key`, `modelo` y `timeout_segundos`.
2. **Traducir los errores.** Revisar en la documentación del proveedor qué estados indican cuota, bloqueo y errores permanentes, y especializar `_error_de_estado` para los que se aparten del mapeo general. Los mensajes no deben incluir contenido ni claves.
3. **Registrarlo.** Sumar la clase a `REGISTRO_PROVEEDORES` en `dependencias.py`.
4. **Sumar el nombre y sus variables en `app/config.py`.** Agregar el nombre a `PROVEEDORES_LLM_VALIDOS`; crear los campos `<nombre>_api_key`, `<nombre>_modelo` y `<nombre>_timeout_segundos` (el registro los busca con esos nombres); sumar la clave al diccionario de `proveedores_disponibles` y el tiempo máximo a `_validar_presupuesto`. Documentar las variables en `.env.example`.
5. **Escribir las pruebas.** Unitarias con `httpx.MockTransport` siguiendo `test_gemini.py`, y un caso en las de integración. Hay una prueba que comprueba que el registro y `PROVEEDORES_LLM_VALIDOS` coinciden.

El proveedor debe ser gratuito y no pedir tarjeta de crédito (RNF10).
