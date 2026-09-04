"""Coleta de cotações: provedores, laço, agendamento e persistência.

Reúne o que fala com a fonte de cotações e escreve o resultado -- provedores
RTD (Excel e COM direto), o laço compartilhado pelos dois destinos, a trava de
instância única, o agendamento e o agente remoto. É a fronteira que a Fase 4
desenhou: a aplicação web consome este pacote, não é dona dele.

Não reexporta nada de propósito. `database.py` importa `app.routes.helpers` e
`app/__init__.py` importa `heartbeat` -- um `__init__` que puxasse os
submódulos fecharia esse ciclo na importação do pacote.
"""
