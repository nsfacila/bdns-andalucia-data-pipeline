"""
Pruebas unitarias para el módulo captador_bbdd/filtros.py
"""

import unittest
from captador_bbdd.filtros import evaluar_subvencion, es_ubicacion_andalucia


class TestFiltrosSubvenciones(unittest.TestCase):

    def test_caso_valido_andalucia(self):
        concesion = {
            "empresa": "Empresa Ejemplo S.L.",
            "organismo": "CDTI",
            "convocatoria": "Programa Misiones de Ciencia e Innovación",
            "comunidad_autonoma": "Andalucía",
            "provincia": "Sevilla",
            "estado_verificacion": "VERIFICADO",
            "fecha_concesion": "2024-05-10"
        }
        res = evaluar_subvencion(concesion)
        self.assertEqual(res["estado"], "aceptada")

    def test_palabras_clave_y_ubicacion_con_tildes(self):
        concesion = {
            "organismo": "Red.es",
            "convocatoria": "Tecnologías Duales",
            "provincia": "Málaga",
            "estado_verificacion": "VERIFICADO",
            "fecha_concesion": "2024-02-01"
        }
        self.assertEqual(evaluar_subvencion(concesion)["estado"], "aceptada")

    def test_delegacion_andalucia_sede_madrid(self):
        concesion = {
            "empresa": "Empresa Madrid S.A.",
            "organismo": "Red.es",
            "convocatoria": "Ayudas a proyectos de IA y Tecnologías Duales 2025",
            "comunidad_autonoma": "Comunidad de Madrid",
            "provincia": "Madrid",
            "delegaciones": "Cádiz",
            "presencia_andalucia": "Si",
            "estado_verificacion": "VERIFICADO",
            "fecha_concesion": "2025-01-15"
        }
        res = evaluar_subvencion(concesion)
        self.assertEqual(res["estado"], "aceptada")

    def test_descarte_anio_anterior(self):
        concesion = {
            "empresa": "Empresa Antigua S.L.",
            "organismo": "CDTI",
            "convocatoria": "Programa Misiones",
            "comunidad_autonoma": "Andalucía",
            "fecha_concesion": "2023-11-20"
        }
        res = evaluar_subvencion(concesion)
        self.assertEqual(res["estado"], "descartada")
        self.assertIn("Año fuera de rango", res["motivo"])

    def test_descarte_fecha_ausente(self):
        concesion = {
            "organismo": "CDTI",
            "convocatoria": "Programa Misiones",
            "comunidad_autonoma": "Andalucía"
        }
        res = evaluar_subvencion(concesion)
        self.assertEqual(res["estado"], "descartada")
        self.assertIn("Año fuera de rango", res["motivo"])

    def test_descarte_organismo_no_valido(self):
        concesion = {
            "empresa": "Empresa Local S.L.",
            "organismo": "Junta de Andalucía",
            "convocatoria": "Incentivos Locales",
            "comunidad_autonoma": "Andalucía",
            "fecha_concesion": "2024-06-01"
        }
        res = evaluar_subvencion(concesion)
        self.assertEqual(res["estado"], "descartada")
        self.assertIn("Organo no válido", res["motivo"])

    def test_sin_ubicacion_queda_pendiente_de_enriquecimiento(self):
        concesion = {
            "codigo_bdns": "834244",
            "id_concesion": "SB123",
            "cif": "B29012345",
            "empresa": "Empresa sin ubicación",
            "organismo": "CDTI",
            "convocatoria": "Misiones Ciencia e Innovación",
            "fecha_concesion": "2025-12-29",
        }

        resultado = evaluar_subvencion(concesion)

        self.assertEqual(resultado["estado"], "pendiente_enriquecimiento")
        self.assertIn("No se clasifica por NUTS ni por CIF", resultado["motivo"])

    def test_region_nuts_no_se_considera_domicilio_del_beneficiario(self):
        concesion = {
            "organismo": "Red.es",
            "convocatoria": "Ayudas de inteligencia artificial",
            "comunidad_autonoma": "ES - España",
            "fecha_concesion": "2026-09-16",
        }

        self.assertIsNone(es_ubicacion_andalucia(concesion))
        self.assertEqual(
            evaluar_subvencion(concesion)["estado"],
            "pendiente_enriquecimiento",
        )

    def test_no_confunde_una_ayuda_de_proyecto_andaluz_con_domicilio(self):
        concesion = {
            "organismo": "CDTI",
            "convocatoria": "Misiones Ciencia e Innovación",
            "region_nuts": "ES61 Andalucía",
            "fecha_concesion": "2025-12-29",
        }

        self.assertIsNone(es_ubicacion_andalucia(concesion))
        self.assertEqual(
            evaluar_subvencion(concesion)["estado"],
            "pendiente_enriquecimiento",
        )

    def test_ubicacion_mercantil_explicita_se_evalua_sin_inferencia_nuts(self):
        concesion = {
            "codigo_bdns": "848058",
            "organismo": "Red.es",
            "convocatoria": "Ayudas de inteligencia artificial",
            "provincia": "Málaga",
            "estado_verificacion": "VERIFICADO",
            "fecha_concesion": "2026-09-16",
        }

        self.assertTrue(es_ubicacion_andalucia(concesion))
        self.assertEqual(evaluar_subvencion(concesion)["estado"], "aceptada")

    def test_no_clasifica_ubicacion_sin_estado_verificado(self):
        concesion = {
            "organismo": "CDTI",
            "convocatoria": "Misiones Ciencia e Innovación",
            "provincia": "Sevilla",
            "comunidad_autonoma": "Andalucía",
            "presencia_andalucia": "Si",
            "fecha_concesion": "2025-12-29",
        }

        self.assertIsNone(es_ubicacion_andalucia(concesion))
        self.assertEqual(
            evaluar_subvencion(concesion)["estado"],
            "pendiente_enriquecimiento",
        )

    def test_empresa_verificada_fuera_de_andalucia_se_conserva_como_no_andaluza(self):
        concesion = {
            "codigo_bdns": "834244",
            "organismo": "CDTI",
            "convocatoria": "Misiones Ciencia e Innovación",
            "provincia": "Madrid",
            "comunidad_autonoma": "Comunidad de Madrid",
            "presencia_andalucia": "No",
            "estado_verificacion": "VERIFICADO",
            "fecha_concesion": "2025-12-29",
        }

        self.assertFalse(es_ubicacion_andalucia(concesion))
        self.assertEqual(
            evaluar_subvencion(concesion)["estado"],
            "descartada",
        )

    def test_codigo_bdns_limita_el_organismo_al_alcance_de_fase_1(self):
        concesion = {
            "codigo_bdns": "999999",
            "organismo": "CDTI",
            "convocatoria": "Misiones Ciencia e Innovación",
            "comunidad_autonoma": "Andalucía",
            "fecha_concesion": "2025-12-29",
        }

        resultado = evaluar_subvencion(concesion)

        self.assertEqual(resultado["estado"], "descartada")
        self.assertIn("Organo no válido", resultado["motivo"])


if __name__ == "__main__":
    unittest.main()