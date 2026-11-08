import { guardar, leer } from './almacen';

const CLAVE = 'asistente.idSesion';
const FORMATO_UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;

/**
 * Identificador aleatorio de la sesión (UUID v4, minúsculas), que el backend espera en
 * `X-Sesion-Id`. No se deriva de ningún dato de la persona (RNF01).
 */
export function obtenerIdSesion(): string {
  const existente = leer(CLAVE);
  if (existente && FORMATO_UUID.test(existente)) return existente;
  const nuevo = generarUuid();
  guardar(CLAVE, nuevo);
  return nuevo;
}

function generarUuid(): string {
  if (typeof crypto.randomUUID === 'function') return crypto.randomUUID();
  // randomUUID solo existe en contextos seguros; getRandomValues está siempre.
  const bytes = crypto.getRandomValues(new Uint8Array(16));
  bytes[6] = ((bytes[6] ?? 0) & 0x0f) | 0x40;
  bytes[8] = ((bytes[8] ?? 0) & 0x3f) | 0x80;
  const hex = Array.from(bytes, (b) => b.toString(16).padStart(2, '0')).join('');
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}
