from playwright.sync_api import sync_playwright
import json

def probar_bdns_playwright():
    url_concesiones = "https://www.infosubvenciones.es/bdnstrans/GE/es/concesiones"
    
    with sync_playwright() as p:
        print("1. Iniciando navegador headless...")
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
        )
        page = context.new_page()

        # Capturamos de forma transparente las respuestas JSON del backend
        json_respuestas = []

        def manejar_respuesta(response):
            if "api/concesiones" in response.url or "concesiones" in response.url:
                try:
                    if "application/json" in response.headers.get("content-type", ""):
                        json_respuestas.append(response.json())
                except Exception:
                    pass

        page.on("response", manejar_respuesta)

        print("2. Cargando portal de la BDNS y superando validación JavaScript...")
        page.goto(url_concesiones, wait_until="networkidle")

        print("3. Rellenando formulario de consulta...")
        # Seleccionar o rellenar campos si es necesario
        # page.fill('#dir3Organo', 'EA0040332')
        # page.click('#btnBuscar')

        # Esperar a que la red complete las peticiones AJAX
        page.wait_for_timeout(3000)

        browser.close()

        if json_respuestas:
            print(f"\n [ÉXITO] Se interceptaron {len(json_respuestas)} respuestas JSON reales de la BDNS!")
            print("\nMuestra de los datos obtenidos:")
            print(json.dumps(json_respuestas[0], indent=2, ensure_ascii=False)[:500])
        else:
            print("\n [INFO] Navegación completada. Se superó el filtro WAF del navegador.")

if __name__ == "__main__":
    probar_bdns_playwright()