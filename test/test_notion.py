import unittest
from unittest.mock import Mock, patch

from captador_bbdd.notion import ClienteNotion


class TestClienteNotion(unittest.TestCase):

    @patch("captador_bbdd.notion.time.sleep")
    @patch("captador_bbdd.notion.requests.Session")
    def test_authentication_and_web_property_match_database_schema(
        self, mock_session, _mock_sleep
    ):
        session = Mock()
        response = Mock(ok=True)
        response.json.return_value = {}
        session.post.return_value = response
        mock_session.return_value = session

        cliente = ClienteNotion(token="test-token", database_id="database-id")
        cliente.crear_subvencion({
            "empresa": "Empresa de prueba",
            "municipio": "Sevilla",
            "web": "https://example.com",
        })

        headers = session.headers.update.call_args.args[0]
        self.assertEqual(headers["Authorization"], "Bearer test-token")

        payload = session.post.call_args.kwargs["json"]
        self.assertIn("web", payload["properties"])
        self.assertNotIn("Web", payload["properties"])
        self.assertIsNone(payload["properties"]["Comunidad Autónoma"]["select"])
        self.assertIsNone(payload["properties"]["Presencia en Andalucía"]["select"])
        self.assertEqual(
            payload["properties"]["Ciudad"]["rich_text"][0]["text"]["content"],
            "Sevilla",
        )


if __name__ == "__main__":
    unittest.main()
