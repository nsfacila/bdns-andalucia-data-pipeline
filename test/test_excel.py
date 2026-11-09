import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from captador_bbdd.filtros import evaluar_subvencion

def probar_filtro_con_excel():
    import pandas as pd

    excel_path = "BD_Empresas_Andalucía.xlsx"
    df = pd.read_excel(excel_path)
    
    aceptadas = 0
    descartadas = 0

    print("--- EVALUANDO EXCEL DE MUESTRA ---")
    for idx, fila in df.iterrows():
        # Mapeo del Excel al formato del captador
        concesion = {
            "empresa": fila.get("Empresa"),
            "cif": fila.get("CIF"),
            "comunidad_autonoma": fila.get("CCAA"),
            "provincia": fila.get("Provincia"),
            "ciudad": fila.get("Ciudad"),
            "convocatoria": fila.get("Programa de la Subvención"),
            "proyecto": fila.get("Proyecto"),
            "fecha_concesion": str(fila.get("Año convocatoria", "2024")),
            "presupuesto": fila.get("Presupuesto (€)", 0),
            "subvencion": fila.get("Subvención (€)", 0)
        }

        dictamen = evaluar_subvencion(concesion)
        if dictamen["estado"] == "aceptada":
            aceptadas += 1
            print(f" [ACEPTADA] {concesion['empresa']} ({concesion['comunidad_autonoma']}) -> {concesion['convocatoria']}")
        else:
            descartadas += 1
            print(f" [DESCARTADA] {concesion['empresa']} -> Motivo: {dictamen['motivo']}")

    print(f"\nResumen: {aceptadas} Aceptadas | {descartadas} Descartadas")

if __name__ == "__main__":
    probar_filtro_con_excel()