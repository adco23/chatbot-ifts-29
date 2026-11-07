"""Selección sobre resultados con score real; nunca puntúa lecturas de texto."""

from app.models.rag import ResultadoRecuperacion
from app.services.objetivo_consulta import contenido_pertinente


def seleccionar(resultado, *, documentos, objetivo, limite, catalogo, periodo):
    por_id = {d.id: d for d in documentos}
    candidatos = []
    for fragmento in resultado.fragmentos:
        documento = por_id.get(fragmento.documento_id)
        if documento is None:
            continue
        if fragmento.vigente is not True or not catalogo.documento_habilitado(fragmento, periodo):
            continue
        if contenido_pertinente(fragmento.contenido, objetivo, documento.titulo):
            candidatos.append(fragmento)
    candidatos.sort(key=lambda f: (-f.similitud, f.fragmento_id))
    originales = resultado.fragmento_ids_primarios
    primarios = [f for f in candidatos if originales is None or f.fragmento_id in originales]
    primarios = primarios[:limite]
    ids = {f.fragmento_id for f in primarios}
    complementarios = [f for f in candidatos if f.fragmento_id not in ids
                      and originales is not None and f.fragmento_id not in originales]
    mejor = max((f.similitud for f in primarios), default=None)
    confianza = max(0.0, min(1.0, mejor)) if mejor is not None else 0.0
    return ResultadoRecuperacion(
        fragmentos=[*primarios, *complementarios[:max(0, limite-len(primarios))]],
        mejor_similitud=mejor, confianza=confianza, umbral=resultado.umbral,
        evidencia_suficiente=bool(primarios) and confianza >= resultado.umbral,
        fragmento_ids_primarios=([f.fragmento_id for f in primarios]
                                if originales is not None else None),
    )
