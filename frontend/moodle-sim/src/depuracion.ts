import type { Configuracion } from './main';

/** Panel colapsado con el modo activo, la URL y el tamaño del iframe, y controles. */
export function iniciarDepuracion(configuracion: Configuracion): void {
  const texto = (id: string, valor: string) => {
    const elemento = document.getElementById(id);
    if (elemento) elemento.textContent = valor;
  };

  texto(
    'dep-modo',
    configuracion.widget
      ? configuracion.modo +
          (configuracion.modo === 'incrustado' ? ` (${configuracion.ubicacion})` : '')
      : 'sin VITE_WIDGET_URL: no se carga el asistente',
  );

  const formulario = document.getElementById('dep-form');
  if (formulario instanceof HTMLFormElement) {
    const campos = formulario.elements;
    const fijar = (nombre: string, valor: string) => {
      const campo = campos.namedItem(nombre);
      if (campo instanceof HTMLInputElement || campo instanceof HTMLSelectElement) {
        campo.value = valor;
      }
    };
    fijar('modo', configuracion.modo);
    fijar('ubicacion', configuracion.ubicacion);
    fijar('offset', String(configuracion.offset));
    fijar('ancho', String(configuracion.ancho));
    formulario.addEventListener('submit', (evento) => {
      evento.preventDefault();
      const datos = new FormData(formulario);
      const parametros = new URLSearchParams();
      for (const [clave, valor] of datos) parametros.set(clave, String(valor));
      window.location.search = parametros.toString();
    });
  }

  // El iframe del modo flotante lo crea embed.js más tarde: se espera a que aparezca.
  const mostrar = (iframe: HTMLIFrameElement) => {
    texto('dep-url', iframe.src);
    const medir = () => {
      const caja = iframe.getBoundingClientRect();
      texto('dep-tamano', `${Math.round(caja.width)} × ${Math.round(caja.height)} px`);
    };
    medir();
    new ResizeObserver(medir).observe(iframe);
  };
  const buscar = () => {
    const iframe = document.querySelector<HTMLIFrameElement>(
      `iframe[src^="${configuracion.widget}"]`,
    );
    if (iframe) mostrar(iframe);
    return Boolean(iframe);
  };
  if (configuracion.widget && !buscar()) {
    const observador = new MutationObserver(() => {
      if (buscar()) observador.disconnect();
    });
    observador.observe(document.body, { childList: true, subtree: true });
  }
}
