import { URL_API } from '../config/entorno';
import { obtenerIdSesion } from '../sesion/idSesion';
import type { ClienteChat } from './cliente';
import { ClienteMock } from './clienteMock';
import { ClienteReal } from './clienteReal';

export type { ClienteChat, OpcionesEnvio } from './cliente';
export * from './tipos';

/** Sin `VITE_API_URL` se usa el mock: permite trabajar el front sin depender del backend. */
export const usaMock = URL_API === '';

export const clienteChat: ClienteChat = usaMock
  ? new ClienteMock()
  : new ClienteReal(URL_API, obtenerIdSesion);
