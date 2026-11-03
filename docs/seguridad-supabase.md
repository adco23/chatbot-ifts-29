# Cambio manual de credencial y permisos: 0008

La migración funcional **0007** permite probar Bloque 6 antes del cambio global de
seguridad. **0008 no debe aplicarse hasta que FastAPI y las CLI tengan una
credencial privada configurada.** Ningún script aplica estas migraciones.

El backend anterior usa `SUPABASE_KEY`, actualmente publishable/anon. El cliente
ahora prefiere `SUPABASE_BACKEND_KEY`; admite una clave `sb_secret_...` o, como
compatibilidad, una clave JWT `service_role`. La clasificación es local: no valida
la firma ni comprueba la clave contra Supabase. Una clave inválida en el campo
privado no provoca una degradación silenciosa al rol público.

## Orden de aplicación

1. Revisar y aplicar manualmente 0007; probar FAQ con el acceso actual.
2. Crear/copiar una credencial privada para el backend en Supabase y configurarla
   manualmente como `SUPABASE_BACKEND_KEY` en el entorno de FastAPI y las CLI.
   No usar `VITE_`, React, un repositorio versionado ni logs para esta credencial.
3. Reiniciar el backend: la configuración y el cliente se almacenan por proceso.
4. Comprobar el acceso del backend con la credencial privada antes de aplicar 0008.
5. Revisar y aplicar manualmente `backend/migrations/0008_seguridad_supabase.sql`.
6. Verificar `/health`, consultas generales/académicas y FAQ; ejecutar únicamente
   cargas o sincronizaciones expresamente autorizadas.

0008 activa RLS en documento, fragmento, pregunta_frecuente y pregunta_fuente;
retira ACL de tabla, columna y secuencias a PUBLIC/anon/authenticated; restringe
RPC documentales, FAQ y lock al backend. Verifica privilegios efectivos y aborta
si una herencia de roles deja acceso público residual. No toca RPC ajenas al
conjunto documental/FAQ.

El rol `service_role` conserva sólo las ACL necesarias en estas tablas: no recibe
DELETE sobre documento o pregunta_frecuente. El lock sigue accesible mediante sus
funciones existentes, sin permisos directos sobre su tabla. La credencial privada
usa un rol con BYPASSRLS: mantenerla sólo en el servidor es indispensable.

0008 no cambia respuestas, embeddings ni datos documentales. No es posible
comprobar ACL reales o el funcionamiento de RLS mediante mocks Python; los tests
incluidos sólo verifican el contrato estático y la selección segura de credencial.

El transporte PostgREST usa `SUPABASE_TIMEOUT_SEGUNDOS` (default 5 segundos), en
lugar del default de 120 segundos de la biblioteca instalada. Un timeout de
`asyncio.to_thread` no interrumpe una petición HTTP que ya esté ejecutándose:
el presupuesto asíncrono y el timeout de transporte son límites distintos. La
medición del límite total serverless sigue pendiente en T29. La regeneración
además pasa una fecha límite al upsert para no publicar una FAQ fuera de su
presupuesto aunque el hilo de transporte termine posteriormente.
