"""앱 설정. 값은 모두 app/.env에서 읽고, 코드·로그에 키를 남기지 않는다."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
ROOT_DIR = APP_DIR.parent

try:  # python-dotenv가 없으면 환경변수만 사용한다
    from dotenv import load_dotenv

    load_dotenv(APP_DIR / ".env")
except ModuleNotFoundError:  # pragma: no cover - 테스트 환경
    pass


def _int(name: str, default: int) -> int:
    raw = os.getenv(name)
    try:
        return int(raw) if raw not in (None, "") else default
    except ValueError:
        return default


@dataclass(frozen=True)
class Station:
    """관측소 한 곳. air=에어코리아 측정소명, weather=기상청 격자."""

    key: str
    name: str
    air_station: str | None = None
    nx: int | None = None
    ny: int | None = None
    role: str = "target"  # target=우리 동네, upwind=바람이 불어오는 쪽


# v0.1 대상: 순천(예측 지점) + 광양·여수(상류 참고)
STATIONS: tuple[Station, ...] = (
    Station("suncheon", "순천 연향동", air_station="연향동", nx=70, ny=70, role="target"),
    Station("gwangyang", "광양 태인동", air_station="태인동", nx=73, ny=70, role="upwind"),
    Station("yeosu", "여수 여천동", air_station="여천동", nx=73, ny=66, role="upwind"),
)


@dataclass(frozen=True)
class Settings:
    internal_host: str = "127.0.0.1"
    internal_port: int = 8000
    service_key: str = ""           # 공공데이터포털 서비스 키 (기상청·에어코리아 공용)
    db_path: Path = ROOT_DIR / "data" / "wave.db"
    collect_interval_minutes: int = 30
    request_timeout_sec: float = 10.0
    request_retries: int = 2
    llm_provider: str = "none"
    llm_model: str = ""
    llm_api_key: str = ""
    llm_timeout_sec: float = 4.0
    stations: tuple[Station, ...] = field(default_factory=lambda: STATIONS)

    @property
    def has_service_key(self) -> bool:
        return bool(self.service_key.strip())


def _db_path() -> Path:
    """DB_PATH가 상대 경로면 프로젝트 루트 기준으로 읽는다.

    Supervisor가 작업 디렉터리를 `app/`으로 잡기 때문에, 그냥 두면 배포 서버에서만
    `app/data/`에 DB가 생겨 로컬과 위치가 달라진다(AGENTS.md는 `data/`로 정해 둠).
    """
    raw = Path(os.getenv("DB_PATH", "") or (ROOT_DIR / "data" / "wave.db"))
    return raw if raw.is_absolute() else ROOT_DIR / raw


def load_settings() -> Settings:
    return Settings(
        internal_host=os.getenv("INTERNAL_HOST", "127.0.0.1"),
        internal_port=_int("INTERNAL_PORT", 8000),
        service_key=os.getenv("DATA_GO_KR_SERVICE_KEY", ""),
        db_path=_db_path(),
        collect_interval_minutes=_int("COLLECT_INTERVAL_MINUTES", 30),
        request_timeout_sec=float(os.getenv("REQUEST_TIMEOUT_SEC", "10")),
        request_retries=_int("REQUEST_RETRIES", 2),
        llm_provider=os.getenv("LLM_PROVIDER", "none").strip().lower(),
        llm_model=os.getenv("LLM_MODEL", "").strip(),
        llm_api_key=os.getenv("LLM_API_KEY", ""),
        llm_timeout_sec=float(os.getenv("LLM_TIMEOUT_SEC", "4")),
    )
