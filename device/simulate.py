# -*- coding: utf-8 -*-
"""
Run the Alpaca dome server against a simulated K8055.

    python simulate.py

Useful for developing Alpaca clients, for trying ASCOM Conform Universal, and
for anyone who needs the HTTP surface without a board wired up. The shells
actually travel, so opens and closes take about as long as the real thing.

NEVER point this at the observatory: it drives nothing, so a client that
believes it is closing the dome would be wrong.
"""
from board_sim import install

BOARD = install(animate=True)

import app  # noqa: E402  (must come after install())

if __name__ == '__main__':
    print('Greenhill dome Alpaca server -- SIMULATED HARDWARE, no board attached')
    app.main()
