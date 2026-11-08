export type Modo = 'flotante' | 'incrustado';

/** `?modo=incrustado` muestra solo el panel; cualquier otro valor (o ninguno) es `flotante`. */
export function leerModo(): Modo {
  return new URLSearchParams(window.location.search).get('modo') === 'incrustado'
    ? 'incrustado'
    : 'flotante';
}
