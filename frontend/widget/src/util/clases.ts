/** Une nombres de clase e ignora los vacíos (los CSS modules pueden devolver undefined). */
export function clases(...nombres: (string | false | null | undefined)[]): string {
  return nombres.filter(Boolean).join(' ');
}
