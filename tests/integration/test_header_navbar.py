from __future__ import annotations

from flask.testing import FlaskClient


def test_navbar_shows_avatar_next_to_username_and_logout(auth_client: FlaskClient) -> None:
    response = auth_client.get("/")

    assert response.status_code == 200
    html = response.get_data(as_text=True)

    # Avatar com iniciais (2 primeiras letras do username, maiúsculas) e
    # hover mostrando o username completo via title — coexistindo com o
    # botão Sair, não substituindo-o.
    assert 'class="avatar"' in html
    assert 'title="tester"' in html
    assert ">TE<" in html
    assert 'class="nav-username"' in html
    assert 'class="logout-form"' in html


def test_logout_button_uses_primary_button_style(auth_client: FlaskClient) -> None:
    response = auth_client.get("/")

    assert response.status_code == 200
    html = response.get_data(as_text=True)

    logout_start = html.index('class="logout-form"')
    logout_end = html.index("</form>", logout_start)
    logout_block = html[logout_start:logout_end]

    # Mesmo layout visual do botão "Nova posição" (.button.button-primary),
    # não o estilo neutro/discreto anterior.
    assert 'class="button button-primary"' in logout_block
