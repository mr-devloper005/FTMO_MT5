"""Paths, pilot universe and source settings. No secrets live here."""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Optional, Tuple

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parent
STATE = ROOT / "state"                       # gitignored: raw downloads, rag.db, eval runs

PILOT = ("MU", "SNDK", "TSLA", "GS", "MSFT")

# How much history the POC keeps per company (most recent first).
DEFAULT_LIMITS: Dict[str, int] = {"10-K": 2, "10-Q": 4, "8-K": 6}

# SEC fair access: max 10 requests/second (sec.gov/os/accessing-edgar-data). We stay well under.
SEC_MAX_RPS = 4.0

EMBED_MODEL = "BAAI/bge-small-en-v1.5"       # 384-dim, local CPU (fastembed / ONNX), no API key
QUERY_PREFIX = "Represent this sentence for searching relevant passages: "   # bge retrieval prefix
ANSWER_MODEL = "sonnet"                      # Claude CLI alias (same as research_agent)

NOT_FOUND = "Not found in the available data."
NOT_FOUND_LEGACY = "Not found in the available documents."   # wording before FMP data was added

_UA = re.compile(r"\S.*\s+\S+@\S+\.\S+")     # "Company or Name  contact@domain.tld"


class MissingCredential(RuntimeError):
    """A source needs a value only the operator can provide."""


def _dotenv(name: str) -> Optional[str]:
    env = REPO / ".env"
    if not env.exists():
        return None
    for line in env.read_text(encoding="utf-8", errors="ignore").splitlines():
        k, _, v = line.partition("=")
        if k.strip() == name and v.strip():
            return v.strip().strip('"').strip("'")
    return None


def sec_user_agent() -> str:
    """SEC requires a declared User-Agent: '<company or name> <contact email>'. Never guessed."""
    ua = os.environ.get("SEC_USER_AGENT") or _dotenv("SEC_USER_AGENT")
    if not ua:
        raise MissingCredential(
            "SEC_USER_AGENT is not set. The SEC requires every automated client to declare who it is.\n"
            "Set it once (shell or the FTMO .env), e.g.:\n"
            '  export SEC_USER_AGENT="Your Company Name you@yourcompany.com"')
    if not _UA.fullmatch(ua.strip()):
        raise MissingCredential(f"SEC_USER_AGENT must look like 'Company Name contact@domain.com' (got {ua!r})")
    return ua.strip()


def _setting(name: str, default: Optional[str] = None) -> Optional[str]:
    v = os.environ.get(name)
    return v if v not in (None, "") else (_dotenv(name) or default)


def fmp_api_key() -> str:
    """FMP key from FMP_API_KEY (shell or the FTMO .env). Never logged, never put in URLs."""
    key = _setting("FMP_API_KEY")
    if not key:
        raise MissingCredential("FMP_API_KEY is not set. Add it to the FTMO .env (gitignored) or the shell.")
    return key.strip()


@dataclass(frozen=True)
class PgConfig:
    """PostgreSQL connection, from POSTGRES_HOST / _PORT / _DB / _USER / _PASSWORD (shell or the
    FTMO .env). Only host and port have defaults; no credential is ever defaulted."""
    host: str
    port: int
    dbname: str
    user: str
    password: Optional[str] = None

    @classmethod
    def from_env(cls) -> "PgConfig":
        missing = [n for n in ("POSTGRES_DB", "POSTGRES_USER") if not _setting(n)]
        if missing:
            raise MissingCredential(
                f"{', '.join(missing)} not set. The RAG store is PostgreSQL + pgvector; set POSTGRES_HOST, "
                "POSTGRES_PORT, POSTGRES_DB, POSTGRES_USER and POSTGRES_PASSWORD (shell or the FTMO .env). "
                "See rag/README.md.")
        return cls(host=_setting("POSTGRES_HOST", "localhost"), port=int(_setting("POSTGRES_PORT", "5432")),
                   dbname=_setting("POSTGRES_DB"), user=_setting("POSTGRES_USER"),
                   password=_setting("POSTGRES_PASSWORD"))

    @classmethod
    def test_from_env(cls) -> "PgConfig":
        """Tests use POSTGRES_TEST_DB (default '<POSTGRES_DB>_test') and refuse any name not ending in _test."""
        base = cls.from_env()
        name = _setting("POSTGRES_TEST_DB") or f"{base.dbname}_test"
        if not name.endswith("_test"):
            raise MissingCredential(f"refusing to run tests against {name!r}: test database must end in _test")
        return cls(base.host, base.port, name, base.user, base.password)

    def kwargs(self) -> Dict[str, object]:
        kw: Dict[str, object] = {"host": self.host, "port": self.port, "dbname": self.dbname, "user": self.user,
                                 "connect_timeout": 5, "application_name": "ftmo-rag"}
        if self.password:
            kw["password"] = self.password
        return kw

    def __repr__(self) -> str:                                   # never print the password
        return f"PgConfig({self.user}@{self.host}:{self.port}/{self.dbname})"


@dataclass
class Settings:
    state_dir: Path = STATE
    limits: Dict[str, int] = field(default_factory=lambda: dict(DEFAULT_LIMITS))
    pg: Optional[PgConfig] = None                # None = read POSTGRES_* from the environment / .env

    @property
    def raw_dir(self) -> Path:
        return self.state_dir / "raw"

    @property
    def sqlite_path(self) -> Path:
        """The retired SQLite store (source of ``python -m rag migrate``). Never written any more."""
        return self.state_dir / "rag.db"

    @property
    def migrations_dir(self) -> Path:
        return self.state_dir / "migrations"

    def pg_config(self) -> PgConfig:
        return self.pg or PgConfig.from_env()

    @property
    def eval_dir(self) -> Path:
        return self.state_dir / "eval"

    def ensure_dirs(self) -> None:
        for d in (self.state_dir, self.raw_dir, self.eval_dir, self.migrations_dir):
            d.mkdir(parents=True, exist_ok=True)


def period_label(fy: Optional[int], fq: Optional[str]) -> str:
    if not fy:
        return "period unknown"
    return f"FY{fy}" if fq in (None, "FY") else f"{fq} FY{fy}"


def parse_period(text: str) -> Tuple[Optional[int], Optional[str]]:
    """Fiscal period named in a question: 'Q3 FY2026', 'Q3 2026', 'fiscal 2025', 'FY2025'.
    Returns (fiscal_year, 'Q1'..'Q4' | 'FY' | None). Calendar dates are not guessed."""
    t = text.upper()
    year = lambda y: int(y) + (2000 if len(y) == 2 else 0)            # noqa: E731
    for pat, order in ((r"\bQ([1-4])\s*(?:OF\s+)?(?:FISCAL\s+(?:YEAR\s+)?)?(?:FY\s*)?(20\d\d)\b", "qy"),
                       (r"\bQ([1-4])\s*(?:FY\s*'?|')(\d\d)\b", "qy"),          # Q3 FY26 / Q3 '26
                       (r"\b(?:FY\s*)?(20\d\d)\s*Q([1-4])\b", "yq")):
        m = re.search(pat, t)
        if m:
            q, y = m.groups() if order == "qy" else m.groups()[::-1]
            return year(y), f"Q{q}"
    m = re.search(r"\b(?:FISCAL\s+(?:YEAR\s+)?(20\d\d)|FY\s*'?(20\d\d|\d\d))\b", t)
    if m:
        return year(m.group(1) or m.group(2)), "FY"
    return None, None
