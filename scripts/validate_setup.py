#!/usr/bin/env python3
"""
FTMO MT5 Setup Validator
Validates configuration, credentials, and system readiness.
Run this BEFORE starting the trading system to catch issues early.

Usage:
    python scripts/validate_setup.py
"""
import os
import sys
import yaml
from pathlib import Path
from decimal import Decimal, getcontext

# Set Decimal precision per PRD
getcontext().prec = 10


def load_env():
    """Load .env file into environment."""
    env_path = Path(__file__).parent.parent / ".env"
    if not env_path.exists():
        return False
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, value = line.partition("=")
                os.environ.setdefault(key.strip(), value.strip())
    return True


def validate_env():
    """Check all required environment variables."""
    print("\n[1/6] Checking Credentials (.env)")
    required = ["MT5_LOGIN", "MT5_SERVER", "MT5_PASSWORD"]
    all_ok = True
    for var in required:
        val = os.environ.get(var, "")
        if not val or val.startswith("<"):
            print(f"  FAIL: {var} is not set or still has placeholder")
            all_ok = False
        else:
            masked = val[:2] + "*" * (len(val) - 2) if var == "MT5_PASSWORD" else val
            print(f"  OK:   {var} = {masked}")
    return all_ok


def validate_config():
    """Check configuration file."""
    print("\n[2/6] Checking Configuration")
    config_path = os.environ.get("FTMO_CONFIG_PATH", "config/default.yaml")
    if not os.path.exists(config_path):
        print(f"  FAIL: Config file not found: {config_path}")
        return False

    with open(config_path) as f:
        cfg = yaml.safe_load(f)

    ftmo = cfg.get("ftmo", {})
    capital = Decimal(str(ftmo.get("initial_capital", 0)))
    mdl_pct = Decimal(str(ftmo.get("mdl_pct", 0)))
    ml_pct = Decimal(str(ftmo.get("ml_pct", 0)))
    challenge = ftmo.get("challenge_type", "UNKNOWN")

    print(f"  Challenge:       {challenge}")
    print(f"  Capital:         ${capital:,.2f}")
    print(f"  Daily Loss Max:  ${capital * mdl_pct / 100:,.2f} ({mdl_pct}%)")
    print(f"  Overall Max:     ${capital * ml_pct / 100:,.2f} ({ml_pct}%)")
    print(f"  Floor Equity:    ${capital - capital * ml_pct / 100:,.2f}")

    if challenge not in ("ONE_STEP", "TWO_STEP"):
        print(f"  WARN: Unknown challenge type: {challenge}")

    symbols = cfg.get("mt5", {}).get("symbols", [])
    if not symbols:
        print("  FAIL: No trading symbols configured")
        return False
    print(f"  Symbols:         {', '.join(symbols)}")
    print(f"  OK:   Configuration valid")
    return True


def validate_structure():
    """Check all required files exist."""
    print("\n[3/6] Checking Project Structure")
    required_files = [
        "src/nehanth/__init__.py",
        "src/nehanth/main.py",
        "src/nehanth/core/models.py",
        "src/nehanth/core/constants.py",
        "src/nehanth/core/exceptions.py",
        "src/nehanth/engine/rule_engine.py",
        "src/nehanth/execution/mt5_gateway.py",
        "src/nehanth/execution/mt5_executor.py",
        "src/nehanth/execution/reconciliation.py",
        "src/nehanth/agents/coordinator.py",
        "src/nehanth/agents/risk_agent.py",
        "src/nehanth/agents/signal_agent.py",
        "src/nehanth/agents/execution_agent.py",
        "src/nehanth/store/event_store.py",
        "src/nehanth/supervisor/watchdog.py",
        "src/nehanth/observability/telemetry.py",
        "src/nehanth/observability/health.py",
        "config/default.yaml",
    ]
    all_ok = True
    for f in required_files:
        if os.path.exists(f):
            print(f"  OK:   {f}")
        else:
            print(f"  FAIL: {f} missing")
            all_ok = False
    return all_ok


def validate_syntax():
    """Compile-check all Python files."""
    print("\n[4/6] Syntax Validation")
    import py_compile
    errors = []
    count = 0
    for root, dirs, files in os.walk("src/nehanth"):
        if "__pycache__" in root:
            continue
        for f in sorted(files):
            if f.endswith(".py"):
                count += 1
                path = os.path.join(root, f)
                try:
                    py_compile.compile(path, doraise=True)
                except py_compile.PyCompileError as e:
                    errors.append(path)
                    print(f"  FAIL: {path}")
    if errors:
        print(f"  {len(errors)}/{count} files have syntax errors")
        return False
    print(f"  OK:   {count}/{count} files compile cleanly")
    return True


def validate_dependencies():
    """Check required Python packages."""
    print("\n[5/6] Checking Dependencies")
    packages = {
        "pydantic": "pydantic",
        "structlog": "structlog",
        "yaml": "pyyaml",
        "numpy": "numpy",
        "scipy": "scipy",
        "pandas": "pandas",
        "sklearn": "scikit-learn",
    }
    all_ok = True
    for module, pip_name in packages.items():
        try:
            __import__(module)
            print(f"  OK:   {pip_name}")
        except ImportError:
            print(f"  MISS: {pip_name}  -->  pip install {pip_name}")
            all_ok = False

    # Check MT5 (Windows only)
    try:
        import MetaTrader5
        print(f"  OK:   MetaTrader5")
    except ImportError:
        if sys.platform == "win32":
            print(f"  MISS: MetaTrader5  -->  pip install MetaTrader5")
            all_ok = False
        else:
            print(f"  SKIP: MetaTrader5 (requires Windows, using mock)")
    return all_ok


def validate_mt5_connection():
    """Try connecting to MT5 terminal."""
    print("\n[6/6] MT5 Connection Test")
    if sys.platform != "win32":
        print("  SKIP: Not on Windows - cannot test MT5 connection")
        print("  INFO: Deploy to Windows with MT5 terminal for live testing")
        return True

    try:
        import MetaTrader5 as mt5

        login = int(os.environ.get("MT5_LOGIN", "0"))
        server = os.environ.get("MT5_SERVER", "")
        password = os.environ.get("MT5_PASSWORD", "")

        if not mt5.initialize():
            print(f"  FAIL: MT5 terminal not running or not found")
            return False

        authorized = mt5.login(login, password=password, server=server)
        if not authorized:
            error = mt5.last_error()
            print(f"  FAIL: Login failed - {error}")
            mt5.shutdown()
            return False

        info = mt5.account_info()
        print(f"  OK:   Connected to {server}")
        print(f"  OK:   Account #{login}")
        print(f"  OK:   Balance: ${info.balance:,.2f}")
        print(f"  OK:   Equity:  ${info.equity:,.2f}")
        print(f"  OK:   Leverage: 1:{info.leverage}")

        mt5.shutdown()
        return True

    except Exception as e:
        print(f"  FAIL: {e}")
        return False


def main():
    print("=" * 60)
    print("  FTMO MT5 SETUP VALIDATOR")
    print("=" * 60)

    os.chdir(Path(__file__).parent.parent)

    env_ok = load_env()
    results = {
        "Credentials": validate_env(),
        "Configuration": validate_config(),
        "Project Files": validate_structure(),
        "Syntax": validate_syntax(),
        "Dependencies": validate_dependencies(),
        "MT5 Connection": validate_mt5_connection(),
    }

    print("\n" + "=" * 60)
    print("  RESULTS SUMMARY")
    print("=" * 60)
    all_pass = True
    for name, passed in results.items():
        status = "PASS" if passed else "FAIL"
        icon = "+" if passed else "X"
        print(f"  [{icon}] {name}: {status}")
        if not passed:
            all_pass = False

    if all_pass:
        print("\n  ALL CHECKS PASSED - System ready to run!")
        print("\n  Next steps:")
        print("    python scripts/paper_trade.py --duration 1h    # Paper trade test")
        print("    python -m nehanth.main                         # Start system")
    else:
        print("\n  SOME CHECKS FAILED - Fix issues above before running")
        print("  Missing packages? Run: pip install -e '.[dev]'")

    print("=" * 60)
    return 0 if all_pass else 1


if __name__ == "__main__":
    sys.exit(main())
