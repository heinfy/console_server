"""在临时项目中验证真实 shell 入口、dotenv 隔离和传给 Uvicorn 的配置。"""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


PROJECT_ROOT = Path(__file__).resolve().parent.parent


class StartupFixture(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="console startup ")
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        (self.root / "scripts").mkdir()
        (self.root / "src").mkdir()
        environment_module = self.root / "src/console_server/core/environment.py"
        environment_module.parent.mkdir(parents=True)
        shutil.copy2(PROJECT_ROOT / "src/console_server/core/environment.py", environment_module)
        for filename in ("scripts/start.sh", "scripts/start.py", "docker-compose.deploy.yaml"):
            shutil.copy2(PROJECT_ROOT / filename, self.root / filename)
        # 替代服务器，观察经过全部启动入口后实际收到的参数与环境。
        uvicorn = self.root / "probe" / "uvicorn"
        uvicorn.mkdir(parents=True)
        (uvicorn / "__init__.py").write_text("", encoding="utf-8")
        (uvicorn / "__main__.py").write_text(
            "import json, os, sys\n"
            "keys = ['ENV', 'DEBUG', 'DATABASE_URL', 'SECRET_KEY', 'CORS_ALLOW_ORIGIN', "
            "'COOKIE_SECURE', 'LITERAL', 'UVICORN_RELOAD', 'UVICORN_ENV_FILE']\n"
            "print(json.dumps({'argv': sys.argv[1:], 'cwd': os.getcwd(), "
            "'env': {key: os.environ.get(key) for key in keys}}))\n",
            encoding="utf-8",
        )
        self.environment = {
            "PATH": os.environ.get("PATH", ""),
            "PYTHON_BIN": sys.executable,
            "PYTHONPATH": str(self.root / "probe"),
        }

    def write_env(self, filename, content):
        path = self.root / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path

    def launch(self, *args, env=None):
        return subprocess.run(
            ["bash", str(self.root / "scripts/start.sh"), *args],
            env={**self.environment, **(env or {})},
            cwd=self.root.parent,
            text=True,
            capture_output=True,
            timeout=10,
        )

    def probe(self, *args, env=None):
        result = self.launch(*args, env=env)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout.splitlines()[-1])


class StartupTests(StartupFixture):
    def test_development_uses_legacy_dotenv_before_start(self):
        self.write_env(".env", "ENV=local\nDATABASE_URL=legacy-db\nDEBUG=false\nCOOKIE_SECURE=false\n")
        result = self.probe("dev", env={"ENV": "prod"})
        self.assertEqual(result["env"]["ENV"], "dev")
        self.assertEqual(result["env"]["DATABASE_URL"], "legacy-db")
        self.assertEqual(result["env"]["DEBUG"], "false")
        self.assertEqual(result["env"]["COOKIE_SECURE"], "false")
        self.assertIn("--reload", result["argv"])
        self.assertEqual(result["cwd"], str(self.root.resolve()))

    def test_development_file_replaces_legacy_file(self):
        self.write_env(".env", "SECRET_KEY=must-not-leak\nDATABASE_URL=legacy-db\n")
        self.write_env(".env.dev", "DATABASE_URL=dev-db\n")
        result = self.probe("dev")
        self.assertEqual(result["env"]["DATABASE_URL"], "dev-db")
        self.assertIsNone(result["env"]["SECRET_KEY"])
        self.assertEqual(result["env"]["DEBUG"], "true")

    def test_production_is_default_and_ignores_local_dotenv(self):
        self.write_env(".env", "DEBUG=true\nCOOKIE_SECURE=false\nDATABASE_URL=local-db\n")
        result = self.probe(env={"DATABASE_URL": "prod-db", "SECRET_KEY": "prod-secret"})
        self.assertEqual(result["env"]["ENV"], "prod")
        self.assertEqual(result["env"]["DEBUG"], "false")
        self.assertEqual(result["env"]["COOKIE_SECURE"], "true")
        self.assertNotIn("--reload", result["argv"])
        self.assertEqual(result["argv"][-2:], ["--workers", "1"])

    def test_staging_alias_loads_pre_file_only(self):
        self.write_env(".env.prod", "DATABASE_URL=prod-db\nSECRET_KEY=prod-secret\n")
        self.write_env(".env.pre", "ENV=prod\nDATABASE_URL=pre-db\nSECRET_KEY=pre-secret\n")
        result = self.probe(env={"ENV": "staging"})
        self.assertEqual(result["env"]["ENV"], "pre")
        self.assertEqual(result["env"]["DATABASE_URL"], "pre-db")
        self.assertEqual(result["env"]["SECRET_KEY"], "pre-secret")
        self.assertEqual(result["env"]["DEBUG"], "false")

    def test_custom_environment_and_platform_variable_priority(self):
        self.write_env(".env.uat", "DB_HOST=file-host\nDATABASE_URL=postgresql://${DB_HOST}/${ENV}\n"
                       "SECRET_KEY=file-secret\nPORT=8000\nWORKERS=2\n")
        result = self.probe("uat", env={"ENV": "prod", "DB_HOST": "platform-host", "PORT": "8080",
                                       "SECRET_KEY": "platform-secret"})
        self.assertEqual(result["env"]["DATABASE_URL"], "postgresql://platform-host/uat")
        self.assertEqual(result["env"]["SECRET_KEY"], "platform-secret")
        self.assertEqual(result["argv"][result["argv"].index("--port") + 1], "8080")
        self.assertEqual(result["argv"][-2:], ["--workers", "2"])

    def test_explicit_file_wins_over_env_file_variable(self):
        self.write_env("config/pre config.env", "DATABASE_URL=explicit-db\nSECRET_KEY=explicit-secret\n")
        self.write_env(".env.pre", "DATABASE_URL=automatic-db\nSECRET_KEY=automatic-secret\n")
        result = self.probe("pre", "--env-file", "config/pre config.env", env={"ENV_FILE": "missing.env"})
        self.assertEqual(result["env"]["DATABASE_URL"], "explicit-db")

    def test_absolute_env_file_variable(self):
        path = self.write_env("custom.env", "DATABASE_URL=external-db\nSECRET_KEY=external-secret\n")
        result = self.probe("pre", env={"ENV_FILE": str(path)})
        self.assertEqual(result["env"]["DATABASE_URL"], "external-db")

    def test_explicit_missing_file_fails(self):
        result = self.launch("dev", "--env-file", "missing.env")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("配置文件不存在", result.stderr)

    def test_deployment_requires_explicit_credentials(self):
        self.write_env(".env", "DATABASE_URL=local-db\nSECRET_KEY=local-secret\n")
        for environment in ("prod", "pre", "test", "uat"):
            with self.subTest(environment=environment):
                result = self.launch(environment)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("DATABASE_URL, SECRET_KEY", result.stderr)
                self.assertNotIn("local-secret", result.stderr)

    def test_invalid_options_fail_before_server_starts(self):
        for values, message in (
            ({"PORT": "0"}, "PORT"),
            ({"PORT": "65536"}, "PORT"),
            ({"WORKERS": "invalid"}, "WORKERS"),
            ({"DEBUG": "invalid"}, "DEBUG"),
            ({"WORKERS": "2"}, "热更新不能与多进程"),
        ):
            with self.subTest(values=values):
                result = self.launch("dev", env=values)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(message, result.stderr)

    def test_deployment_rejects_reload(self):
        result = self.launch("pre", env={"RELOAD": "true"})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("热更新仅支持 dev", result.stderr)

    def test_uvicorn_environment_cannot_enable_reload_or_second_file(self):
        result = self.probe("prod", env={"DATABASE_URL": "prod-db", "SECRET_KEY": "prod-secret",
                                         "UVICORN_RELOAD": "true", "UVICORN_ENV_FILE": ".env"})
        self.assertIsNone(result["env"]["UVICORN_RELOAD"])
        self.assertIsNone(result["env"]["UVICORN_ENV_FILE"])

    def test_dotenv_is_data_and_does_not_execute_shell(self):
        marker = self.root / "executed"
        literal = f"$(touch '{marker}')"
        self.write_env(".env.dev", f'LITERAL="{literal}"\n')
        result = self.probe("dev")
        self.assertEqual(result["env"]["LITERAL"], literal)
        self.assertFalse(marker.exists())

    def test_dry_run_hides_credentials_and_does_not_start_server(self):
        result = self.launch("prod", "--dry-run", env={"DATABASE_URL": "private-db", "SECRET_KEY": "private-key"})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--workers 1", result.stdout)
        self.assertNotIn("private-db", result.stdout)
        self.assertNotIn("private-key", result.stdout)
        self.assertNotIn('"argv"', result.stdout)

    def test_environment_name_cannot_escape_project(self):
        result = self.launch("../prod")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("环境名必须", result.stderr)


@unittest.skipUnless(shutil.which("docker"), "未安装 Docker CLI")
class DockerStartupTests(StartupFixture):
    def compose_config(self, *args, env=None):
        result = self.launch(*args, "--docker", "config", "--format", "json", env=env)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout.split("\n", 1)[1])

    def test_file_port_controls_server_and_default_published_port(self):
        self.write_env(".env.pre", "ENV=prod\nDATABASE_URL=pre-db\nSECRET_KEY=pre-secret\nPORT=8080\n")
        config = self.compose_config("staging")
        backend = config["services"]["backend"]
        self.assertEqual(config["name"], "console-pre")
        self.assertEqual(backend["environment"]["ENV"], "pre")
        self.assertEqual(backend["environment"]["PORT"], "8080")
        self.assertEqual(backend["ports"][0]["target"], 8080)
        self.assertEqual(backend["ports"][0]["published"], "8080")
        self.assertEqual(backend["ports"][0]["host_ip"], "127.0.0.1")

    def test_host_port_changes_only_published_port(self):
        self.write_env(".env.pre", "DATABASE_URL=pre-db\nSECRET_KEY=pre-secret\nPORT=8080\n")
        backend = self.compose_config("pre", env={"HOST_PORT": "8001"})["services"]["backend"]
        self.assertEqual(backend["environment"]["PORT"], "8080")
        self.assertEqual(backend["ports"][0]["target"], 8080)
        self.assertEqual(backend["ports"][0]["published"], "8001")

    def test_system_values_override_file_in_compose(self):
        self.write_env(".env.pre", "DATABASE_URL=file-db\nSECRET_KEY=file-secret\nPORT=8000\nDEBUG=true\n")
        backend = self.compose_config("pre", env={"PORT": "8080", "DATABASE_URL": "platform-db",
                                                "SECRET_KEY": "platform-secret", "DEBUG": "false"})["services"]["backend"]
        self.assertEqual(backend["environment"]["DATABASE_URL"], "platform-db")
        self.assertEqual(backend["environment"]["SECRET_KEY"], "platform-secret")
        self.assertEqual(backend["environment"]["DEBUG"], "false")
        self.assertEqual(backend["ports"][0]["target"], 8080)

    def test_environment_only_compose_never_loads_development_file(self):
        self.write_env(".env", "DATABASE_URL=local-db\nSECRET_KEY=local-secret\nPORT=9999\nDEBUG=true\n")
        backend = self.compose_config("prod", env={"DATABASE_URL": "platform-db",
                                                 "SECRET_KEY": "platform-secret"})["services"]["backend"]
        self.assertEqual(backend["environment"]["DATABASE_URL"], "platform-db")
        self.assertEqual(backend["environment"]["PORT"], "8000")
        self.assertEqual(backend["environment"]["DEBUG"], "false")
        self.assertEqual(backend["environment"]["COOKIE_SECURE"], "true")

    def test_compose_uses_explicit_file_and_preserves_json_and_dollar_values(self):
        self.write_env(".env.pre", "DATABASE_URL=automatic-db\nSECRET_KEY=automatic-secret\n")
        path = self.write_env("config/pre config.env", "DATABASE_URL=external-db\nSECRET_KEY='literal-$key-secret'\n"
                              "PORT=8081\nAUTH_ALLOWED_ORIGINS='[\"https://pre.example.com\"]'\n")
        config = self.compose_config("pre", "--env-file", str(path), env={"ENV_FILE": "missing.env"})
        backend = config["services"]["backend"]
        self.assertEqual(backend["environment"]["DATABASE_URL"], "external-db")
        # Compose 输出的配置可再次作为 YAML 使用，因此将字面量 $ 转义为 $$。
        self.assertEqual(backend["environment"]["SECRET_KEY"], "literal-$$key-secret")
        self.assertEqual(backend["environment"]["AUTH_ALLOWED_ORIGINS"], '["https://pre.example.com"]')
        self.assertEqual(backend["ports"][0]["target"], 8081)

    def test_docker_dry_run_uses_default_up_without_exposing_credentials(self):
        result = self.launch("pre", "--dry-run", "--docker", env={"DATABASE_URL": "private-db", "SECRET_KEY": "private-key"})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("docker compose", result.stdout)
        self.assertIn("-p console-pre", result.stdout)
        self.assertIn("up -d", result.stdout)
        self.assertNotIn("private-db", result.stdout)
        self.assertNotIn("private-key", result.stdout)


if __name__ == "__main__":
    unittest.main()
