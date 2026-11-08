"""Pruebas del endpoint de salud."""


def test_health_responde_ok(cliente):
    respuesta = cliente.get("/health")

    assert respuesta.status_code == 200
    assert respuesta.json() == {
        "estado": "ok",
        "servicio": "asistente-ifts29-api",
        "version": "0.1.0",
        "entorno": "local",
    }


def test_health_informa_entorno_y_version_configurados(cliente, monkeypatch):
    monkeypatch.setenv("ENTORNO", "produccion")
    monkeypatch.setenv("VERSION_APP", "1.2.3")

    cuerpo = cliente.get("/health").json()

    assert cuerpo["entorno"] == "produccion"
    assert cuerpo["version"] == "1.2.3"
