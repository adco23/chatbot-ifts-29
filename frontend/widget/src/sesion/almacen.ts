/**
 * sessionStorage con respaldo en memoria. Dentro de un iframe cross-origin el navegador puede
 * bloquear el almacenamiento (cookies de terceros, modo privado), y en ese caso acceder lanza
 * una excepción. Nunca se guardan datos personales: solo el id aleatorio y preferencias de UI.
 */
const memoria = new Map<string, string>();

export function leer(clave: string): string | null {
  try {
    const valor = window.sessionStorage.getItem(clave);
    if (valor !== null) return valor;
  } catch {
    // Almacenamiento no disponible: se usa memoria.
  }
  return memoria.get(clave) ?? null;
}

export function guardar(clave: string, valor: string): void {
  memoria.set(clave, valor);
  try {
    window.sessionStorage.setItem(clave, valor);
  } catch {
    // Queda solo en memoria hasta que se recargue la página.
  }
}
