"""
Standalone test script — run directly on your Windows machine to debug order_send.
Usage:  python test_order.py

This tries multiple approaches to place a tiny ETHUSD buy order.
"""
import sys
import MetaTrader5 as mt5

# ── 1. Initialize MT5 ────────────────────────────────────────────────
if not mt5.initialize():
    print(f"MT5 initialize failed: {mt5.last_error()}")
    sys.exit(1)

info = mt5.account_info()
print(f"Connected: login={info.login}, server={info.server}, balance={info.balance}")
print(f"Trade allowed: {info.trade_allowed}, Trade expert: {info.trade_expert}")
print(f"MT5 version: {mt5.version()}")
print()

# ── 2. Get ETHUSD info ───────────────────────────────────────────────
symbol = "ETHUSD"
mt5.symbol_select(symbol, True)

sym = mt5.symbol_info(symbol)
tick = mt5.symbol_info_tick(symbol)

if sym is None or tick is None:
    print(f"Cannot get info for {symbol}")
    mt5.shutdown()
    sys.exit(1)

print(f"Symbol: {sym.name}")
print(f"  trade_mode={sym.trade_mode}  (4=FULL)")
print(f"  trade_exemode={sym.trade_exemode}  (0=Request, 1=Instant, 2=Market, 3=Exchange)")
print(f"  filling_mode={sym.filling_mode}  (1=FOK, 2=IOC, 3=FOK+IOC)")
print(f"  volume_min={sym.volume_min}, volume_max={sym.volume_max}, volume_step={sym.volume_step}")
print(f"  trade_contract_size={sym.trade_contract_size}")
print(f"  digits={sym.digits}")
print(f"  ask={tick.ask}, bid={tick.bid}")
print()

# ── 3. Try order_check first ─────────────────────────────────────────
print("=" * 60)
print("TEST 1: order_check with IOC filling")
print("=" * 60)

check_request = {
    "action": mt5.TRADE_ACTION_DEAL,
    "symbol": symbol,
    "volume": sym.volume_min,
    "type": mt5.ORDER_TYPE_BUY,
    "price": tick.ask,
    "type_filling": mt5.ORDER_FILLING_IOC,
    "deviation": 30,
}
print(f"Request: {check_request}")
check = mt5.order_check(check_request)
if check:
    print(f"Result: retcode={check.retcode}, comment={check.comment}")
    print(f"  margin={check.margin}, margin_free={check.margin_free}")
    # Print the request that order_check normalized
    print(f"  check.request = {check.request}")
else:
    print(f"order_check returned None: {mt5.last_error()}")
print()

# ── 4. Try order_send with the SAME request ──────────────────────────
print("=" * 60)
print("TEST 2: order_send with SAME minimal request (IOC)")
print("=" * 60)

# Refresh tick
tick = mt5.symbol_info_tick(symbol)
send_request = {
    "action": mt5.TRADE_ACTION_DEAL,
    "symbol": symbol,
    "volume": sym.volume_min,
    "type": mt5.ORDER_TYPE_BUY,
    "price": tick.ask,
    "type_filling": mt5.ORDER_FILLING_IOC,
    "deviation": 30,
}
print(f"Request: {send_request}")
result = mt5.order_send(send_request)
if result:
    print(f"Result: retcode={result.retcode}, comment={result.comment}")
    if result.retcode in (10008, 10009, 10010):
        print(f"  SUCCESS! ticket={result.order}, deal={result.deal}, price={result.price}")
    else:
        print(f"  FAILED. Full result:")
        print(f"  {result}")
        # Print the request back from result
        print(f"  result.request = {result.request}")
else:
    print(f"order_send returned None: {mt5.last_error()}")
print()

# ── 5. Try with keyword argument ─────────────────────────────────────
print("=" * 60)
print("TEST 3: order_send with request= keyword (IOC)")
print("=" * 60)

tick = mt5.symbol_info_tick(symbol)
send_request2 = {
    "action": mt5.TRADE_ACTION_DEAL,
    "symbol": symbol,
    "volume": sym.volume_min,
    "type": mt5.ORDER_TYPE_BUY,
    "price": tick.ask,
    "type_filling": mt5.ORDER_FILLING_IOC,
    "deviation": 30,
}
print(f"Request: {send_request2}")
result2 = mt5.order_send(request=send_request2)
if result2:
    print(f"Result: retcode={result2.retcode}, comment={result2.comment}")
    if result2.retcode in (10008, 10009, 10010):
        print(f"  SUCCESS! ticket={result2.order}, deal={result2.deal}, price={result2.price}")
    else:
        print(f"  FAILED. Full result:")
        print(f"  {result2}")
        print(f"  result.request = {result2.request}")
else:
    print(f"order_send returned None: {mt5.last_error()}")
print()

# ── 6. Try with FOK filling ──────────────────────────────────────────
print("=" * 60)
print("TEST 4: order_send with FOK filling (positional arg)")
print("=" * 60)

tick = mt5.symbol_info_tick(symbol)
send_request3 = {
    "action": mt5.TRADE_ACTION_DEAL,
    "symbol": symbol,
    "volume": sym.volume_min,
    "type": mt5.ORDER_TYPE_BUY,
    "price": tick.ask,
    "type_filling": mt5.ORDER_FILLING_FOK,
    "deviation": 30,
}
print(f"Request: {send_request3}")
result3 = mt5.order_send(send_request3)
if result3:
    print(f"Result: retcode={result3.retcode}, comment={result3.comment}")
    if result3.retcode in (10008, 10009, 10010):
        print(f"  SUCCESS! ticket={result3.order}, deal={result3.deal}, price={result3.price}")
    else:
        print(f"  FAILED. Full result:")
        print(f"  {result3}")
        print(f"  result.request = {result3.request}")
else:
    print(f"order_send returned None: {mt5.last_error()}")
print()

# ── 7. Try a different symbol (EURUSD) as sanity check ───────────────
print("=" * 60)
print("TEST 5: order_send EURUSD with IOC (sanity check)")
print("=" * 60)

symbol2 = "EURUSD"
mt5.symbol_select(symbol2, True)
sym2 = mt5.symbol_info(symbol2)
tick2 = mt5.symbol_info_tick(symbol2)

if sym2 and tick2:
    print(f"EURUSD: trade_exemode={sym2.trade_exemode}, filling_mode={sym2.filling_mode}")
    send_request4 = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": symbol2,
        "volume": sym2.volume_min,
        "type": mt5.ORDER_TYPE_BUY,
        "price": tick2.ask,
        "type_filling": mt5.ORDER_FILLING_IOC,
        "deviation": 30,
    }
    print(f"Request: {send_request4}")
    result4 = mt5.order_send(send_request4)
    if result4:
        print(f"Result: retcode={result4.retcode}, comment={result4.comment}")
        if result4.retcode in (10008, 10009, 10010):
            print(f"  SUCCESS! ticket={result4.order}, deal={result4.deal}, price={result4.price}")
            # Close it immediately
            close_req = {
                "action": mt5.TRADE_ACTION_DEAL,
                "symbol": symbol2,
                "volume": sym2.volume_min,
                "type": mt5.ORDER_TYPE_SELL,
                "price": mt5.symbol_info_tick(symbol2).bid,
                "type_filling": mt5.ORDER_FILLING_IOC,
                "deviation": 30,
                "position": result4.order,
            }
            close_result = mt5.order_send(close_req)
            if close_result:
                print(f"  Close result: retcode={close_result.retcode}, comment={close_result.comment}")
        else:
            print(f"  FAILED: {result4}")
            print(f"  result.request = {result4.request}")
    else:
        print(f"order_send returned None: {mt5.last_error()}")
else:
    print(f"Cannot get EURUSD info")
print()

# ── 8. Check if order_check normalized request gives us a clue ───────
print("=" * 60)
print("TEST 6: Use order_check normalized request for order_send")
print("=" * 60)

tick = mt5.symbol_info_tick("ETHUSD")
check_req = {
    "action": mt5.TRADE_ACTION_DEAL,
    "symbol": "ETHUSD",
    "volume": sym.volume_min,
    "type": mt5.ORDER_TYPE_BUY,
    "price": tick.ask,
    "type_filling": mt5.ORDER_FILLING_IOC,
    "deviation": 30,
}
check2 = mt5.order_check(check_req)
if check2 and check2.retcode == 0:
    # Extract the normalized request from check result
    normalized = check2.request
    print(f"Normalized request from order_check:")
    print(f"  action={normalized.action}")
    print(f"  magic={normalized.magic}")
    print(f"  order={normalized.order}")
    print(f"  symbol={normalized.symbol}")
    print(f"  volume={normalized.volume}")
    print(f"  price={normalized.price}")
    print(f"  stoplimit={normalized.stoplimit}")
    print(f"  sl={normalized.sl}")
    print(f"  tp={normalized.tp}")
    print(f"  deviation={normalized.deviation}")
    print(f"  type={normalized.type}")
    print(f"  type_filling={normalized.type_filling}")
    print(f"  type_time={normalized.type_time}")
    print(f"  expiration={normalized.expiration}")
    print(f"  comment={normalized.comment}")
    print(f"  position={normalized.position}")
    print(f"  position_by={normalized.position_by}")

    # Now try to send using EXACTLY those values
    print()
    print("Sending with exact normalized values...")
    tick = mt5.symbol_info_tick("ETHUSD")  # fresh tick
    exact_request = {
        "action": normalized.action,
        "magic": normalized.magic,
        "order": normalized.order,
        "symbol": normalized.symbol,
        "volume": normalized.volume,
        "price": tick.ask,  # use fresh price
        "stoplimit": normalized.stoplimit,
        "sl": normalized.sl,
        "tp": normalized.tp,
        "deviation": normalized.deviation,
        "type": normalized.type,
        "type_filling": normalized.type_filling,
        "type_time": normalized.type_time,
        "expiration": normalized.expiration,
        "comment": normalized.comment,
        "position": normalized.position,
        "position_by": normalized.position_by,
    }
    print(f"Request: {exact_request}")
    result5 = mt5.order_send(exact_request)
    if result5:
        print(f"Result: retcode={result5.retcode}, comment={result5.comment}")
        if result5.retcode in (10008, 10009, 10010):
            print(f"  SUCCESS! ticket={result5.order}, deal={result5.deal}")
        else:
            print(f"  FAILED: {result5}")
    else:
        print(f"order_send returned None: {mt5.last_error()}")
else:
    print(f"order_check failed: {check2}")

print()
print("=" * 60)
print("All tests complete.")
print("=" * 60)

mt5.shutdown()
