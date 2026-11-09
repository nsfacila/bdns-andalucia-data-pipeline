import csv
import os
import tempfile
import unittest
from unittest.mock import Mock, patch

from captador_bbdd import main


class TestPipeline(unittest.TestCase):

    def test_same_company_can_have_distinct_concession_keys(self):
        first = {"cif": "A12345678", "id_concesion": "grant-1"}
        second = {"cif": "A12345678", "id_concesion": "grant-2"}

        self.assertNotEqual(main._clave_concesion(first), main._clave_concesion(second))

    def test_empty_existing_csv_gets_header(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "registro_historico.csv")
            with open(path, "w", encoding="utf-8"):
                pass

            old_path = main.PATH_CSV_HISTORICO
            main.PATH_CSV_HISTORICO = path
            try:
                with patch("captador_bbdd.main.os.makedirs"):
                    main.inicializar_csv_historico()

                with open(path, newline="", encoding="utf-8") as csv_file:
                    header = next(csv.reader(csv_file))
                self.assertEqual(header, main.CAMPOS_CSV)
            finally:
                main.PATH_CSV_HISTORICO = old_path

    def test_existing_historical_csv_is_migrated_without_losing_rows(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "registro_historico.csv")
            with open(path, "w", newline="", encoding="utf-8") as csv_file:
                writer = csv.DictWriter(
                    csv_file,
                    fieldnames=["id_concesion", "cif", "estado_evaluacion"],
                )
                writer.writeheader()
                writer.writerow({
                    "id_concesion": "SB-legacy",
                    "cif": "B18866749",
                    "estado_evaluacion": "pendiente_enriquecimiento",
                })

            old_path = main.PATH_CSV_HISTORICO
            old_fields = main.CAMPOS_CSV[:]
            main.PATH_CSV_HISTORICO = path
            try:
                main.inicializar_csv_historico()
                with open(path, newline="", encoding="utf-8-sig") as csv_file:
                    lector = csv.DictReader(csv_file)
                    fila = next(lector)
                    self.assertIn("estado_verificacion", lector.fieldnames)
                self.assertEqual(fila["id_concesion"], "SB-legacy")
                self.assertEqual(fila["estado_evaluacion"], "pendiente_enriquecimiento")
                self.assertEqual(fila["estado_verificacion"], "")
            finally:
                main.PATH_CSV_HISTORICO = old_path
                main.CAMPOS_CSV[:] = old_fields

    def test_missing_company_location_is_pending_not_discarded(self):
        concession = {
            "id_concesion": "SB-1",
            "empresa": "Empresa sin ubicación",
            "cif": "B29012345",
            "organismo": "CDTI",
            "convocatoria": "Misiones Ciencia e Innovación",
            "fecha_concesion": "2025-12-29",
            "provincia": "",
            "comunidad_autonoma": "",
            "subvencion": 1000,
        }

        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "registro_historico.csv")
            old_path = main.PATH_CSV_HISTORICO
            main.PATH_CSV_HISTORICO = path
            try:
                with patch(
                    "captador_bbdd.main.obtener_todas_las_concesiones",
                    return_value=[concession],
                ):
                    main.ejecutar_pipeline(dry_run=True)

                with open(path, newline="", encoding="utf-8") as csv_file:
                    row = next(csv.DictReader(csv_file))
                self.assertEqual(
                    row["estado_evaluacion"],
                    "pendiente_enriquecimiento",
                )
                self.assertEqual(row["estado_verificacion"], "PENDIENTE")
            finally:
                main.PATH_CSV_HISTORICO = old_path

    def test_only_verified_andalusian_concessions_are_sent_to_notion(self):
        concession = {
            "id_concesion": "SB-verified",
            "cif": "B18866749",
            "empresa": "Nombre BDNS",
            "empresa_registral": "Nombre Registral SL",
            "organismo": "CDTI",
            "convocatoria": "Misiones Ciencia e Innovación",
            "fecha_concesion": "2025-12-29",
            "provincia": "Sevilla",
            "comunidad_autonoma": "Andalucía",
            "estado_verificacion": "VERIFICADO",
            "presencia_andalucia": "Si",
            "subvencion": 1000,
        }
        cliente = Mock()
        cliente.leer_subvenciones_existentes.return_value = []

        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "registro_historico.csv")
            old_path = main.PATH_CSV_HISTORICO
            main.PATH_CSV_HISTORICO = path
            try:
                with (
                    patch.dict(os.environ, {
                        "NOTION_TOKEN": "test-token",
                        "NOTION_DATABASE_ID": "test-database",
                    }),
                    patch(
                        "captador_bbdd.main.ClienteNotion",
                        return_value=cliente,
                    ),
                    patch(
                        "captador_bbdd.main.obtener_todas_las_concesiones",
                        return_value=[concession],
                    ),
                    patch(
                        "captador_bbdd.main.enriquecer_concesiones",
                        return_value=[concession],
                    ),
                ):
                    main.ejecutar_pipeline(dry_run=False)
            finally:
                main.PATH_CSV_HISTORICO = old_path

        cliente.crear_subvencion.assert_called_once_with(concession)


if __name__ == "__main__":
    unittest.main()
