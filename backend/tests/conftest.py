import os
from pathlib import Path

os.environ["DATABASE_URL"] = "sqlite:///./data/test_agent.db"
# Functional tests run with console auth disabled; a dedicated test mutates the
# token to verify enforcement (see test_admin_auth_guards_management_api).
os.environ["ADMIN_TOKEN"] = ""
# Open device registration in tests (no provision key) — else the generated
# per-install key would 401 the register calls.
os.environ["PROVISION_KEY"] = ""
# Null the DeepSeek key so tests never hit the real API (and the engine reads as
# unconfigured); the engine pipeline test monkeypatches the client directly.
os.environ["DEEPSEEK_API_KEY"] = ""
# Disable the global publish-concurrency cap in tests — the suite shares one DB,
# so other tests' leftover active tasks would trip the cap and starve the
# auto-publish functional tests. (The cap is a production scale knob.)
os.environ["MAX_CONCURRENT_PUBLISHES"] = "0"
Path("./data/test_agent.db").unlink(missing_ok=True)
