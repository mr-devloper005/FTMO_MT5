Run these in order.

1. On the Windows VM — open MetaTrader 5 and log in to your account, then in a terminal inside the VM:

cd C:\path\to\FTMO_MT5
python mt5_api_server.py

(or double-click start_trading.bat in that folder — it does the same). Leave it running; it serves on port 8500.

2. On the Mac — first stop the hub I started earlier so the ports are free:

cd ~/Downloads/FTMO_MT5 && ./start.sh --stop

3. Start everything on the Mac (port forwarder + broker hub):

cd ~/Downloads/FTMO_MT5 && ./start.sh

4. Open the dashboard:

open http://localhost:8600

MT5 should now show connected in the top bar and prices should stream.

To stop it all later:

cd ~/Downloads/FTMO_MT5 && ./start.sh --stop

Don't use ./start.sh --bot for now — that starts the autonomous trading bot, which has never completed a successful trade cycle.