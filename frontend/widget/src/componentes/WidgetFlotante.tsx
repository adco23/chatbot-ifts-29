import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';
import { clienteChat } from '../api';
import { TEMAS } from '../config/temas';
import { avisarTamano, enIframe } from '../embebido/mensajeria';
import { useConversacion } from '../hooks/useConversacion';
import { guardar, leer } from '../sesion/almacen';
import { GloboSaludo } from './GloboSaludo';
import { Lanzador } from './Lanzador';
import { PanelChat } from './PanelChat';
import estilos from './WidgetFlotante.module.css';

const ID_PANEL = 'asistente-panel';
const CLAVE_GLOBO = 'asistente.globoDescartado';
const RETRASO_GLOBO_MS = 1500;
/** Lugar para el botón × del globo, que sobresale arriba a la izquierda. */
const HOLGURA_PX = 12;
/** Lugar para la sombra del panel abierto, para que el borde del iframe no la corte. */
const HOLGURA_SOMBRA_PX = 32;

/** Modo `flotante`: lanzador abajo a la derecha, globo de saludo y panel de chat. */
export function WidgetFlotante() {
  const conversacion = useConversacion(clienteChat);
  const [abierto, setAbierto] = useState(false);
  const [globoVisible, setGloboVisible] = useState(false);
  const [conInsignia, setConInsignia] = useState(true);
  const lanzador = useRef<HTMLButtonElement>(null);
  const contenedor = useRef<HTMLDivElement>(null);

  // Dentro del iframe de embed.js: avisar al padre cuánto lugar hace falta, para que el
  // iframe ocupe solo eso y no bloquee clics en la página cuando el chat está cerrado.
  useLayoutEffect(() => {
    const elemento = contenedor.current;
    if (!enIframe || !elemento) return;
    const informar = () => {
      const estilo = getComputedStyle(elemento);
      const px = (variable: string) => parseFloat(estilo.getPropertyValue(variable)) || 0;
      const margen = px('--margen');
      const margenesHorizontales = margen + px('--lanzador-derecha');
      if (abierto) {
        // Tamaño de diseño, no el actual: el iframe todavía puede tener el tamaño de cerrado.
        avisarTamano(
          'abierto',
          px('--panel-ancho') + margenesHorizontales + HOLGURA_SOMBRA_PX,
          px('--panel-alto') +
            px('--espacio-3') +
            px('--lanzador-tamano') +
            2 * margen +
            HOLGURA_SOMBRA_PX,
        );
      } else {
        const caja = elemento.getBoundingClientRect();
        avisarTamano(
          'cerrado',
          caja.width + margenesHorizontales + HOLGURA_PX,
          caja.height + 2 * margen + HOLGURA_PX,
        );
      }
    };
    informar();
    const observador = new ResizeObserver(informar);
    observador.observe(elemento);
    return () => observador.disconnect();
  }, [abierto, globoVisible]);

  useEffect(() => {
    if (leer(CLAVE_GLOBO) === 'si') return;
    const temporizador = setTimeout(() => setGloboVisible(true), RETRASO_GLOBO_MS);
    return () => clearTimeout(temporizador);
  }, []);

  const abrir = useCallback(() => {
    setAbierto(true);
    setConInsignia(false);
  }, []);

  const cerrar = useCallback(() => {
    setAbierto(false);
    lanzador.current?.focus();
  }, []);

  useEffect(() => {
    if (!abierto) return;
    const alTeclear = (evento: KeyboardEvent) => {
      if (evento.key === 'Escape') cerrar();
    };
    document.addEventListener('keydown', alTeclear);
    return () => document.removeEventListener('keydown', alTeclear);
  }, [abierto, cerrar]);

  const descartarGlobo = () => {
    setGloboVisible(false);
    guardar(CLAVE_GLOBO, 'si');
    lanzador.current?.focus();
  };

  return (
    <div ref={contenedor} className={estilos.contenedor}>
      <PanelChat
        id={ID_PANEL}
        abierto={abierto}
        alCerrar={cerrar}
        conversacion={conversacion}
        temas={TEMAS}
        className={estilos.panel}
      />
      <div className={estilos.base}>
        {/* En las capturas el globo sigue visible con el chat abierto: se toma como artefacto. */}
        {globoVisible && !abierto && <GloboSaludo alAbrir={abrir} alDescartar={descartarGlobo} />}
        <Lanzador
          ref={lanzador}
          abierto={abierto}
          conInsignia={conInsignia}
          idPanel={ID_PANEL}
          alPulsar={abierto ? cerrar : abrir}
        />
      </div>
    </div>
  );
}
