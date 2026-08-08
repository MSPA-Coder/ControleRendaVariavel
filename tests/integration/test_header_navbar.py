from __future__ import annotations

import pytest
from flask.testing import FlaskClient

pytestmark = [pytest.mark.smoke]


def test_navbar_renders_avatar_and_logout_button(auth_client: FlaskClient) -> None:
    response = auth_client.get("/")

    assert response.status_code == 200
    html = response.get_data(as_text=True)

    # Avatar com as iniciais (2 primeiras letras do username, maiúsculas); o
    # nome por extenso aparece só no title e no aria-label, que é o que
    # mantém o avatar legível para leitores de tela.
    assert 'class="avatar"' in html
    assert 'title="tester"' in html
    assert ">TE<" in html
    assert 'aria-label="Usuário tester"' in html

    logout_start = html.index('class="logout-form"')
    logout_block = html[logout_start : html.index("</form>", logout_start)]
    assert 'class="button button-primary"' in logout_block
    # Toda escrita originada no navegador leva CSRF (ver AGENTS.md).
    assert 'name="csrf_token"' in logout_block
