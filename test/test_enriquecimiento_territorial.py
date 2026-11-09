import csv
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from captador_bbdd.enriquecimiento_territorial import (
    EnriquecimientoConfigurationError,
    enriquecer_concesiones,
    normalizar_cif,
    validar_cif,
)


def _respuesta(status_code, payload, headers=None):
    respuesta = Mock()
    respuesta.status_code = status_code
    respuesta.headers = headers or {}
    respuesta.raise_for_status.return_value = None
    respuesta.json.return_value = payload
    return respuesta


class TestEnriquecimientoTerritorial(unittest.TestCase):

    def test_normaliza_y_valida_cif_con_digito_de_control(self):
        self.assertEqual(normalizar_cif(" b188 66749 "), "B18866749")
        self.assertTrue(validar_cif("B18866749"))
        self.assertFalse(validar_cif("B18866740"))
        self.assertFalse(validar_cif("***7436**"))

    def test_consulta_una_vez_por_cif_y_deja_el_exceso_pendiente(self):
        session = Mock()
        session.get.side_effect = [
            _respuesta(200, {
                "success": True,
                "data": {"stats": {"remaining_calls": 1}},
            }),
            _respuesta(200, {
                "success": True,
                "data": {
                    "cif": "B18866749",
                    "name": "Empresa de prueba SL",
                    "province": "Málaga",
                    "municipality": "Málaga",
                    "updated_at": "2026-11-01 10:00:00",
                    "sources": {
                        "registry": {
                            "source": "borme",
                            "as_of": "2026-10-25",
                            "url_pdf": "https://www.boe.es/borme/documento.pdf",
                        }
                    },
                },
            }),
        ]
        concesiones = [
            {"cif": "B18866749", "codigo_bdns": "834244", "id_concesion": "SB-1"},
            {"cif": "B18866749", "codigo_bdns": "848058", "id_concesion": "SB-2"},
            {"cif": "A15075062", "codigo_bdns": "848058", "id_concesion": "SB-3"},
        ]

        with tempfile.TemporaryDirectory() as directorio:
            ruta_cache = Path(directorio) / "empresas.csv"
            resultados = enriquecer_concesiones(
                concesiones,
                cache_path=ruta_cache,
                api_key="test-key",
                max_consultas=100,
                session=session,
                fecha_verificacion="2026-11-03",
                output_path=Path(directorio) / "concesiones_enriquecidas.csv",
            )

            self.assertEqual(len(resultados), 3)
            self.assertEqual(resultados[0]["estado_verificacion"], "VERIFICADO")
            self.assertEqual(resultados[0]["provincia"], "Málaga")
            self.assertEqual(resultados[0]["comunidad_autonoma"], "Andalucía")
            self.assertEqual(resultados[0]["presencia_andalucia"], "Si")
            self.assertEqual(resultados[0]["fecha_verificacion"], "2026-11-03")
            self.assertEqual(resultados[0]["fecha_dato_fuente"], "2026-10-25")
            self.assertEqual(resultados[1]["estado_verificacion"], "VERIFICADO")
            self.assertEqual(resultados[2]["estado_verificacion"], "PENDIENTE")
            self.assertEqual(session.get.call_count, 2)

            with ruta_cache.open(encoding="utf-8-sig", newline="") as archivo:
                perfiles = list(csv.DictReader(archivo))
            self.assertEqual(len(perfiles), 1)
            self.assertEqual(perfiles[0]["cif"], "B18866749")

    def test_cache_evitas_consultar_de_nuevo_el_mismo_cif(self):
        session = Mock()
        concesion = {"cif": "B18866749", "codigo_bdns": "834244"}

        with tempfile.TemporaryDirectory() as directorio:
            ruta_cache = Path(directorio) / "empresas.csv"
            primero = Mock()
            primero.get.side_effect = [
                _respuesta(200, {
                    "success": True,
                    "data": {"stats": {"remaining_calls": 1}},
                }),
                _respuesta(200, {
                    "success": True,
                    "data": {
                        "cif": "B18866749",
                        "name": "Empresa de prueba SL",
                        "province": "Sevilla",
                        "sources": {"registry": {"source": "borme"}},
                    },
                }),
            ]
            enriquecer_concesiones(
                [concesion],
                cache_path=ruta_cache,
                api_key="test-key",
                session=primero,
                fecha_verificacion="2026-11-03",
                output_path=None,
            )

            session.get.assert_not_called()
            resultado = enriquecer_concesiones(
                [concesion],
                cache_path=ruta_cache,
                session=session,
                output_path=None,
            )

        self.assertEqual(resultado[0]["provincia"], "Sevilla")
        self.assertEqual(resultado[0]["estado_verificacion"], "VERIFICADO")
        session.get.assert_not_called()

    def test_no_consulta_sin_api_key(self):
        session = Mock()
        with self.assertRaises(EnriquecimientoConfigurationError):
            enriquecer_concesiones(
                [{"cif": "B18866749", "codigo_bdns": "834244"}],
                cache_path=Path("cache-no-creado.csv"),
                api_key="",
                session=session,
            )
        session.get.assert_not_called()

    def test_no_identifica_si_la_api_devuelve_otro_cif(self):
        session = Mock()
        session.get.side_effect = [
            _respuesta(200, {
                "success": True,
                "data": {"stats": {"remaining_calls": 1}},
            }),
            _respuesta(200, {
                "success": True,
                "data": {
                    "cif": "A15075062",
                    "name": "Otra empresa",
                    "province": "Madrid",
                },
            }),
        ]
        with tempfile.TemporaryDirectory() as directorio:
            resultado = enriquecer_concesiones(
                [{"cif": "B18866749", "codigo_bdns": "834244"}],
                cache_path=Path(directorio) / "empresas.csv",
                api_key="test-key",
                session=session,
                fecha_verificacion="2026-11-03",
                output_path=None,
            )
        self.assertEqual(resultado[0]["estado_verificacion"], "NO_IDENTIFICADO")
        self.assertEqual(resultado[0]["provincia"], "")

    def test_limite_temporal_deja_el_cif_pendiente_y_registra_retry_after(self):
        session = Mock()
        session.get.side_effect = [
            _respuesta(200, {
                "success": True,
                "data": {"stats": {"remaining_calls": 1}},
            }),
            _respuesta(429, {}, headers={"Retry-After": "1"}),
        ]
        with tempfile.TemporaryDirectory() as directorio:
            ruta_cache = Path(directorio) / "empresas.csv"
            with self.assertLogs(
                "captador_bbdd.enriquecimiento_territorial", level="WARNING"
            ) as mensajes:
                resultado = enriquecer_concesiones(
                    [{"cif": "B18866749", "codigo_bdns": "834244"}],
                    cache_path=ruta_cache,
                    api_key="test-key",
                    session=session,
                    fecha_verificacion="2026-11-03",
                    output_path=None,
                )

        self.assertEqual(resultado[0]["estado_verificacion"], "PENDIENTE")
        self.assertIn("límite temporal", resultado[0]["motivo_enriquecimiento"])
        self.assertIn("Retry-After: 1", mensajes.output[0])
        self.assertFalse(ruta_cache.exists())
        self.assertEqual(session.get.call_count, 2)


if __name__ == "__main__":
    unittest.main()
