"""
Pruebas de integración para el conector de la BDNS (captador_bbdd/conectores_bdns.py)
"""

import csv
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from captador_bbdd.conectores_bdns import (
    CODIGO_BDNS_CDTI,
    CODIGO_BDNS_RED_ES,
    DIR3_CDTI,
    INFORMES_CSV_FIELDS,
    obtener_concesiones_organismo,
    obtener_concesiones_convocatoria_api,
    procesar_convocatorias_api,
    procesar_exportaciones_csv,
)


CABECERAS_CSV_BDNS = [
    "Administración",
    "Departamento",
    "Órgano",
    "Código BDNS",
    "Convocatoria",
    "Bases reguladoras",
    "Código de concesión",
    "Fecha de concesión",
    "Fecha de registro",
    "Beneficiario",
    "Importe",
    "Instrumento",
    "Ayuda equivalente",
]


def _fila_bdns(
    codigo_bdns,
    id_concesion,
    beneficiario,
    importe="1250.75",
    fecha="07/10/2026",
    organismo="Órgano concedente",
    convocatoria="Convocatoria de prueba",
):
    return [
        "ESTADO",
        "Departamento",
        organismo,
        codigo_bdns,
        convocatoria,
        "Bases",
        id_concesion,
        fecha,
        "10/10/2026",
        beneficiario,
        importe,
        "SUBVENCIÓN",
        importe,
    ]


def _escribir_csv_bdns(ruta, filas):
    with ruta.open("w", newline="", encoding="cp1252") as archivo:
        escritor = csv.writer(archivo)
        escritor.writerow(CABECERAS_CSV_BDNS)
        escritor.writerows(filas)


def _respuesta_api(content, number, total_pages, total_elements):
    respuesta = Mock()
    respuesta.raise_for_status.return_value = None
    respuesta.json.return_value = {
        "content": content,
        "number": number,
        "numberOfElements": len(content),
        "totalPages": total_pages,
        "totalElements": total_elements,
    }
    return respuesta


def _item_api(id_registro, codigo_concesion, numero_convocatoria, beneficiario):
    return {
        "id": id_registro,
        "codConcesion": codigo_concesion,
        "numeroConvocatoria": numero_convocatoria,
        "idConvocatoria": int(numero_convocatoria),
        "beneficiario": beneficiario,
        "convocatoria": "Convocatoria API de prueba",
        "fechaConcesion": "2026-10-07",
        "importe": 1250.75,
        "codigoInvente": "ORIGINAL",
        "nivel1": "ESTADO",
        "nivel2": "DEPARTAMENTO",
        "nivel3": "ORGANO",
    }


class TestConectoresBDNS(unittest.TestCase):

    @patch("captador_bbdd.conectores_bdns.requests.get")
    def test_consulta_y_normaliza_concesion_cdti(self, mock_get):
        respuesta = Mock()
        respuesta.status_code = 200
        respuesta.json.return_value = {
            "items": [
                {
                    "idConcesion": "123",
                    "beneficiarioRazonSocial": "Empresa de prueba",
                    "beneficiarioNif": "A12345678",
                    "codigoPostal": "41001"
                }
            ]
        }
        mock_get.return_value = respuesta

        concesiones = obtener_concesiones_organismo(
            DIR3_CDTI,
            "CDTI",
            fecha_desde="2024-01-01",
            fecha_hasta="2024-01-31"
        )

        self.assertIsInstance(concesiones, list)
        self.assertEqual(len(concesiones), 1)
        self.assertEqual(concesiones[0]["empresa"], "Empresa de prueba")
        self.assertEqual(concesiones[0]["organismo"], "CDTI")
        self.assertEqual(concesiones[0]["id_concesion"], "123")
        self.assertEqual(concesiones[0]["codigo_postal"], "41001")
        self.assertEqual(concesiones[0]["fecha_concesion"], "")
        self.assertEqual(mock_get.call_args.kwargs["params"]["dir3"], DIR3_CDTI)

    @patch("captador_bbdd.conectores_bdns.requests.get")
    def test_api_recupera_todas_las_paginas_y_conserva_ids_originales(self, mock_get):
        mock_get.side_effect = [
            _respuesta_api([
                _item_api(101, "SB100", CODIGO_BDNS_CDTI, "B39827654 EMPRESA SL"),
                _item_api(102, "SB101", CODIGO_BDNS_CDTI, "B39827654 EMPRESA SL"),
            ], 0, 2, 3),
            _respuesta_api([
                _item_api(103, "SB102", CODIGO_BDNS_CDTI, "***7436** BENEFICIARIO"),
            ], 1, 2, 3),
        ]

        concesiones, total_paginas, errores = obtener_concesiones_convocatoria_api(
            CODIGO_BDNS_CDTI,
            "CDTI",
            page_size=2,
        )

        self.assertEqual(total_paginas, 2)
        self.assertEqual(len(concesiones), 3)
        self.assertEqual(errores, [])
        self.assertEqual(
            [call.kwargs["params"]["page"] for call in mock_get.call_args_list],
            [0, 1],
        )
        self.assertTrue(all(
            call.kwargs["params"]["vpd"] == "GE"
            and call.kwargs["params"]["numeroConvocatoria"] == CODIGO_BDNS_CDTI
            and call.kwargs["params"]["pageSize"] == 2
            for call in mock_get.call_args_list
        ))
        self.assertEqual(concesiones[0]["id_concesion"], "SB100")
        original = json.loads(concesiones[0]["datos_originales_json"])
        self.assertEqual(original["id"], 101)
        self.assertEqual(original["codigoInvente"], "ORIGINAL")
        self.assertEqual(original["idConvocatoria"], 834244)
        self.assertEqual(original["nivel1"], "ESTADO")
        self.assertEqual(original["nivel2"], "DEPARTAMENTO")
        self.assertEqual(original["nivel3"], "ORGANO")
        self.assertEqual(concesiones[0]["fecha_concesion"], "2026-10-07")
        self.assertEqual(concesiones[0]["cif"], "B39827654")
        self.assertIsNone(concesiones[2]["cif"])

    @patch("captador_bbdd.conectores_bdns.requests.get")
    def test_procesa_ambas_convocatorias_api_y_escribe_tres_csv(self, mock_get):
        mock_get.side_effect = [
            _respuesta_api([
                _item_api(1, "SB-CDTI-1", CODIGO_BDNS_CDTI, "B39827654 EMPRESA SL"),
            ], 0, 1, 1),
            _respuesta_api([
                _item_api(2, "SB-REDES-1", CODIGO_BDNS_RED_ES, "A12345678 EMPRESA SA"),
            ], 0, 1, 1),
        ]

        with tempfile.TemporaryDirectory() as directorio:
            salida = Path(directorio)
            informe = procesar_convocatorias_api(salida, page_size=50)

            self.assertEqual(informe["total_concesiones_consolidadas"], 2)
            self.assertEqual(informe["convocatorias"][CODIGO_BDNS_CDTI]["paginas_recuperadas"], 1)
            self.assertEqual(informe["convocatorias"][CODIGO_BDNS_RED_ES]["paginas_recuperadas"], 1)
            for nombre in (
                "concesiones_cdti_834244.csv",
                "concesiones_red_es_848058.csv",
                "concesiones_consolidado.csv",
                "informe_extraccion.json",
            ):
                self.assertTrue((salida / nombre).is_file())
            with (salida / "concesiones_consolidado.csv").open(
                encoding="utf-8-sig", newline=""
            ) as archivo:
                lector = csv.DictReader(archivo)
                filas = list(lector)
                self.assertEqual(lector.fieldnames, INFORMES_CSV_FIELDS)
            self.assertEqual(len(filas), 2)
            self.assertTrue(all(not fila["provincia"] and not fila["comunidad_autonoma"] for fila in filas))
            self.assertNotIn("codigo_bdns_original", filas[0])
            self.assertNotIn("id_concesion_original", filas[0])
            originales = json.loads(filas[0]["datos_originales_json"])
            self.assertIn("codConcesion", originales)
            self.assertIn("numeroConvocatoria", originales)

    def test_procesa_deduplica_y_consolida_las_dos_exportaciones(self):
        with tempfile.TemporaryDirectory() as directorio:
            raiz = Path(directorio)
            csv_cdti = raiz / "cdti.csv"
            csv_redes = raiz / "redes.csv"
            salida = raiz / "salida"
            _escribir_csv_bdns(csv_cdti, [
                _fila_bdns(
                    CODIGO_BDNS_CDTI,
                    "SB100",
                    "B39827654 EMPRESA PRUEBA SL",
                    organismo="CENTRO PARA EL DESARROLLO TECNOLÓGICO",
                ),
                _fila_bdns(
                    CODIGO_BDNS_CDTI,
                    "SB100",
                    "B39827654 EMPRESA PRUEBA SL",
                    organismo="CENTRO PARA EL DESARROLLO TECNOLÓGICO",
                ),
                _fila_bdns(
                    CODIGO_BDNS_CDTI,
                    "SB101",
                    "B39827654 EMPRESA PRUEBA SL",
                    organismo="CENTRO PARA EL DESARROLLO TECNOLÓGICO",
                ),
                _fila_bdns("999999", "SB999", "B12345678 AJENA SL"),
            ])
            _escribir_csv_bdns(csv_redes, [
                _fila_bdns(
                    CODIGO_BDNS_RED_ES,
                    "SB200",
                    "A12345678 REDES PRUEBA SA",
                    organismo="ENTIDAD PÚBLICA EMPRESARIAL RED.ES",
                ),
                _fila_bdns(
                    CODIGO_BDNS_RED_ES,
                    "SB201",
                    "***7436** MORALES BURRUEZO JOSE IGNACIO - -",
                    organismo="ENTIDAD PÚBLICA EMPRESARIAL RED.ES",
                    fecha="21/09/2026",
                    importe="3000",
                ),
            ])

            informe = procesar_exportaciones_csv(csv_cdti, csv_redes, salida)

            cdti = informe["convocatorias"][CODIGO_BDNS_CDTI]
            redes = informe["convocatorias"][CODIGO_BDNS_RED_ES]
            self.assertEqual(cdti["concesiones_recuperadas"], 2)
            self.assertEqual(cdti["duplicados_detectados"], 1)
            self.assertEqual(cdti["filas_ignoradas_por_otro_codigo_bdns"], 1)
            self.assertEqual(redes["registros_con_cif"], 1)
            self.assertEqual(redes["registros_sin_cif"], 1)
            self.assertEqual(redes["beneficiarios_identificados"], 2)
            self.assertEqual(informe["total_concesiones_consolidadas"], 4)

            with (salida / "concesiones_cdti_834244.csv").open(
                encoding="utf-8-sig", newline=""
            ) as archivo:
                concesiones_cdti = list(csv.DictReader(archivo))
            self.assertEqual(
                [concesion["id_concesion"] for concesion in concesiones_cdti],
                ["SB100", "SB101"],
            )
            self.assertEqual(concesiones_cdti[0]["cif"], "B39827654")
            self.assertEqual(concesiones_cdti[0]["empresa"], "EMPRESA PRUEBA SL")
            self.assertEqual(concesiones_cdti[0]["fecha_concesion"], "2026-10-07")
            self.assertEqual(concesiones_cdti[0]["subvencion"], "1250.75")
            self.assertEqual(concesiones_cdti[0]["provincia"], "")
            self.assertEqual(concesiones_cdti[0]["comunidad_autonoma"], "")
            originales_cdti = json.loads(concesiones_cdti[0]["datos_originales_json"])
            self.assertEqual(originales_cdti["Código de concesión"], "SB100")
            self.assertEqual(originales_cdti["Código BDNS"], CODIGO_BDNS_CDTI)
            self.assertEqual(
                list(concesiones_cdti[0]),
                INFORMES_CSV_FIELDS,
            )

            with (salida / "concesiones_red_es_848058.csv").open(
                encoding="utf-8-sig", newline=""
            ) as archivo:
                concesiones_redes = list(csv.DictReader(archivo))
            self.assertEqual(concesiones_redes[1]["cif"], "")
            self.assertEqual(
                concesiones_redes[1]["empresa"],
                "MORALES BURRUEZO JOSE IGNACIO - -",
            )
            originales_redes = json.loads(concesiones_redes[1]["datos_originales_json"])
            self.assertEqual(
                originales_redes["Beneficiario"],
                "***7436** MORALES BURRUEZO JOSE IGNACIO - -",
            )

            with (salida / "informe_extraccion.json").open(encoding="utf-8") as archivo:
                informe_guardado = json.load(archivo)
            self.assertEqual(
                informe_guardado["duplicados_detectados_total"],
                1,
            )

    def test_informa_fechas_importes_e_identificadores_invalidos(self):
        with tempfile.TemporaryDirectory() as directorio:
            raiz = Path(directorio)
            csv_cdti = raiz / "cdti.csv"
            csv_redes = raiz / "redes.csv"
            _escribir_csv_bdns(csv_cdti, [
                _fila_bdns(
                    CODIGO_BDNS_CDTI,
                    "",
                    "BENEFICIARIO SIN CIF",
                    importe="importe desconocido",
                    fecha="fecha inválida",
                ),
            ])
            _escribir_csv_bdns(csv_redes, [])

            informe = procesar_exportaciones_csv(
                csv_cdti,
                csv_redes,
                raiz / "salida",
            )

            errores = informe["posibles_errores_extraccion"]
            tipos_error = {error["tipo"] for error in errores}
            self.assertIn("fecha_invalida", tipos_error)
            self.assertIn("importe_invalido", tipos_error)
            self.assertIn("id_concesion_vacio", tipos_error)

    def test_falla_con_cabeceras_requeridas_ausentes(self):
        with tempfile.TemporaryDirectory() as directorio:
            raiz = Path(directorio)
            csv_cdti = raiz / "cdti.csv"
            csv_redes = raiz / "redes.csv"
            csv_cdti.write_text("columna\nvalor\n", encoding="utf-8")
            _escribir_csv_bdns(csv_redes, [])

            with self.assertRaisesRegex(RuntimeError, "columnas requeridas"):
                procesar_exportaciones_csv(csv_cdti, csv_redes, raiz / "salida")


if __name__ == "__main__":
    unittest.main()