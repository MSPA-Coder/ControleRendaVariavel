from __future__ import annotations

from flask.testing import FlaskClient


def test_tables_keep_catalogs_in_an_exclusive_native_accordion(
    auth_client: FlaskClient,
) -> None:
    response = auth_client.get("/tables")

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    for section in ("brokers", "tickers", "expirations", "option-contracts"):
        assert f'id="{section}" name="reference-table"' in html


def test_broker_save_returns_to_its_catalog_section(auth_client: FlaskClient) -> None:
    response = auth_client.post(
        "/tables/brokers",
        data={"name": "Genial", "acronym": "GE"},
    )

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/tables#brokers")
