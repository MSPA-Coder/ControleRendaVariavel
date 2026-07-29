import pytest

from app import create_app


@pytest.mark.parametrize(
    "template_name",
    ["base.html", "index.html", "options.html", "tables.html"],
)
def test_main_templates_compile(template_name: str) -> None:
    app = create_app({"TESTING": True})

    app.jinja_env.get_template(template_name)
