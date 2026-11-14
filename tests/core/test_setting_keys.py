import re
from pathlib import Path

from app.core import setting_keys
from app.services.scheduler import SchedulerService
from app.services.settings_service import SettingsService

TEMPLATES_DIR = Path(__file__).resolve().parents[2] / "app" / "templates"
# Keys templates read or compare, e.g. get_system_setting("ui.pagination_mode") or setting.key === 'general.app_name'
TEMPLATE_KEY_PATTERNS = (
    re.compile(r"""get_system_setting\(\s*["']([^"']+)["']"""),
    re.compile(r"""setting\.key\s*===?\s*["']([^"']+)["']"""),
)


def _constants() -> dict[str, str]:
    return {name: value for name, value in vars(setting_keys).items() if name.isupper()}


def _default_keys() -> list[str]:
    return [default["key"] for default in SettingsService.DEFAULTS]


def test_every_default_has_a_constant_and_every_constant_has_a_default():
    # A constant whose value has a typo would otherwise read as a missing
    # setting and quietly fall back to its default.
    assert sorted(_constants().values()) == sorted(_default_keys())


def test_default_keys_are_unique():
    keys = _default_keys()
    assert len(keys) == len(set(keys))


def test_constant_names_follow_their_keys():
    for name, key in _constants().items():
        assert name == key.upper().replace(".", "_"), name


def test_scheduled_tasks_have_interval_settings():
    keys = set(_default_keys())
    for job_id in SchedulerService._TASK_REGISTRY:
        key = setting_keys.task_interval(job_id)
        assert key in keys
        assert setting_keys.is_task_interval(key)
    assert not setting_keys.is_task_interval(setting_keys.JOBS_RETENTION_DAYS)


def test_template_setting_keys_exist():
    keys = set(_default_keys())
    found: dict[str, set[str]] = {}
    for path in TEMPLATES_DIR.rglob("*.html"):
        text = path.read_text(encoding="utf-8")
        for pattern in TEMPLATE_KEY_PATTERNS:
            for key in pattern.findall(text):
                found.setdefault(key, set()).add(path.relative_to(TEMPLATES_DIR).as_posix())

    assert found, "expected templates to read some settings"
    unknown = {key: sorted(paths) for key, paths in found.items() if key not in keys}
    assert unknown == {}
