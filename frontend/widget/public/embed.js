/*
 * Loader del Asistente Virtual AppMinds para Moodle (modo flotante).
 *
 * Uso (bloque HTML de Moodle; ver docs/embed.md):
 *   <script src="https://<widget>/embed.js" data-offset="80" defer></script>
 *
 * Atributos opcionales:
 *   data-offset  distancia en px desde el borde inferior (por defecto 80, para no tapar el
 *                botón "?" de ayuda de Moodle).
 *   data-zindex  z-index del iframe (por defecto 2147483000).
 *
 * Crea un iframe fijo abajo a la derecha, del tamaño justo del lanzador mientras el chat está
 * cerrado (para no bloquear clics en la página) y lo agranda cuando se abre. El widget avisa
 * los cambios con postMessage: solo estado y medidas, nunca contenido de la conversación.
 * No usa cookies, no guarda nada y no lee la página de Moodle.
 */
(function () {
  'use strict';

  if (window.__asistenteAppMinds) return;
  window.__asistenteAppMinds = true;

  var script = document.currentScript;
  if (!script || !script.src) return;

  var origenWidget = new URL(script.src).origin;
  var offset = numero(script.getAttribute('data-offset'), 80);
  var zIndex = numero(script.getAttribute('data-zindex'), 2147483000);
  var ANCHO_CELULAR = 480;
  var CERRADO_INICIAL = 96;

  var estado = { abierto: false, ancho: CERRADO_INICIAL, alto: CERRADO_INICIAL };

  var url = new URL('/', origenWidget);
  url.searchParams.set('modo', 'flotante');
  url.searchParams.set('padre', window.location.origin);

  var iframe = document.createElement('iframe');
  iframe.src = url.toString();
  iframe.title = 'Asistente virtual';
  iframe.setAttribute('allowtransparency', 'true');
  iframe.referrerPolicy = 'strict-origin';
  var estilo = iframe.style;
  estilo.position = 'fixed';
  estilo.border = '0';
  estilo.margin = '0';
  estilo.padding = '0';
  estilo.background = 'transparent';
  // Igual al del widget: si difieren, el navegador pinta un fondo opaco detrás del iframe.
  estilo.colorScheme = 'light';
  estilo.zIndex = String(zIndex);
  estilo.maxWidth = '100vw';
  estilo.maxHeight = '100vh';

  window.addEventListener('message', function (evento) {
    if (evento.origin !== origenWidget || evento.source !== iframe.contentWindow) return;
    var datos = evento.data;
    if (
      !datos ||
      datos.fuente !== 'asistente-appminds' ||
      datos.tipo !== 'tamano' ||
      (datos.estado !== 'abierto' && datos.estado !== 'cerrado') ||
      !esMedida(datos.ancho) ||
      !esMedida(datos.alto)
    ) {
      return;
    }
    estado = { abierto: datos.estado === 'abierto', ancho: datos.ancho, alto: datos.alto };
    aplicar();
  });

  window.addEventListener('resize', aplicar);
  aplicar();

  if (document.body) {
    document.body.appendChild(iframe);
  } else {
    document.addEventListener('DOMContentLoaded', function () {
      document.body.appendChild(iframe);
    });
  }

  function aplicar() {
    var anchoVentana = document.documentElement.clientWidth || window.innerWidth;
    var altoVentana = window.innerHeight;

    if (estado.abierto && anchoVentana <= ANCHO_CELULAR) {
      // Celular con el chat abierto: casi pantalla completa.
      fijar({ top: '0', right: '0', bottom: '0', left: '0', width: '100%', height: '100%' });
      return;
    }
    var bottom = Math.min(offset, Math.max(altoVentana - estado.alto, 0));
    fijar({
      top: 'auto',
      right: '0',
      bottom: bottom + 'px',
      left: 'auto',
      width: Math.min(estado.ancho, anchoVentana) + 'px',
      height: Math.min(estado.alto, altoVentana - bottom) + 'px',
    });
  }

  function fijar(valores) {
    for (var clave in valores) estilo[clave] = valores[clave];
  }

  function numero(valor, porDefecto) {
    var n = parseInt(valor, 10);
    return isFinite(n) && n >= 0 ? n : porDefecto;
  }

  function esMedida(valor) {
    return typeof valor === 'number' && isFinite(valor) && valor > 0 && valor < 5000;
  }
})();
